"""Independent event detail for JSONL files and terminal output."""
from contextlib import redirect_stdout
from dataclasses import replace
import io
import json
import os
from unittest.mock import Mock
import unittest

from sambinha.cli import parser
from sambinha.config import load, validate
from sambinha.engine import run
from sambinha.errors import ConfigError
from sambinha.logging import RunLog
from sambinha.process import Runner
from sambinha.recovery import recover
from tests.helpers import REAL_TOOLS, Sandbox


class LogLevelTests(Sandbox):
    def test_simple_sync_requests_only_selected_rclone_output(self):
        self.file("file")
        seen = []
        runner = Mock()
        runner.run.side_effect = lambda args, **kwargs: seen.append((args, kwargs)) or ""
        for file_level, terminal_level, expected in (("summary", "summary", "ERROR"),
                                                    ("error", "summary", "ERROR"),
                                                    ("summary", "detailed", "DEBUG")):
            with self.subTest(file_level=file_level, terminal_level=terminal_level):
                config = replace(self.config, log_file_level=file_level, log_terminal_level=terminal_level)
                from sambinha.storage import Storage
                Storage(config.jobs[0], config, runner).sync_simple(self.source, ".sambinha/history/run",
                                                                    max_delete=100)
                args, kwargs = seen[-1]
                self.assertEqual(args[args.index("--log-level") + 1], expected)
                self.assertNotIn("-vv", args)
                self.assertEqual(kwargs.get("on_output") is not None, expected == "DEBUG")

    @unittest.skipUnless(REAL_TOOLS, "rclone and 7z required")
    def test_summary_sync_preserves_newer_destination_without_debug_output(self):
        source = self.file("file", b"old")
        config = validate(replace(self.config, log_file_level="summary", log_terminal_level="summary"))
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
        config = validate(replace(self.config, log_file_level="detailed", log_terminal_level="summary"))
        with redirect_stdout(io.StringIO()):
            self.assertEqual(run(config, runner=Runner()), 0)
        records = [json.loads(line) for line in next(config.log_dir.glob("*.jsonl")).read_text().splitlines()]
        self.assertTrue(any(item["event"] == "rclone" and item["level"] == "debug"
                            for item in records))

    def test_default_file_and_terminal_only_report_errors_and_summaries(self):
        config = replace(self.config, log_file_level="summary", log_terminal_level="summary", dry_run=True)
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(run(validate(config), runner=Mock()), 0)
        file_events = [json.loads(line)["event"] for line in next(config.log_dir.glob("*.jsonl")).read_text().splitlines()]
        terminal_events = [json.loads(line)["event"] for line in output.getvalue().splitlines()]
        self.assertEqual(file_events, ["summary", "end"])
        self.assertEqual(terminal_events, file_events)

    def test_file_and_terminal_levels_are_independent(self):
        output = io.StringIO()
        with redirect_stdout(output):
            log = RunLog(self.config.log_dir, file_level="error", terminal_level="detailed")
            log.event("start", source="private")
            log.event("summary", success=False)
            log.event("error", error="failure")
            log.close()
        self.assertEqual([json.loads(line)["event"] for line in log.path.read_text().splitlines()], ["error"])
        self.assertEqual([json.loads(line)["event"] for line in output.getvalue().splitlines()],
                         ["start", "summary", "error"])

        output = io.StringIO()
        with redirect_stdout(output):
            log = RunLog(self.config.log_dir, file_level="detailed", terminal_level="error")
            log.event("start")
            log.event("summary", success=False)
            log.event("error", error="failure")
            log.close()
        self.assertEqual([json.loads(line)["event"] for line in log.path.read_text().splitlines()],
                         ["start", "summary", "error"])
        self.assertEqual([json.loads(line)["event"] for line in output.getvalue().splitlines()], ["error"])

    def test_verification_uses_the_same_independent_levels(self):
        config = replace(self.config, dry_run=True, log_file_level="detailed", log_terminal_level="error")
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(recover(validate(config), "verify-local-metadata", runner=Mock()), 0)
        events = [json.loads(line)["event"] for line in next(config.log_dir.glob("*.jsonl")).read_text().splitlines()]
        self.assertIn("would_verify", events)
        self.assertEqual(events[-1], "end")
        self.assertEqual(output.getvalue(), "")

    def test_config_and_cli_levels(self):
        path = self.root / "config.toml"
        path.write_text('[[jobs]]\nname="test"\nsource="source"\ndestination="destination"\n'
                        'log_file_level="detailed"\nlog_terminal_level="error"\n')
        loaded = load(path)
        self.assertEqual((loaded.jobs[0].log_file_level, loaded.jobs[0].log_terminal_level), ("detailed", "error"))
        arguments = vars(parser().parse_args(["--log-file-level", "error", "--log-terminal-level", "summary"]))
        overridden = load(path, {name: arguments[name] for name in ("log_file_level", "log_terminal_level")})
        self.assertEqual((overridden.jobs[0].log_file_level, overridden.jobs[0].log_terminal_level), ("error", "summary"))
        for name in ("log_file_level", "log_terminal_level"):
            for invalid in ("info", 1, [], None):
                with self.subTest(name=name, invalid=invalid), self.assertRaises(ConfigError):
                    validate(replace(self.config, jobs=(replace(self.config.jobs[0], **{name: invalid}),)))
