"""Daily source checks, metadata replica checks and manual archive integrity tests."""
from dataclasses import replace
import json
from pathlib import Path
import tempfile

from .archive_recovery import check_archive
from .batches import plan
from .errors import BackupError
from .filtering import Selection
from .inventory import scan
from .metadata import cached_control, control_directory, local_state, member_metadata, prepare_controls, read_control
from .mounts import check_mounts
from .recovery_index import read_index
from .state import indexed_files
from .storage import Storage


def verify_local_metadata_job(config, job, runner, log, state=None, *, progress=None):
    """Compare source size/time with committed local controls; never read a remote."""
    if job.logic == "simple":
        Storage(job, config, runner).check_simple()
        log.event("verification_complete", job=job.name, mode="rclone_check")
        return {"mode": "rclone_check"}
    check_mounts(replace(job, destination_mount=None), runner)
    state = local_state(config, job) if state is None else state
    prepare_controls(config, job, state, write_missing=not config.dry_run)
    controls = {key: cached_control(config, job, key, value, write_missing=not config.dry_run)
                for key, value in state['active'].items()}
    previous = {path: metadata for control in controls.values() for path, metadata in member_metadata(control).items()}
    selection = Selection(job.exclude_files, config, runner, ignored_roots=(job.source,))
    selected_files, selected_dirs = selection.source(job.source)
    files = scan(job.source, selected_files, selected_dirs, mode="simple", progress=progress)
    current = {item.path: (item.size, item.mtime_ns) for item in files}
    included = selection.include_paths(previous)
    added = current.keys() - included
    removed = included - current.keys()
    modified = {path for path in current.keys() & included if current[path] != previous[path]}
    excluded = previous.keys() - included
    changes = removed | modified | excluded
    affected = [key for key, control in controls.items()
                if (any(item['path'] in changes for item in control['files']) or
                    not key.endswith('.' + job.archive_format))]
    reused_paths = {item['path'] for key, control in controls.items() if key not in affected
                    for item in control['files']}
    pending = plan([item for item in files if item.path not in reused_paths], job.max_bytes, extension=job.archive_format, individual_percent=job.individual_batch_percent)
    for key in affected:
        log.event('batch_update_needed', job=job.name, archive=key)
    result = {'mode': 'source_metadata', 'files': len(files), 'bytes': sum(item.size for item in files),
              'new_files': len(added), 'modified_files': len(modified), 'removed_files': len(removed),
              'excluded_files': len(excluded), 'unchanged_files': len(current) - len(added) - len(modified),
              'batches_to_update': len(affected), 'new_batches': len(pending),
              'needs_update': bool(added or removed or modified or excluded or affected)}
    log.event('verification_complete', job=job.name, **result)
    return result


def _reference(config, job, storage, work, state):
    if state is not None:
        return state
    local = local_state(config, job)
    if ((config.state_dir / job.name / 'state.json').exists() or
            (control_directory(config, job) / '.index.json').exists()):
        return local
    return read_index(config, job, storage, work)


def verify_destination_metadata_job(config, job, runner, log, state=None, *, progress=None):
    """Compare local and destination JSON controls without downloading archives."""
    if job.logic != 'batch':
        raise BackupError('verify-destination-metadata exige logic=batch')
    storage = Storage(job, config, runner)
    with tempfile.TemporaryDirectory(prefix='sambinha-metadata-') as temp:
        work = Path(temp)
        storage.temporary_directory = work
        state = _reference(config, job, storage, work, state)
        prepare_controls(config, job, state, write_missing=not config.dry_run)
        root = control_directory(config, job)
        controls = {key: cached_control(config, job, key, value, write_missing=not config.dry_run)
                    for key, value in {**state.get('excluded', {}), **state['active']}.items()}
        for entry in state.get('retired', []):
            if entry.get('control'):
                controls[entry['key']] = read_control(root / entry['control'], entry['key'])
        for index, (key, expected) in enumerate(controls.items()):
            if 'archive_size' not in expected:
                raise BackupError(f'Lote antigo sem tamanho de envio no índice: {key}')
            if storage.stat(key, hashes=False)['Size'] != expected['archive_size']:
                raise BackupError(f'Tamanho do lote divergente: {key}')
            path = work / 'control.json'
            try:
                storage.get(key + '.json', path)
                remote = read_control(path, key)
                if json.dumps(remote, sort_keys=True) != json.dumps(expected, sort_keys=True):
                    raise BackupError(f'JSON local e remoto divergentes: {key}.json')
            finally:
                path.unlink(missing_ok=True)
            if progress:
                progress.update('Conferindo JSONs', job.destination, index + 1, len(controls))
            log.event('verified_metadata', job=job.name, path=key + '.json')
    result = {'mode': 'metadata_sync', 'batches': len(controls)}
    log.event('metadata_verification_complete', job=job.name, **result)
    return result


def verify_destination_integrity_job(config, job, runner, log, state=None, *, progress=None):
    """Manually test one archive at a time without writing extracted files."""
    if job.logic != 'batch':
        raise BackupError('verify-destination-integrity exige logic=batch')
    storage = Storage(job, config, runner)
    with tempfile.TemporaryDirectory(prefix='sambinha-integrity-') as temp:
        work = Path(temp)
        storage.temporary_directory = work
        state = _reference(config, job, storage, work, state)
        for index, (key, control) in enumerate(state['active'].items()):
            if job.type == 'local':
                check_archive(storage, runner, key, control)
            else:
                archive = work / 'archive'
                try:
                    storage.get(key, archive)
                    check_archive(storage, runner, key, control, archive=archive)
                finally:
                    archive.unlink(missing_ok=True)
            if progress:
                progress.update('Testando integridade', job.destination, index + 1, len(state['active']))
            log.event('tested_archive', job=job.name, path=key)
    result = {'mode': 'archive_integrity', 'batches': len(state['active']),
              'files': len(indexed_files(state, job.logic))}
    log.event('integrity_complete', job=job.name, **result)
    return result
