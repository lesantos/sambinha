"""Read a validated local index or a bounded remote recovery member."""
from .errors import BackupError
from .names import STATE_FILE
from .state import identity, load_state


def read_index(config, job, storage, work, *, metadata_only=False):
    path = config.state_dir / job.name / STATE_FILE
    if path.is_symlink():
        raise BackupError("Estado contém link simbólico")
    if path.exists() and not (path.parent / 'publication.json').exists():
        try:
            return load_state(path, identity(job))
        except BackupError as exc:
            if 'pertence a outra' in str(exc) or 'não suportada' in str(exc):
                raise
    if metadata_only:
        raise BackupError("Verificação S3 exige o índice local; nenhum objeto será baixado")
    if job.logic != "batch":
        raise BackupError("Restauração da cópia simples não usa índice batch")
    from .index_generations import remote_index
    state, controls = remote_index(config, job, storage, work)
    return state
