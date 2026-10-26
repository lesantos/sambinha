"""Verify expected local mounts before backups and destructive operations."""
import os
import sys
from pathlib import Path

from .errors import BackupError

_PLATFORM = sys.platform


def uses_mountpoint_command():
    """Linux ships util-linux `mountpoint`; macOS has no equivalent binary."""
    return _PLATFORM != "darwin"


def is_mount_point(path):
    """Portable `mountpoint -q`: a mount's device differs from its parent's.

    The root directory is its own parent and therefore also a mount point,
    which is the same rule util-linux applies.
    """
    try:
        here = os.lstat(path)
        parent = os.lstat(Path(path).parent)
    except OSError:
        return False
    return here.st_dev != parent.st_dev or here.st_ino == parent.st_ino


def check_mounts(job, runner, *, check_source=True):
    pairs = [("origem", job.source, job.source_mount)] if check_source else []
    if job.type == "local":
        pairs.append(("destino", Path(job.destination), job.destination_mount))
    for label, directory, expected in pairs:
        if expected is None:
            continue
        if directory != expected and expected not in directory.parents:
            raise BackupError(f"Montagem de {label}: diretório fora do ponto esperado {expected}")
        try:
            if not expected.is_dir():
                raise BackupError("ponto ausente")
            if uses_mountpoint_command():
                runner.run(["mountpoint", "-q", str(expected)])
            elif not is_mount_point(expected):
                raise BackupError("não é ponto de montagem")
            if next(expected.iterdir(), None) is None:
                raise BackupError("volume vazio")
        except (BackupError, OSError):
            raise BackupError(f"Montagem de {label} ausente, vazia, ilegível ou diferente da esperada: {expected}") from None
