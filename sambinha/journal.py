"""Per-job upload journal: an interrupted batch backup resumes without recompressing/reuploading.

Each successful ``upload_batch`` appends the immutable archive it just stored to
``state/<job>/uploads.json``. The journal is only a hint: entries are trusted
again only when the destination still holds the object with the journaled size
and the very same members are still on the source (see ``strategies.batch``).
It is dropped when the interrupted run finally commits, and by ``--fresh-start``.
"""
import json
import re

from .errors import BackupError
from .inventory import safe_relative
from .local_paths import reject_symlinks
from .state import atomic_json, validate_file

JOURNAL = "uploads.json"
SCHEMA = 1


def journal_path(directory):
    return directory / JOURNAL


def entry_control(entry):
    """The batch control an entry stands for (what ``Batch.control()`` would have produced)."""
    control = {"schema": 1, "archive": entry["key"], "files": entry["files"],
               "archive_size": entry["archive_size"], "archive_md5": entry["archive_md5"]}
    for name in ("batch_id", "individual", "passthrough"):
        if name in entry:
            control[name] = entry[name]
    return control


def _validate(entry):
    safe_relative(entry["key"])
    if (type(entry["archive_size"]) is not int or entry["archive_size"] < 0
            or not re.fullmatch("[0-9a-f]{32}", entry["archive_md5"])
            or not isinstance(entry["files"], list) or not entry["files"]
            or type(entry["uploaded_at"]) not in (int, float)):
        raise ValueError("entrada do journal")
    for item in entry["files"]:
        validate_file(item)


def load(directory):
    """Valid journal entries by key; a missing, foreign or corrupt journal is simply empty."""
    path = journal_path(directory)
    reject_symlinks(path, label="Estado", include_parents=False)
    try:
        data = json.loads(path.read_text())
        if data["schema"] != SCHEMA:
            return {}
        entries = {}
        for key, entry in data["entries"].items():
            _validate(entry)
            if entry["key"] != key:
                raise ValueError("chave do journal")
            entries[key] = entry
        return entries
    except (OSError, ValueError, KeyError, TypeError, AttributeError, BackupError):
        return {}


def record(ctx, item, control):
    """Persist one finished upload; workers are threads, so writes are serialized."""
    entry = {"key": item.key, "archive_size": control["archive_size"], "archive_md5": control["archive_md5"],
             "files": control["files"], "uploaded_at": ctx.now}
    for name in ("batch_id", "individual", "passthrough"):
        if name in control:
            entry[name] = control[name]
    with ctx.journal_lock:
        ctx.journal[item.key] = entry
        atomic_json(journal_path(ctx.directory), {"schema": SCHEMA, "entries": ctx.journal})


def discard(ctx, key):
    """Forget one entry in memory; the file is rewritten by the next record or dropped on commit."""
    with ctx.journal_lock:
        ctx.journal.pop(key, None)


def clear(ctx):
    """Drop the journal after a successful commit (or a fresh start)."""
    with ctx.journal_lock:
        ctx.journal = {}
        reject_symlinks(journal_path(ctx.directory), label="Estado", include_parents=False)
        journal_path(ctx.directory).unlink(missing_ok=True)


def usable(ctx, confirmed, current_files):
    """Entries whose members still match the source exactly and that no confirmed batch owns.

    Overlapping entries (an older, superseded interruption) keep the first by key.
    Dropped entries are reported once and never reach the planner.
    """
    kept, claimed = {}, set()
    for key, entry in sorted(load(ctx.directory).items()):
        paths = [f["path"] for f in entry["files"]]
        if key in confirmed:
            reason = "confirmed"
        elif any(current_files.get(f["path"]) != f for f in entry["files"]):
            reason = "changed"
        elif claimed.intersection(paths):
            reason = "overlap"
        else:
            claimed.update(paths)
            kept[key] = entry
            continue
        ctx.event("journal_entry_discarded", archive=key, reason=reason)
    return kept
