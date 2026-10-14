"""Read a validated local index or a bounded remote recovery member."""
import json
import tarfile

from .errors import BackupError
from .state import atomic_json, identity, load_state


def read_index(config, job, storage, work, *, metadata_only=False):
    path = config.state_dir / job.name / "state.json"
    if path.exists() or path.is_symlink():
        return load_state(path, identity(job))
    if metadata_only:
        raise BackupError("Verificação S3 exige o índice local; nenhum objeto será baixado")
    if job.logic != "batch":
        raise BackupError("Restauração e verificação da cópia simples exigem o índice local")
    archive = work / "control.tar.gz"
    storage.get(".sambinha/control.tar.gz", archive)
    # Read only this bounded regular member; never extract arbitrary tar paths.
    with tarfile.open(archive) as tar:
        entries = [m for m in tar.getmembers() if m.name == "state.json"]
        if len(entries) != 1 or not entries[0].isreg() or entries[0].size > 64 * 1024 * 1024:
            raise BackupError("Índice de recuperação inválido")
        state = json.load(tar.extractfile(entries[0]))
    recovered = work / "state.json"
    atomic_json(recovered, state)
    return load_state(recovered, identity(job))
