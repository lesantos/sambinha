"""Independent event detail for JSONL files and terminal output."""


import io
import json
import os
import unittest
from contextlib import redirect_stdout

from sambinha.config import with_job_overrides
from sambinha.config_validation import validate
from sambinha.engine import run
from sambinha.process import Runner
from tests.helpers import REAL_TOOLS, Sandbox


class LogLevelTests(Sandbox):
    @unittest.skipUnless(REAL_TOOLS, "rclone and 7z required")
    def test_summary_sync_preserves_newer_destination_without_debug_output(self):
        source = self.file("file", b"old")
        config = validate(with_job_overrides(self.config, log_file_level="summary", log_terminal_level="summary"))
        with redirect_stdout(io.StringIO()):
            self.assertEqual(run(config, runner=Runner()), 0)
        target = self.destination / "file"
        target.write_bytes(b"new")
        ns = source.stat().st_mtime_ns + 1_000_000_000
        os.utime(target, ns=(ns, ns))
        runner = Runner()
        with redirect_stdout(io.StringIO()):
            self.assertEqual(run(config, runner=runner), 0)
        self.assertEqual(target.read_bytes(), b"new")
        self.assertEqual(target.stat().st_mtime_ns, ns)
        self.assertNotIn("Destination is newer", runner.last_stderr or "")
        records = [json.loads(line) for path in config.log_dir.glob("*.jsonl")
                   for line in path.read_text().splitlines()]
        self.assertNotIn("rclone", [item["event"] for item in records])

    @unittest.skipUnless(REAL_TOOLS, "rclone and 7z required")
    def test_detailed_simple_sync_records_rclone_debug(self):
        self.file("file", b"content")
        config = validate(with_job_overrides(self.config, log_file_level="detailed", log_terminal_level="summary"))
        with redirect_stdout(io.StringIO()):
            self.assertEqual(run(config, runner=Runner()), 0)
        records = [json.loads(line) for line in next(config.log_dir.glob("*.jsonl")).read_text().splitlines()]
        self.assertTrue(any(item["event"] == "rclone" and item["level"] == "debug"
                            for item in records))
