"""Redacted JSON-lines events with independent file and terminal detail."""
from datetime import datetime, timezone
import json
from pathlib import Path
import uuid
import os
import re
import stat
import time
import fcntl
import errno

from .state import private_directory

ERROR_EVENTS = {"error", "notification_error", "mass_deletion_blocked"}
SUMMARY_EVENTS = ERROR_EVENTS | {"summary", "restore_summary", "verification_complete", "end", "deletion_alert"}

LOG_NAME = re.compile(r"\d{8}T\d{6}-[0-9a-f]{32}\.jsonl\Z")


class RunLog:
    def __init__(self, directory: Path, redact=None, progress=None, *,
                 file_level="summary", terminal_level="summary"):
        private_directory(directory)
        self.redact = redact or (lambda value: value)
        self.progress = progress
        self.file_level = file_level
        self.terminal_level = terminal_level
        self.id = uuid.uuid4().hex
        self.path = directory / f"{datetime.now(timezone.utc):%Y%m%dT%H%M%S}-{self.id}.jsonl"
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        # A retention pass in another invocation must not remove an open log.
        fcntl.flock(fd, fcntl.LOCK_SH)
        self.stream = os.fdopen(fd, "w", encoding="utf-8")

    def prune(self, days, *, now=None):
        """Remove only inactive Sambinha logs whose last write exceeds retention."""
        if type(days) is not int or days < 0:
            raise ValueError("log_retention_days deve ser um inteiro não negativo")
        if days == 0:
            return 0
        now = time.time() if now is None else now
        removed = 0
        for path in self.path.parent.iterdir():
            if path == self.path or not LOG_NAME.fullmatch(path.name):
                continue
            try:
                before = path.lstat()
                if not stat.S_ISREG(before.st_mode) or now - before.st_mtime <= days * 86400:
                    continue
                fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                try:
                    opened = os.fstat(fd)
                    if (not stat.S_ISREG(opened.st_mode)
                            or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)):
                        continue
                    try:
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        continue
                    # Recheck after acquiring the lock: another writer may
                    # have just finished, or the pathname may have changed.
                    current = path.lstat()
                    if ((current.st_dev, current.st_ino) == (opened.st_dev, opened.st_ino)
                            and now - os.fstat(fd).st_mtime > days * 86400):
                        path.unlink()
                        removed += 1
                finally:
                    os.close(fd)
            except OSError as exc:
                if exc.errno not in {errno.ENOENT, errno.ELOOP}:
                    raise
                # Concurrent cleanup or replacement with a symlink.
                continue
        return removed

    def event(self, event, **details):
        def visible(level):
            return (level == "detailed" or
                    (event in ERROR_EVENTS if level == "error" else event in SUMMARY_EVENTS))
        to_file = visible(self.file_level)
        to_terminal = visible(self.terminal_level)
        if not to_file and not to_terminal:
            return
        record = {"time": datetime.now(timezone.utc).isoformat(), "event": event, **details}
        # Redact values before JSON escaping so multiline secrets are covered.
        def clean(value):
            if isinstance(value, str):
                return self.redact(value)
            if isinstance(value, dict):
                return {k: clean(v) for k, v in value.items()}
            if isinstance(value, (list, tuple)):
                return [clean(v) for v in value]
            return value
        record = clean(record)
        line = json.dumps(record, ensure_ascii=False)
        if to_file:
            self.stream.write(line + "\n")
            self.stream.flush()
        if to_terminal:
            if self.progress:
                self.progress.clear()
            print(line)

    def close(self):
        self.stream.close()
