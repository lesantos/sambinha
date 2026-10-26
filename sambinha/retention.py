"""Durable deletion deadlines and non-blocking forecasts shared by all jobs."""
from datetime import UTC, datetime

from .errors import BackupError

DAY = 86400


def retirement(key, files, reason, now, days, control=None):
    expires = now + days * DAY
    entry = {"key": key, "files": list(files), "reason": reason,
             "retired_at": now, "retention_days": days, "expires": expires,
             "alert_at": expires - min(2, days) * DAY}
    if control:
        entry["control"] = control
    return entry


def alert_time(entry, deleted_days, version_days):
    if "alert_at" in entry:
        return entry["alert_at"]
    days = deleted_days if entry.get("reason") == "deleted" else version_days
    return entry["expires"] - min(2, days) * DAY


def date(value):
    try:
        return datetime.fromtimestamp(value, UTC).isoformat()
    except (ValueError, OverflowError, OSError):
        raise BackupError("Prazo de exclusão fora do intervalo de datas suportado") from None


class DeletionForecast:
    def __init__(self, threshold, log, notify):
        self.threshold, self.log, self.notify = threshold, log, notify
        self.records = {}
        self.sent = False
        self.count_reported = 0
        self.message = ""

    def add(self, job, candidates):
        for entry, files in candidates:
            self.records[(job, entry["key"], entry["expires"])] = (entry, list(files))
        count = sum(len(files) for entry, files in self.records.values())
        if count <= self.threshold or count == self.count_reported:
            return
        self.count_reported = count
        first = min(entry["expires"] for entry, files in self.records.values())
        self.message = (f"Alerta de exclusão definitiva: {count} arquivos/versões acima do limite "
                        f"{self.threshold}; primeira data {date(first)}. Confira os arquivos no log; "
                        "o backup e os prazos continuam normalmente.")
        self.log.event("deletion_alert", count=count, threshold=self.threshold,
                       first_deletion_at=date(first), message=self.message)
        for (name, key, expires), (entry, files) in sorted(self.records.items()):
            for path in files:
                self.log.event("deletion_candidate", job=name, path=path, object=key,
                               reason=entry.get("reason"), deletion_at=date(expires))
        # Alert now, before any imminent purge; do not raise or require approval.
        self.notify(self.message)
        self.sent = True
