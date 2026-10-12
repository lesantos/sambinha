"""Complete JSON-lines audits; interactive terminals only show run-level events."""
from datetime import datetime, timezone
import json
from pathlib import Path
import uuid
import os
import sys
import re
import stat
import time
import fcntl
import errno

from .state import private_directory

CONSOLE_EVENTS = {"start", "directory", "summary", "error", "end", "notification_error",
                  "deletion_alert", "mass_deletion_blocked"}

LOG_NAME = re.compile(r"\d{8}T\d{6}-[0-9a-f]{32}\.jsonl\Z")


class RunLog:
    def __init__(self, directory: Path, redact=None, progress=None):
        private_directory(directory)
        self.redact = redact or (lambda value: value)
        self.progress = progress
        self.interactive = sys.stdout.isatty() or bool(progress and progress.enabled)
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
        self.stream.write(line + "\n")
        self.stream.flush()
        if self.interactive and event not in CONSOLE_EVENTS:
            return
        if self.progress:
            self.progress.clear()
        print(line)

    def close(self):
        self.stream.close()
