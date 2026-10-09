"""Verify expected local mounts before backups and destructive operations."""
from .errors import BackupError


def check_mounts(job, runner):
    pairs = [("origem", job.source, job.source_mount)]
    if job.type == "local":
        from pathlib import Path
        pairs.append(("destino", Path(job.destination), job.destination_mount))
    for label, directory, expected in pairs:
        if expected is None:
            continue
        if directory != expected and expected not in directory.parents:
            raise BackupError(f"Montagem de {label}: diretório fora do ponto esperado {expected}")
        try:
            if not expected.is_dir():
                raise BackupError("ponto ausente")
            runner.run(["mountpoint", "-q", str(expected)])
            if next(expected.iterdir(), None) is None:
                raise BackupError("volume vazio")
        except (BackupError, OSError):
            raise BackupError(f"Montagem de {label} ausente, vazia, ilegível ou diferente da esperada: {expected}") from None
