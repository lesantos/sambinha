"""Confirmed recovery generations, safe bootstrap and publication recovery."""
import hashlib
import json
from pathlib import Path
import re
import tarfile
import tempfile
import time
import uuid

from .errors import BackupError
from .filesystems import supports_posix_metadata
from .inventory import safe_relative
from .local_paths import reject_symlinks
from .metadata import read_control, write_control
from .names import CONTROL, INDEXES, STATE_FILE
from .state import atomic_json, identity, load_state, validate_controls
from .storage import Storage

PREFIX = INDEXES + '/'
GENERATION = re.compile(r'[0-9]{20}-[0-9a-f]{32}\Z')
MAX_METADATA = 64 * 1024 * 1024
MAX_CONTROLS_METADATA = 256 * 1024 * 1024


def listing(storage):
    """A missing local destination is new; a missing/inaccessible S3 bucket is fatal."""
    if storage.job.type == 'local' and not Path(storage.job.destination).exists():
        return {}
    raw = storage.command('lsjson', storage.job.destination, '--recursive', '--files-only',
                          '--no-modtime', '--no-mimetype')
    try:
        records = json.loads(raw)
        if not isinstance(records, list):
            raise ValueError("listagem não é uma lista")
        result = {}
        for item in records:
            key = safe_relative(item['Path'])
            if type(item['Size']) is not int or item['Size'] < 0:
                raise ValueError('tamanho inválido')
            result[key] = item['Size']
        return result
    except (ValueError, KeyError, TypeError) as exc:
        raise BackupError('Inventário de recuperação inválido') from exc


def _archive(config, job, archive, work):
    """Validate bounded JSON members without extracting paths from the tar."""
    controls = {}
    total = 0
    with tarfile.open(archive) as tar:
        entries = tar.getmembers()
        states = [m for m in entries if m.name == STATE_FILE]
        if len(states) != 1 or not states[0].isreg() or states[0].size > MAX_METADATA:
            raise BackupError('Índice de recuperação inválido')
        state_path = work / 'validated-state.json'
        atomic_json(state_path, json.load(tar.extractfile(states[0])),
                    preserve_permissions=supports_posix_metadata(work))
        state = load_state(state_path, identity(job))
        known = {**state.get('excluded', {}), **state['active']}
        retired = {entry['key'] for entry in state['retired']}
        for member in entries:
            if not member.name.startswith('controls/') or not member.name.endswith('.json'):
                continue
            key = safe_relative(member.name.removeprefix('controls/')[:-5])
            if key not in known and key not in retired:
                continue
            total += member.size
            if (not member.isreg() or member.size > MAX_METADATA or total > MAX_CONTROLS_METADATA
                    or key in controls):
                raise BackupError('Controles de recuperação inválidos')
            control = json.load(tar.extractfile(member))
            validate_controls({key: control}, True)
            if key in known and control != known[key]:
                raise BackupError('Controle diverge do índice de recuperação')
            controls[key] = control
    return state, controls


def remote_index(config, job, storage, work, *, allow_new=False, entries=None):
    entries = listing(storage) if entries is None else entries
    generations = sorted((key[len(PREFIX):-12] for key in entries
                          if key.startswith(PREFIX) and key.endswith('.commit.json')
                          and GENERATION.fullmatch(key[len(PREFIX):-12])), reverse=True)
    candidates = []
    for generation in generations:
        candidates.append((PREFIX + generation + '.tar.gz', generation))
        if CONTROL in entries:
            candidates.append((CONTROL, generation))
    if CONTROL in entries:
        candidates.append((CONTROL, None))
    failures = []
    for key, generation in candidates:
        archive = work / 'control.tar.gz'
        try:
            storage.get(key, archive)
            if generation:
                marker = work / 'commit.json'
                storage.get(PREFIX + generation + '.commit.json', marker)
                commit = json.loads(marker.read_text())
                if not isinstance(commit, dict):
                    raise BackupError('Marcador de geração inválido')
                if (commit.get('generation') != generation or commit.get('size') != archive.stat().st_size
                        or commit.get('sha256') != hashlib.sha256(archive.read_bytes()).hexdigest()):
                    raise BackupError('Geração de recuperação incompleta ou corrompida')
            state, controls = _archive(config, job, archive, work)
            if generation and state.get('generation') != generation:
                raise BackupError('Geração divergente do índice')
            for lot, control in state['active'].items():
                if (lot not in entries or lot + '.json' not in entries or
                        'archive_size' in control and entries[lot] != control['archive_size']):
                    raise BackupError(f'Geração referencia lote ausente/divergente: {lot}')
            return state, controls
        except (BackupError, OSError, ValueError, KeyError, TypeError, tarfile.TarError) as exc:
            # Never adopt another job's identity or follow a metadata symlink.
            if isinstance(exc, BackupError) and ('pertence a outra' in str(exc) or 'não suportada' in str(exc) or 'link simbólico' in str(exc)):
                raise
            failures.append(str(exc))
    if not candidates and not entries and allow_new:
        return load_state(work / 'empty-state.json', identity(job)), {}
    raise BackupError('Nenhuma geração confirmada recuperável no destino' +
                      (': ' + '; '.join(failures[:3]) if failures else ''))


