"""Local mirrors of batch controls, kept separately from archive data."""
import json

from .errors import BackupError, ForeignStateError
from .filesystems import supports_posix_metadata
from .inventory import safe_relative
from .local_paths import reject_symlinks
from .names import STATE_FILE
from .state import atomic_json, identity, load_state, validate_controls


def control_directory(config, job):
    return (config.metadata_dir / job.name if config.metadata_dir else
            config.state_dir / job.name / "controls")


def member_metadata(control):
    return {item["path"]: (item["size"], item["mtime_ns"]) for item in control["files"]}


def write_control(config, job, key, control):
    root = control_directory(config, job)
    atomic_json(root / (key + '.json'), control, preserve_permissions=supports_posix_metadata(root))


def read_control(path, key):
    reject_symlinks(path, label="Controle de lote", show_path=True)
    try:
        control = json.loads(path.read_text(encoding="utf-8"))
        validate_controls({key: control}, True)
        return control
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise BackupError(f"Controle de lote inválido: {path}: {exc}") from exc


def cached_control(config, job, key, expected, *, write_missing=True):
    root = control_directory(config, job)
    path = root / (key + ".json")
    reject_symlinks(path, label="Controle de lote", show_path=True)
    if not path.exists():
        # The committed local index already contains the exact control. This
        # also seeds a newly configured mirror without remote daily reads.
        if write_missing:
            write_control(config, job, key, expected)
        return expected
    control = read_control(path, key)
    if (member_metadata(control) != member_metadata(expected) or
            control.get("archive_size") != expected.get("archive_size")):
        raise BackupError(f"Controle local diverge do índice: {path}")
    return control


def write_manifest(config, job, state):
    root = control_directory(config, job)
    path = root / '.index.json'
    reject_symlinks(path, label='Índice de metadados', show_path=True)
    data = {'schema': 1, 'identity': identity(job), 'active': sorted(state['active'])}
    if path.exists():
        try:
            previous = json.loads(path.read_text(encoding='utf-8'))
            if previous.get('identity') != data['identity']:
                raise ForeignStateError(f'Cache de metadados pertence a outro job: {path}')
            if previous == data:
                return
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            raise BackupError(f'Índice de metadados inválido: {path}: {exc}') from exc
    atomic_json(path, data, preserve_permissions=supports_posix_metadata(root))


def prepare_controls(config, job, state, *, write_missing=True):
    for key, control in {**state.get('excluded', {}), **state['active']}.items():
        cached_control(config, job, key, control, write_missing=write_missing)
    old_root = config.state_dir / job.name / 'controls'
    root = control_directory(config, job)
    if write_missing and root != old_root:
        for entry in state.get('retired', []):
            if entry.get('control'):
                target = root / entry['control']
                previous = old_root / entry['control']
                if not target.exists() and previous.exists():
                    write_control(config, job, entry['key'], read_control(previous, entry['key']))
    if write_missing:
        write_manifest(config, job, state)


def local_state(config, job):
    path = config.state_dir / job.name / STATE_FILE
    if path.exists() or path.is_symlink():
        return load_state(path, identity(job))
    manifest = control_directory(config, job) / '.index.json'
    if manifest.exists() or manifest.is_symlink():
        reject_symlinks(manifest, label='Índice de metadados', show_path=True)
        try:
            data = json.loads(manifest.read_text(encoding='utf-8'))
            if data.get('schema') != 1 or data.get('identity') != identity(job) or not isinstance(data.get('active'), list):
                raise ValueError('identidade ou estrutura')
            active = {safe_relative(key): read_control(control_directory(config, job) / (key + '.json'), key)
                      for key in data['active']}
            validate_controls(active, True)
            return {'schema': 1, 'identity': identity(job), 'active': active, 'retired': []}
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise BackupError(f'Índice de metadados inválido: {manifest}: {exc}') from exc
    return load_state(path, identity(job))
