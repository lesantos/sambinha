"""Reject local filesystems above 95 percent occupancy before writes."""
from pathlib import Path
import os
import shutil
import tempfile

from .errors import BackupError


def get_temporary_path(job):
    if job.temp_dir is None:
        return tempfile.gettempdir()
    directory = Path(job.temp_dir)
    if not directory.is_dir() or not os.access(directory, os.W_OK | os.X_OK):
        raise BackupError(f"Diretório temporário configurado não existe ou não permite gravação: {directory}")
    return directory


def check_disk(path, label, event=None, *, required_bytes=0):
    directory = Path(path)
    try:
        while not directory.exists() and directory.parent != directory:
            directory = directory.parent
        usage = shutil.disk_usage(directory)
    except OSError as exc:
        raise BackupError(f"Não foi possível medir o disco de {label}: {path}: {exc}") from exc
    if usage.total <= 0:
        raise BackupError(f"Não foi possível medir o disco de {label}: {path} "
                          f"(capacidade total informada: {usage.total} bytes)")
    # Include space unavailable to this user, such as filesystem reservations.
    percent = (usage.total - usage.free) * 100 / usage.total
    if event:
        event("disk_usage", role=label, path=str(directory), percent=round(percent, 2),
              total_bytes=usage.total, free_bytes=usage.free)
    if (usage.total - usage.free) * 100 > usage.total * 95:
        raise BackupError(f"Disco de {label} com ocupação de {percent:.2f}%, acima de 95%: {directory}")
    if usage.free < required_bytes:
        raise BackupError(f"Espaço insuficiente no disco de {label}: {directory}; "
                          f"necessários {required_bytes} bytes, disponíveis {usage.free} bytes")


def check_disks(job, event=None, *, temporary_path=None, check_temporary=True, check_source=True):
    if check_source and job.check_source_disk:
        check_disk(job.source, "origem", event)
    if job.type == "local" and job.check_destination_disk:
        check_disk(job.destination, "destino", event)
    if job.logic == "batch" and check_temporary:
        path = temporary_path if temporary_path is not None else get_temporary_path(job)
        check_disk(path, "temporários", event)
