"""Confirmed recovery generations, safe bootstrap and publication recovery."""
import hashlib
import json
import re
import tarfile
import tempfile
import time
import uuid
from pathlib import Path

from .errors import BackupError, ForeignStateError, UnsupportedStateError
from .filesystems import supports_posix_metadata
from .inventory import safe_relative
from .local_paths import reject_symlinks
from .metadata import read_control, write_control
from .names import CONTROL, INDEXES, JOB_MARKER, STATE_FILE
from .state import (
    IDENTITY_SCHEMA,
    STATE_SCHEMA,
    atomic_json,
    identity,
    identity_matches,
    identity_options,
    load_state,
    migrate_identity,
    record_origin,
    validate_controls,
)
from .storage import Storage
from .tempdirs import prefix

PREFIX = INDEXES + '/'
GENERATION = re.compile(r'[0-9]{20}-[0-9a-f]{32}\Z')
# The state is parsed whole (about twice its size in memory, more while reading) and recovery also keeps
# every control in memory, so these bounds are sized for machines with a few GB free, not for "unlimited".
MAX_METADATA = 256 * 1024 * 1024
MAX_CONTROLS_METADATA = 512 * 1024 * 1024


def listing(storage, only=None):
    """A missing local destination is new; a missing/inaccessible S3 bucket is fatal.

    ``only`` restricts the listing to one directory prefix (rclone then skips every
    other directory instead of walking the whole destination); a missing prefix
    is simply empty. Keys stay relative to the destination.
    """
    if storage.job.type == 'local' and not Path(storage.job.destination).exists():
        return {}
    narrow = ['--include', '/' + only.strip('/') + '/**'] if only else []
    raw = storage.command('lsjson', storage.job.destination, '--recursive', '--files-only',
                          '--no-modtime', '--no-mimetype', *narrow)
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
        state = load_state(state_path, identity(job), **identity_options(job))
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
                        ('archive_size' in control and entries[lot] != control['archive_size'])):
                    raise BackupError(f'Geração referencia lote ausente/divergente: {lot}')
            return state, controls
        except (BackupError, OSError, ValueError, KeyError, TypeError, tarfile.TarError) as exc:
            # Never adopt another job's identity or follow a metadata symlink.
            if isinstance(exc, (ForeignStateError, UnsupportedStateError)) or (isinstance(exc, BackupError) and 'link simbólico' in str(exc)):
                raise
            failures.append(str(exc))
    if not candidates and not entries and allow_new:
        return load_state(work / 'empty-state.json', identity(job)), {}
    raise BackupError('Nenhuma geração confirmada recuperável no destino' +
                      (': ' + '; '.join(failures[:3]) if failures else ''))


def job_marker(job, created, generation=None):
    """Destination marker: who owns the prefix and which generation it last confirmed."""
    return {'schema': IDENTITY_SCHEMA, 'name': job.name, 'type': job.type, 'logic': job.logic,
            'created': created, 'generation': generation}


def read_marker(storage, work):
    """The destination's `.sambinha/job.json`, or None when absent/unreadable."""
    target = work / 'job.json'
    try:
        storage.stat(JOB_MARKER, hashes=False)
        storage.get(JOB_MARKER, target)
        data = json.loads(target.read_text(encoding='utf-8'))
    except (BackupError, OSError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get('name'), str) else None


def check_marker(job, storage, work, entries):
    """Refuse a destination whose marker names another job unless adoption was requested."""
    if JOB_MARKER not in entries or job.adopt_identity:
        return
    marker = read_marker(storage, work)
    if marker is not None and marker['name'] != job.name:
        raise ForeignStateError(f'Destino pertence ao job {marker["name"]!r}; '
                                'use --adopt-identity para assumir')


def superseded(job, storage, work, previous, log=None):
    """True when another machine confirmed a generation this local state does not know.

    The marker is one small object, so the check is cheap enough for every run;
    destinations without a marker (pre-0.16 or never published) are trusted.
    """
    local = previous.get('generation')
    if not isinstance(local, str):
        return False
    marker = read_marker(storage, work)
    remote = marker.get('generation') if marker and marker['name'] == job.name else None
    if not isinstance(remote, str) or not GENERATION.fullmatch(remote) or remote == local:
        return False
    if log is not None:
        log.event('remote_generation_differs', job=job.name, local=local, remote=remote)
    return True


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
            previous = load_state(path, identity(job), **identity_options(job))
        except (ForeignStateError, UnsupportedStateError):
            raise
        except BackupError:
            invalid = True
    storage = Storage(job, config, runner)
    with tempfile.TemporaryDirectory(prefix=prefix('bootstrap'), dir=config.temp_dir) as temp:
        work = Path(temp)
        if previous is not None and not journal.exists():
            if not superseded(job, storage, work, previous, log):
                # A legacy/renamed identity accepted above is rewritten in memory;
                # the next save/publish persists it and republishes the index.
                migrate_identity(previous, job, log)
                return previous
        entries = listing(storage)
        check_marker(job, storage, work, entries)
        own_initialization = False
        if initialized.exists():
            own_initialization = identity_matches(json.loads(initialized.read_text()).get('identity'),
                                                  identity(job), **identity_options(job))
        candidates = any(k == CONTROL or (k.endswith('.commit.json') and k.startswith(PREFIX))
                         for k in entries)
        if (allow_new and not invalid and not candidates and
                (not entries or own_initialization)):
            state = previous or load_state(work / 'empty-state.json', identity(job))
            migrate_identity(state, job, log)
            if not job.dry_run:
                atomic_json(initialized, {'identity': identity(job)})
            return state
        state, controls = remote_index(config, job, storage, work, entries=entries)
        state['index_pending'] = True
        migrate_identity(state, job, log)
        if not job.dry_run:
            for key, control in controls.items():
                write_control(config, job, key, control)
            for key, control in {**state.get('excluded', {}), **state['active']}.items():
                write_control(config, job, key, control)
            # Fingerprints are per machine (st_dev/st_ino): never adopt a publisher's (older
            # indexes embedded them); ours live in fingerprints.json and survive recovery.
            state.pop('fingerprints', None)
            record_origin(state, job)
            atomic_json(path, state)
            journal.unlink(missing_ok=True)
            initialized.unlink(missing_ok=True)
        if log:
            log.event('index_recovered', job=job.name, generation=state.get('generation'),
                      files=sum(len(c['files']) for c in state['active'].values()))
        return state


