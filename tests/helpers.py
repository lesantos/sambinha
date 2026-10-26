import hashlib
import io
import json
import shutil
import subprocess
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path
from typing import ClassVar

from sambinha.config import JOB_SCOPED_OPTIONS, Config, Job, with_job_overrides
from sambinha.config_validation import validate
from sambinha.engine import run


def override(config, **values):
    """``replace`` for Config values that may mix global fields and per-job overrides."""
    jobs = {k: v for k, v in values.items() if k in JOB_SCOPED_OPTIONS}
    return with_job_overrides(replace(config, **{k: v for k, v in values.items() if k not in jobs}), **jobs)


def tick():
    """Let the filesystem clock advance before a deliberate edit.

    Some kernels (e.g. Linux 5.15 on ext4) stamp mtime/ctime with a coarse
    clock (a few ms). An edit made right after a scan then keeps identical
    timestamps and goes unnoticed, which is not what these tests exercise.
    """
    time.sleep(0.02)


class Sandbox(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="sambinha-test-")
        self.addCleanup(self.temp.cleanup)
        # macOS places temporary directories under /var, a symlink to
        # /private/var; configured roots are resolved by config.load, so the
        # sandbox resolves too and the no-follow directory walk sees real paths.
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / "source"
        self.source.mkdir()
        self.destination = self.root / "destination"
        self.config = with_job_overrides(
            Config(jobs=(Job("test", self.source, str(self.destination)),),
                   state_dir=self.root / "state", log_dir=self.root / "logs"),
            verification_mode="hash", log_file_level="detailed")
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
            code = run(validate(with_job_overrides(config or self.config, log_terminal_level="detailed")), clock=lambda: now,
                       notifier=lambda *args: self.notifications.append(args), **kwargs)
        self.events = [json.loads(line) for line in self.output.getvalue().splitlines()]
        return code

    def state(self):
        return json.loads((self.config.state_dir / "test" / "state.json").read_text())

    # Canonical batch-job builders shared by the integration suites.
    batch_defaults: ClassVar[dict] = {}

    def job(self, **overrides):
        """First configured job with ``overrides`` applied (the config is untouched)."""
        return replace(self.config.jobs[0], **overrides)

    def use_batch(self, **overrides):
        """Switch the first job to batch logic; ``batch_defaults`` and ``overrides`` apply on top."""
        self.config = replace(self.config, jobs=(self.job(logic="batch", **{**self.batch_defaults, **overrides}),))

    def fixture(self):
        """Copy the shared source tree and add edge-case names (spaces, leading @/-, wildcards)."""
        shutil.copytree(Path(__file__).parent / "fixtures" / "source", self.source, dirs_exist_ok=True)
        self.file("empty", b"")
        self.file("ação/with spaces.txt", "Olá!".encode())
        self.file("ação/@leading.txt", b"@file")
        self.file("ação/-leading.txt", b"-file")
        self.file("ação/[wildcard]*?.txt", b"literal wildcards")
        self.file("binary.dat", bytes(range(256)) * 100)

    def assert_complete(self, logic=None):
        """Assert the local destination restores to exactly the source (simple copy or 7z batches)."""
        logic = logic or self.config.jobs[0].logic
        if logic == "simple":
            self.assertEqual(contents(self.destination), contents(self.source))
            return
        restored = self.root / ("restored-" + str(len(list(self.root.glob("restored-*")))))
        actual = restore_batches(self.state(), self.destination, restored)
        self.assertEqual(actual, contents(self.source))
        for key in set(self.state()["active"]):
            self.assertTrue((self.destination / key).is_file())
            self.assertTrue((self.destination / (key + ".json")).is_file())
        self.assertFalse(set(contents(self.source)).intersection(contents(self.destination)))
        for item in self.state()["active"].values():
            self.assertTrue(item["files"])


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
