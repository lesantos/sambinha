from contextlib import redirect_stdout
from dataclasses import replace
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from sambinha.config import Config, Job, validate
from sambinha.engine import run


class Sandbox(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="sambinha-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.destination = self.root / "destination"
        self.config = Config(jobs=(Job("test", self.source, str(self.destination)),),
                             state_dir=self.root / "state", log_dir=self.root / "logs",
                             verification_mode="hash")
        self.notifications = []

    def file(self, path, content=b"test", mode=0o640):
        target = self.source / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        target.chmod(mode)
        return target

    def execute(self, config=None, now=1000, **kwargs):
        self.output = io.StringIO()
        with redirect_stdout(self.output):
            code = run(validate(config or self.config), clock=lambda: now,
                       notifier=lambda *args: self.notifications.append(args), **kwargs)
        self.events = [json.loads(line) for line in self.output.getvalue().splitlines()]
        return code

    def state(self):
        return json.loads((self.config.state_dir / "test" / "state.json").read_text())


def contents(directory):
    return {p.relative_to(directory).as_posix(): p.read_bytes()
            for p in directory.rglob("*") if p.is_file()
            and ".sambinha" not in p.relative_to(directory).parts}


def restore_batches(state, destination, restore):
    expected = {}
    for key, control in state["active"].items():
        remote = json.loads((destination / (key + ".json")).read_text())
        if remote != control:
            raise AssertionError("Remote control differs from local manifest")
        archive = destination / key
        extracted = restore / hashlib.sha256(key.encode()).hexdigest()[:12]
        extracted.mkdir(parents=True)
        subprocess.run(["7z", "x", "-y", "-o" + str(extracted), str(archive)],
                       check=True, capture_output=True)
        actual = contents(extracted)
        if set(actual) != {item["path"] for item in control["files"]}:
            raise AssertionError(f"Incomplete or extra archived paths: {set(actual)!r} != {set(item['path'] for item in control['files'])!r}")
        for item in control["files"]:
            payload = actual[item["path"]]
            if len(payload) != item["size"] or hashlib.sha256(payload).hexdigest() != item["sha256"]:
                raise AssertionError("Corrupt archive member")
            if item["path"] in expected:
                raise AssertionError("File appears in multiple active lots")
            expected[item["path"]] = payload
    return expected


REAL_TOOLS = bool(shutil.which("rclone") and shutil.which("7z"))
