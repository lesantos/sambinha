"""Modification-time comparisons with nanosecond precision."""
from datetime import datetime
import re

from .errors import BackupError

_TIME = re.compile(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d{1,9}))?(Z|[+-]\d\d:\d\d)\Z")


def timestamp_ns(value):
    match = _TIME.fullmatch(value) if isinstance(value, str) else None
    if not match:
        raise BackupError("Destino não fornece uma data de modificação válida")
    try:
        date = datetime.fromisoformat(match[1] + match[3].replace("Z", "+00:00"))
        return int(date.timestamp()) * 1_000_000_000 + int((match[2] or "").ljust(9, "0"))
    except ValueError:
        raise BackupError("Destino não fornece uma data de modificação válida") from None


def protect_newer(files, destination_times, event):
    conflicts = []
    for item in files:
        if item.path in destination_times and destination_times[item.path] > item.mtime_ns:
            conflicts.append(item.path)
            event("newer_destination", path=item.path, source_mtime_ns=item.mtime_ns,
                  destination_mtime_ns=destination_times[item.path])
    if conflicts:
        raise BackupError("Arquivos mais novos preservados no destino: " + ", ".join(conflicts))
