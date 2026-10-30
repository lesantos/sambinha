"""Durable deletion deadlines and non-blocking forecasts shared by all jobs."""
from datetime import datetime

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
        return datetime.fromtimestamp(value).astimezone().isoformat()
    except (ValueError, OverflowError, OSError):
        raise BackupError("Prazo de exclusão fora do intervalo de datas suportado") from None


class DeletionForecast:
    def __init__(self, threshold, log, notify):
        self.threshold, self.log, self.notify = threshold, log, notify
        self.records = {}
        self.present = {}
        self.sent = False
        self.count_reported = 0
        self.message = ""

    def add(self, job, candidates, present=None):
        """present: paths still in the source (None = unknown). Members of a retired batch that are
        still present only moved to another batch; they are logged as deletion_rebatched (detailed only)."""
        for entry, files in candidates:
            self.records[(job, entry["key"], entry["expires"])] = (entry, list(files))
            self.present[(job, entry["key"], entry["expires"])] = present
        listing = []
        for (name, key, expires), (entry, files) in sorted(self.records.items()):
            present = self.present[(name, key, expires)]
            for path in files:
                listing.append((name, key, expires, entry, path, present is not None and path in present))
        # Only files really gone from the source count: members that merely moved to a rebuilt batch
        # stay in the source and are never deleted for good.
        removed = [item for item in listing if not item[5]]
        count = len(removed)
        if count <= self.threshold or count == self.count_reported:
            return
        self.count_reported = count
        rebatched = len(listing) - count
        first = min(item[2] for item in removed)
        self.message = (f"Alerta de exclusão definitiva: {count} arquivos/versões acima do limite "
                        f"{self.threshold}; primeira data {date(first)}. Confira os arquivos no log; "
                        "o backup e os prazos continuam normalmente.")
        self.log.event("deletion_alert", count=count, threshold=self.threshold, removed=count,
                       rebatched=rebatched, first_deletion_at=date(first), message=self.message)
        for name, key, expires, entry, path, moved in listing:
            self.log.event("deletion_rebatched" if moved else "deletion_candidate", job=name, path=path,
                           object=key, reason=entry.get("reason"), deletion_at=date(expires))
        # Alert now, before any imminent purge; do not raise or require approval.
        self.notify(self.message)
        self.sent = True
