import io
import json
import re
import shutil
import subprocess
import threading
import unittest
from unittest.mock import Mock, patch

from sambinha.process import Runner, rclone_progress
from sambinha.progress import Progress
from tests.helpers import Sandbox, contents
from tests.test_progress import Terminal


class RcloneStreamingTests(unittest.TestCase):
    def stream(self, records):
        text = "".join(line if isinstance(line, str) else json.dumps(line) + "\n" for line in records)
        process = Mock(stdout=io.StringIO(text))
        process.wait.return_value = 0
        report = Mock()
        runner = Runner()
        with patch("sambinha.process.subprocess.Popen", return_value=process):
            result = runner._run_with_progress(["rclone", "sync", "source", "destination"], None, None, report)
        self.assertEqual(result.stderr, text)
        self.assertEqual(runner.last_stderr, text)
        return report

    def test_json_counters_override_one_line_message_percentage(self):
        report = self.stream([{
            "level": "warning", "msg": "  100 KiB / 100 KiB, 100%, 0 B/s, ETA -\n",
            "stats": {"bytes": 25, "totalBytes": 100, "transfers": 1, "totalTransfers": 4,
                      "checks": 12, "totalChecks": 30, "deletes": 2, "renames": 2},
        }])
        self.assertEqual(report.call_args.args, (25.0,))
        self.assertIn("conferidos: 12/30", report.call_args.kwargs["activity"])
        self.assertIn("copiados: 1/4", report.call_args.kwargs["activity"])
        self.assertIn("remoções: 2", report.call_args.kwargs["activity"])

    def test_delete_before_reports_activity_without_false_completed_transfer(self):
        report = self.stream([{
            "msg": "    7.341 GiB / 7.341 GiB, 100%, 0 B/s, ETA -\n",
            "stats": {"bytes": 7882693452, "totalBytes": 7882693452, "transfers": 0,
                      "totalTransfers": 0, "checks": 20004, "deletes": 10004, "renames": 10000},
        }])
        self.assertEqual(report.call_args.args, (None,))
        self.assertIn("conferidos: 20004", report.call_args.kwargs["activity"])
        self.assertIn("movidos: 10000", report.call_args.kwargs["activity"])

    def test_empty_files_use_completed_file_count(self):
        percent, activity = rclone_progress({"bytes": 0, "totalBytes": 0, "transfers": 2, "totalTransfers": 8})
        self.assertEqual(percent, 25)
        self.assertIn("copiados: 2/8", activity)

    def test_plain_and_json_messages_without_stats_support_both_formats(self):
        for message in ("Transferred: 1 MiB / 2 MiB, 50%, 1 MiB/s, ETA 1s\n",
                        "    1 MiB / 2 MiB, 50%, 1 MiB/s, ETA 1s\n"):
            for record in (message, {"msg": message}):
                with self.subTest(record=record):
                    report = self.stream([record])
                    self.assertEqual(report.call_args.args, (50.0,))

    def test_malformed_messages_and_invalid_counters_do_not_stop_reader(self):
        report = self.stream([
            "not JSON\n", {"msg": 123}, {"msg": "Copied directory/file, 50%"},
            {"msg": "99 B / 100 B, 99%", "stats": {"bytes": "invalid", "totalBytes": 0,
                                                     "transfers": True, "checks": float("nan")}},
            {"stats": {"bytes": 50, "totalBytes": 100, "totalTransfers": 1}},
        ])
        self.assertEqual(report.call_count, 2)
        self.assertEqual(report.call_args_list[0].args, (None,))
        self.assertEqual(report.call_args_list[1].args, (50.0,))

    def test_timeout_terminates_and_joins_streaming_reader(self):
        process = Mock(stdout=io.StringIO('{"stats":{"checks":1}}\n'))
        process.wait.side_effect = [subprocess.TimeoutExpired("rclone", 1), 0]
        with patch("sambinha.process.subprocess.Popen", return_value=process), \
             self.assertRaises(subprocess.TimeoutExpired):
            Runner(timeout=1)._run_with_progress(["rclone", "sync"], None, None, Mock())
        process.kill.assert_called_once()
        self.assertEqual(process.wait.call_count, 2)
        self.assertTrue(process.stdout.closed)

    def test_interruption_does_not_leave_child_or_reader_running(self):
        process = Mock(stdout=io.StringIO('{"stats":{"checks":1}}\n'))
        process.wait.side_effect = [KeyboardInterrupt, 0]
        with patch("sambinha.process.subprocess.Popen", return_value=process), \
             self.assertRaises(KeyboardInterrupt):
            Runner()._run_with_progress(["rclone", "sync"], None, None, Mock())
        process.kill.assert_called_once()
        self.assertTrue(process.stdout.closed)


