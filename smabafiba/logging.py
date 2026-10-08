"""One UTF-8 JSON-lines audit log per invocation, also printed to stdout."""
from datetime import datetime, timezone
import json
from pathlib import Path
import uuid


class RunLog:
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        self.id = uuid.uuid4().hex
        self.path = directory / f"{datetime.now(timezone.utc):%Y%m%dT%H%M%S}-{self.id}.jsonl"
        self.stream = self.path.open("x", encoding="utf-8")

    def event(self, event, **details):
        record = {"time": datetime.now(timezone.utc).isoformat(), "event": event, **details}
        line = json.dumps(record, ensure_ascii=False)
        self.stream.write(line + "\n")
        self.stream.flush()
        print(line)

    def close(self):
        self.stream.close()