def backup_state(config, job, runner, *, allow_new=True, log=None):
    """Recover before planning; never mistake an existing backup for a new one."""
    path = config.state_dir / job.name / STATE_FILE
    journal = path.parent / 'publication.json'
    initialized = path.parent / 'initialization.json'
    for candidate in (path, journal, initialized):
        reject_symlinks(candidate, label='Estado', include_parents=False)
    previous = None
    invalid = False
    if path.exists():
        try:
            previous = load_state(path, identity(job))
        except BackupError as exc:
            if 'pertence a outra' in str(exc) or 'não suportada' in str(exc):
                raise
            invalid = True
    if previous is not None and not journal.exists():
        return previous
    storage = Storage(job, config, runner)
    with tempfile.TemporaryDirectory(prefix='sambinha-bootstrap-', dir=config.temp_dir) as temp:
        work = Path(temp)
        entries = listing(storage)
        own_initialization = False
        if initialized.exists():
            own_initialization = json.loads(initialized.read_text()).get('identity') == identity(job)
        candidates = any(k == CONTROL or k.endswith('.commit.json') and k.startswith(PREFIX)
                         for k in entries)
        if (allow_new and not invalid and not candidates and
                (not entries or own_initialization)):
            state = previous or load_state(work / 'empty-state.json', identity(job))
            if not job.dry_run:
                atomic_json(initialized, {'identity': identity(job)})
            return state
        state, controls = remote_index(config, job, storage, work, entries=entries)
        state['index_pending'] = True
        if not job.dry_run:
            for key, control in controls.items():
                write_control(config, job, key, control)
            for key, control in {**state.get('excluded', {}), **state['active']}.items():
                write_control(config, job, key, control)
            atomic_json(path, state)
            journal.unlink(missing_ok=True)
            initialized.unlink(missing_ok=True)
        if log:
            log.event('index_recovered', job=job.name, generation=state.get('generation'),
                      files=sum(len(c['files']) for c in state['active'].values()))
        return state


def publish(ctx, state):
    """Immutable generation + confirmation, then compatibility index and local commit."""
    state['schema'] = 2
    previous = state.get('generation', '')
    sequence = int(previous.split('-', 1)[0]) if isinstance(previous, str) and GENERATION.fullmatch(previous) else 0
    generation = f'{max(time.time_ns(), sequence + 1):020d}-{uuid.uuid4().hex}'
    state['generation'] = generation
    journal = ctx.directory / 'publication.json'
    atomic_json(journal, {'identity': ctx.identity, 'generation': generation})
    with tempfile.TemporaryDirectory(prefix='sambinha-index-', dir=ctx.config.temp_dir) as temp:
        work = Path(temp)
        controls = {**state.get('excluded', {}), **state['active']}
        for entry in state['retired']:
            if entry.get('control'):
                path = ctx.controls_directory / entry['control']
                if not path.exists() and not path.is_symlink():
                    # An interrupted earlier cleanup (or a purged metadata_dir) can
                    # leave a retired entry without its mirror; the destination copy
                    # and the member list in the entry are enough to expire it.
                    ctx.event('retired_control_missing', path=entry['key'], control=entry.pop('control'))
                    continue
                controls[entry['key']] = read_control(path, entry['key'])
        recovery_state = {k: v for k, v in state.items() if k != 'index_pending'}
        atomic_json(work / STATE_FILE, recovery_state)
        if (work / STATE_FILE).stat().st_size > MAX_METADATA:
            raise BackupError('Índice excede limite de metadados (state.json)')
        archive = work / 'control.tar.gz'
        control_bytes = 0
        with tarfile.open(archive, 'w:gz') as tar:
            tar.add(work / STATE_FILE, arcname=STATE_FILE)
            for key, control in controls.items():
                path = work / 'control.json'
                atomic_json(path, control)
                size = path.stat().st_size
                control_bytes += size
                if size > MAX_METADATA or control_bytes > MAX_CONTROLS_METADATA:
                    raise BackupError('Controles excedem limite de metadados')
                tar.add(path, arcname='controls/' + key + '.json')
        key = PREFIX + generation + '.tar.gz'
        ctx.storage.put(archive, key, standard="control")
        marker = work / 'commit.json'
        atomic_json(marker, {'generation': generation, 'size': archive.stat().st_size,
                             'sha256': hashlib.sha256(archive.read_bytes()).hexdigest()})
        ctx.storage.put(marker, PREFIX + generation + '.commit.json', standard="control")
        transferred = ctx.storage.put(archive, CONTROL, standard="control")
        ctx.event('transferred' if transferred else 'identical', path=CONTROL)


def finish(ctx):
    """Called only after saving the confirmed local state."""
    entries = listing(ctx.storage)
    generations = sorted((k[len(PREFIX):-12] for k in entries if k.startswith(PREFIX)
                          and k.endswith('.commit.json') and GENERATION.fullmatch(k[len(PREFIX):-12])), reverse=True)
    for generation in generations[ctx.job.recovery_index_versions:]:
        # Remove confirmation first; an interrupted prune never exposes a bad candidate.
        ctx.storage.delete(PREFIX + generation + '.commit.json')
        ctx.storage.delete(PREFIX + generation + '.tar.gz')

    (ctx.directory / 'publication.json').unlink(missing_ok=True)
    (ctx.directory / 'initialization.json').unlink(missing_ok=True)