class MeasuredActivityTests(unittest.TestCase):
    def test_heartbeat_keeps_activity_visible_without_inventing_percentage(self):
        updated = threading.Event()

        class ObservedTerminal(Terminal):
            def write(self, value):
                result = super().write(value)
                if ("remoções: 12" in value or "rem:12" in value) and ("tempo: 7s" in value or "t:7s" in value):
                    updated.set()
                return result

        terminal = ObservedTerminal()
        clock = Mock(return_value=0)
        progress = Progress(terminal, clock=clock)
        with progress.scope(0.2, 0.8).task("Sincronizando arquivos", "/dados", measured=True) as report:
            report(None, activity="conferidos: 8 | remoções: 12")
            clock.return_value = 7
            self.assertTrue(updated.wait(timeout=3))
            self.assertEqual(progress.percent, 20)
            self.assertNotIn("100.0%", terminal.getvalue())
            report(80, activity="copiados: 8/10")
            self.assertAlmostEqual(progress.percent, 68)
            report(20, activity="copiados: 8/40")
            self.assertAlmostEqual(progress.percent, 68)
        self.assertAlmostEqual(progress.percent, 80)

    def test_failure_stops_heartbeat_and_clears_terminal_without_completion(self):
        terminal = Terminal()
        progress = Progress(terminal)
        with self.assertRaises(RuntimeError):
            with progress.task("Sincronizando arquivos", "/dados", measured=True) as report:
                report(None, activity="remoções: 3")
                raise RuntimeError("failed")
        self.assertFalse(progress.visible)
        self.assertEqual(progress.percent, 0)
        self.assertNotIn("100.0%", terminal.getvalue())

    def test_activity_is_redacted_and_sanitized(self):
        terminal = Terminal()
        progress = Progress(terminal, redact=lambda value: value.replace("secret", "[REDACTED]"))
        progress.update("Sincronizando", "/dados", activity="secret\n\x1b")
        self.assertIn("[REDACTED]??", terminal.getvalue())
        self.assertNotIn("secret", terminal.getvalue())


@unittest.skipUnless(shutil.which("rclone"), "rclone required")
class RcloneProgressIntegrationTests(Sandbox):
    def test_real_rclone_stream_updates_percentage_before_command_completes(self):
        self.file("unique-filename", b"x" * (256 * 1024))
        terminal = Terminal()
        runner = Runner(timeout=30)
        runner.progress = Progress(terminal).scope(0.2, 0.8)
        samples = []

        def record(stats):
            samples.append(dict(stats))
            return rclone_progress(stats)

        with patch("sambinha.process.rclone_progress", side_effect=record):
            runner.run(["rclone", "copy", str(self.source), str(self.destination),
                        "--bwlimit", "64Ki", "--disable", "Copy", "-v"])
        self.assertEqual(contents(self.source), contents(self.destination))
        self.assertTrue(any(0 < s.get("bytes", 0) < s.get("totalBytes", 0) for s in samples), samples)
        values = [float(p) for p in re.findall(r"(\d+\.\d+)% (?:do backup|est\.)", terminal.getvalue())]
        self.assertTrue(any(20 < p < 80 for p in values), values)
        self.assertEqual(values, sorted(values))
        self.assertTrue("conferidos:" in terminal.getvalue() or "conf:" in terminal.getvalue())
        self.assertTrue("copiados:" in terminal.getvalue() or "cóp:" in terminal.getvalue())
        self.assertNotIn("unique-filename", terminal.getvalue())
        self.assertIn('"stats":', runner.last_stderr)
