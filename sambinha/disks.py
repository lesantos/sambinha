"""Reject local filesystems above 95 percent occupancy before writes."""
from pathlib import Path
import shutil
import tempfile

from .errors import BackupError


def check_disk(path, label, event=None):
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


def check_disks(job, event=None):
    if job.check_source_disk:
        check_disk(job.source, "origem", event)
    if job.type == "local" and job.check_destination_disk:
        check_disk(job.destination, "destino", event)
    if job.logic == "batch":
        check_disk(tempfile.gettempdir(), "temporários", event)
