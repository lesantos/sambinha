"""Read a validated local index or a bounded remote recovery member."""
from .errors import BackupError, ForeignStateError, UnsupportedStateError
from .local_paths import reject_symlinks
from .names import STATE_FILE
from .state import identity, load_state


def read_index(config, job, storage, work, *, metadata_only=False):
    path = config.state_dir / job.name / STATE_FILE
    reject_symlinks(path, label="Estado", include_parents=False)
    if path.exists() and not (path.parent / 'publication.json').exists():
        try:
            return load_state(path, identity(job))
        except (ForeignStateError, UnsupportedStateError):
            raise
        except BackupError:
            pass
    if metadata_only:
        raise BackupError("Verificação S3 exige o índice local; nenhum objeto será baixado")
    if job.logic != "batch":
        raise BackupError("Restauração da cópia simples não usa índice batch")
    from .index_generations import remote_index
    state, _controls = remote_index(config, job, storage, work)
    return state