def publish_marker(ctx, work, generation):
    """Rewrite `.sambinha/job.json` after each confirmed generation, keeping the creation time."""
    current = read_marker(ctx.storage, work)
    created = current['created'] if (current and current['name'] == ctx.job.name
                                     and type(current.get('created')) in (int, float)) else time.time()
    marker = work / 'job-marker.json'
    atomic_json(marker, job_marker(ctx.job, created, generation))
    ctx.storage.put(marker, JOB_MARKER, standard="control")
    if current is None or current['name'] != ctx.job.name:
        ctx.event('job_marker_written', path=JOB_MARKER, previous=current)


def publish(ctx, state):
    """Immutable generation + confirmation, then compatibility index and local commit."""
    state['schema'] = STATE_SCHEMA
    state['identity'] = ctx.identity
    record_origin(state, ctx.job, ctx.now)
    previous = state.get('generation', '')
    sequence = int(previous.split('-', 1)[0]) if isinstance(previous, str) and GENERATION.fullmatch(previous) else 0
    generation = f'{max(time.time_ns(), sequence + 1):020d}-{uuid.uuid4().hex}'
    state['generation'] = generation
    journal = ctx.directory / 'publication.json'
    atomic_json(journal, {'identity': ctx.identity, 'generation': generation})
    with tempfile.TemporaryDirectory(prefix=prefix('index'), dir=ctx.config.temp_dir) as temp:
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
        recovery_state = {k: v for k, v in state.items() if k not in ('index_pending', 'fingerprints')}
        atomic_json(work / STATE_FILE, recovery_state)
        state_bytes = (work / STATE_FILE).stat().st_size
        if state_bytes > MAX_METADATA:
            raise BackupError(f'Índice excede limite de metadados (state.json): {state_bytes} bytes, limite {MAX_METADATA}')
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
                    raise BackupError(f'Controles excedem limite de metadados: {key} tem {size} bytes, soma {control_bytes}, '
                                      f'limites {MAX_METADATA} e {MAX_CONTROLS_METADATA}')
                tar.add(path, arcname='controls/' + key + '.json')
        ctx.event('index_size', state_bytes=state_bytes, controls_bytes=control_bytes, controls=len(controls),
                  archive_bytes=archive.stat().st_size, limit_state=MAX_METADATA, limit_controls=MAX_CONTROLS_METADATA)
        key = PREFIX + generation + '.tar.gz'
        ctx.storage.put(archive, key, standard="control")
        marker = work / 'commit.json'
        atomic_json(marker, {'generation': generation, 'size': archive.stat().st_size,
                             'sha256': hashlib.sha256(archive.read_bytes()).hexdigest()})
        ctx.storage.put(marker, PREFIX + generation + '.commit.json', standard="control")
        transferred = ctx.storage.put(archive, CONTROL, standard="control")
        ctx.event('transferred' if transferred else 'identical', path=CONTROL)
        publish_marker(ctx, work, generation)


def finish(ctx):
    """Called only after saving the confirmed local state."""
    # Only the index prefix is needed to prune generations; the orphan report
    # makes its own (single) full listing when it needs one.
    entries = listing(ctx.storage, INDEXES)
    generations = sorted((k[len(PREFIX):-12] for k in entries if k.startswith(PREFIX)
                          and k.endswith('.commit.json') and GENERATION.fullmatch(k[len(PREFIX):-12])), reverse=True)
    for generation in generations[ctx.job.recovery_index_versions:]:
        # Remove confirmation first; an interrupted prune never exposes a bad candidate.
        ctx.storage.delete(PREFIX + generation + '.commit.json')
        ctx.storage.delete(PREFIX + generation + '.tar.gz')
        if ctx.listing is not None:
            ctx.listing.pop(PREFIX + generation + '.commit.json', None)
            ctx.listing.pop(PREFIX + generation + '.tar.gz', None)

    (ctx.directory / 'publication.json').unlink(missing_ok=True)
    (ctx.directory / 'initialization.json').unlink(missing_ok=True)
