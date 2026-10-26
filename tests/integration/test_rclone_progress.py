import re
import shutil
import unittest
from unittest.mock import patch

from sambinha.process import Runner, rclone_progress
from sambinha.progress import Progress
from tests.helpers import Sandbox, Terminal, contents


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
