"""Complete JSON-lines audits; interactive terminals only show run-level events."""
from datetime import datetime, timezone
import json
from pathlib import Path
import uuid
import os
import sys

from .state import private_directory

CONSOLE_EVENTS = {"start", "directory", "summary", "error", "end", "notification_error",
                  "deletion_alert", "mass_deletion_blocked"}


class RunLog:
    def __init__(self, directory: Path, redact=None, progress=None):
        private_directory(directory)
        self.redact = redact or (lambda value: value)
        self.progress = progress
        self.interactive = sys.stdout.isatty() or bool(progress and progress.enabled)
        self.id = uuid.uuid4().hex
        self.path = directory / f"{datetime.now(timezone.utc):%Y%m%dT%H%M%S}-{self.id}.jsonl"
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        self.stream = os.fdopen(fd, "w", encoding="utf-8")

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
