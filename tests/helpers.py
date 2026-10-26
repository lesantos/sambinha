import hashlib
import io
import json
import shutil
import subprocess
import tempfile
import time
import unittest
from collections import namedtuple
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path
from typing import ClassVar
from unittest.mock import Mock

from sambinha.batches import plan
from sambinha.config import JOB_SCOPED_OPTIONS, Config, Job, with_job_overrides
from sambinha.config_validation import validate
from sambinha.context import Context
from sambinha.engine import run
from sambinha.inventory import scan
from sambinha.process import Interrupted, Runner


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


Usage = namedtuple('Usage', 'total used free')


class Terminal(io.StringIO):
    def isatty(self):
        return True


class BatchContext:
    def context(self, workers=2, count=9):
        for index in range(count):
            self.file(f'{index}.bin', b'contents')
        job = replace(self.config.jobs[0], type='s3', logic='batch',
                      destination='aws:fake-bucket/', max_megabytes=1,
                      check_source_disk=False, check_destination_disk=False)
        config = replace(self.config, jobs=(replace(job, batch_workers=workers),))
        job = config.jobs[0]
        files = scan(self.source)
        ctx = Context(config, job, Runner(), Mock(), files, 1000)
        # Keep one tiny file per test batch while the configured size limit is
        # large enough that these batches are not treated as oversized.
        pending = plan(files, 1)
        active = {item.key: item.control() for item in pending}
        return ctx, pending, active


class Reply(namedtuple('Reply', 'stdout stderr')):
    """Scripted result carrying the child's stderr (exposed as ``FakeRunner.last_stderr``)."""


class FakeRunner:
    """Scripted stand-in for ``sambinha.process.Runner``: records commands, never spawns anything.

    ``script`` maps a command prefix (a string split on spaces, or a tuple of arguments) to a reply;
    the longest matching prefix wins and unmatched commands return ``default``. A reply is a string
    (stdout), a ``Reply`` (stdout + stderr), an exception instance (raised), a callable
    ``(args, **options) -> reply`` or a list consumed one item per call (the last one repeats).
    """

    def __init__(self, script=None, *, default=""):
        self.script = {tuple(k.split()) if isinstance(k, str) else tuple(k): v for k, v in (script or {}).items()}
        self.default = default
        self.commands = []   # every argument list, as strings
        self.options = []    # keyword options of each run() call, parallel to commands
        self.required = []
        self.last_stderr = None
        self.progress = None
        self.env = None
        self._cancelled = False

    @property
    def cancelled(self):
        return self._cancelled

    def cancel(self):
        self._cancelled = True

    def check_cancelled(self):
        if self._cancelled:
            raise Interrupted()

    def require(self, programs):
        self.required.append(tuple(programs))

    def safe_error(self, message):
        return message

    def calls(self, *prefix):
        """Recorded commands starting with ``prefix`` (e.g. ``calls('rclone', 'lsjson')``)."""
        return [c for c in self.commands if tuple(c[:len(prefix)]) == prefix]

    def run(self, args, **options):
        self.check_cancelled()
        command = [str(a) for a in args]
        self.commands.append(command)
        self.options.append(options)
        matches = [k for k in self.script if tuple(command[:len(k)]) == k]
        reply = self.script[max(matches, key=len)] if matches else self.default
        if isinstance(reply, list):
            reply = reply.pop(0) if len(reply) > 1 else reply[0]
        if callable(reply):
            reply = reply(command, **options)
        if isinstance(reply, BaseException):
            raise reply
        self.last_stderr = reply.stderr if isinstance(reply, Reply) else None
        return reply.stdout if isinstance(reply, Reply) else reply
