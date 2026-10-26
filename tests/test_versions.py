import io
import json
import stat
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

from sambinha.config import validate
from sambinha.errors import BackupError
from sambinha.recovery import recover
from sambinha.versions import (
    MIN_7ZIP,
    MIN_RCLONE,
    check_compressor_version,
    check_rclone_version,
    parse_7zip_version,
    parse_rclone_version,
)
from tests.helpers import Sandbox


class FakeRunner:
    def __init__(self, output=None, error=None):
        self.output, self.error, self.calls = output, error, []

    def run(self, args, **kwargs):
        self.calls.append(args)
        if self.error:
            raise self.error
        return self.output


class ParsingTests(unittest.TestCase):
    def test_rclone_banners(self):
        for text, expected in (("rclone v1.75.2\n- os/arch: linux/amd64\n", (1, 75)),
                               ("rclone v1.53.3-DEV\n- os/arch: linux/amd64\n", (1, 53)),
                               ("rclone v1.60.0\n", (1, 60)),
                               ("rclone v1.9.0\n", (1, 9)),
                               ("rclone v1.100.1\n", (1, 100)),
                               ("rclone vDEV\n", None), ("", None), (None, None)):
            with self.subTest(text=text):
                self.assertEqual(parse_rclone_version(text), expected)

    def test_7zip_banners(self):
        for text, expected in (
                ("\n7-Zip [64] 16.02 : Copyright (c) 1999-2016 Igor Pavlov : 2016-05-21\n", (16, 2)),
                ("\n7-Zip [64] 17.05 : Copyright (c) 1999-2021 Igor Pavlov : 2017-08-28\n", (17, 5)),
                ("\n7-Zip 9.20  Copyright (c) 1999-2010 Igor Pavlov  2010-11-18\n", (9, 20)),
                ("\n7-Zip (z) 21.07 (x64) : Copyright (c) 1999-2021 Igor Pavlov : 2021-12-26\n", (21, 7)),
                ("\n7-Zip (z) 22.00 (x64) : Copyright (c) 1999-2022 Igor Pavlov : 2022-06-15\n", (22, 0)),
                ("\n7-Zip (a) 23.01 (x64) : Copyright (c) 1999-2023 Igor Pavlov : 2023-06-20\n", (23, 1)),
                ("\n7-Zip (r) 25.01 (x64) : Copyright (c) 1999-2025 Igor Pavlov : 2025-08-03\n", (25, 1)),
                ("7-Zip 24.09 (arm64)\n", (24, 9)),
                ("unzip 6.00", None), ("", None), (None, None)):
            with self.subTest(text=text):
                self.assertEqual(parse_7zip_version(text), expected)

    def test_minimums_are_the_verified_ones(self):
        self.assertEqual(MIN_RCLONE, (1, 60))
        self.assertEqual(MIN_7ZIP, (22, 0))


class CheckTests(unittest.TestCase):
    def test_rclone_below_minimum_is_rejected_with_both_versions(self):
        runner = FakeRunner("rclone v1.53.3-DEV\n")
        with self.assertRaises(BackupError) as raised:
            check_rclone_version(runner, "rclone")
        self.assertIn("rclone 1.53", str(raised.exception))
        self.assertIn("1.60", str(raised.exception))
        self.assertEqual(runner.calls, [["rclone", "version"]])

    def test_rclone_minimum_and_newer_are_accepted(self):
        for text, expected in (("rclone v1.60.0\n", (1, 60)), ("rclone v1.75.2\n", (1, 75)),
                               ("rclone v2.0.0\n", (2, 0))):
            with self.subTest(text=text):
                self.assertEqual(check_rclone_version(FakeRunner(text), "rclone"), expected)

    def test_rclone_minor_is_compared_numerically(self):
        self.assertEqual(check_rclone_version(FakeRunner("rclone v1.100.0\n"), "rclone"), (1, 100))
        with self.assertRaises(BackupError):
            check_rclone_version(FakeRunner("rclone v1.9.9\n"), "rclone")

    def test_7zip_below_minimum_is_rejected_with_reason(self):
        for text in ("7-Zip [64] 16.02 : Copyright", "7-Zip (z) 21.07 (x64) : Copyright"):
            with self.subTest(text=text), self.assertRaises(BackupError) as raised:
                check_compressor_version(FakeRunner(text), "7z")
            self.assertIn("(22.00)", str(raised.exception))
            self.assertIn("fração de segundo", str(raised.exception))

    def test_7zip_minimum_and_newer_are_accepted(self):
        for text, expected in (("7-Zip (z) 22.00 (x64)", (22, 0)), ("7-Zip (z) 25.01 (x64)", (25, 1))):
            with self.subTest(text=text):
                runner = FakeRunner(text)
                self.assertEqual(check_compressor_version(runner, "7zz"), expected)
                self.assertEqual(runner.calls, [["7zz", "-h"]])

    def test_unreadable_versions_do_not_block(self):
        self.assertIsNone(check_rclone_version(FakeRunner("rclone vDEV\n"), "rclone"))
        self.assertIsNone(check_rclone_version(Mock(), "rclone"))
        self.assertIsNone(check_compressor_version(FakeRunner("custom archiver 1.0"), "7z"))
        self.assertIsNone(check_compressor_version(Mock(), "7z"))
        self.assertIsNone(check_compressor_version(FakeRunner(error=BackupError("falhou")), "7z"))


class VersionIntegrationTests(Sandbox):
    """Fake executables prove the checks run before any real work starts."""

    def program(self, name, banner):
        directory = self.root / "bin"
        directory.mkdir(exist_ok=True)
        path = directory / name
        path.write_text(f"#!/bin/sh\necho '{banner}'\n")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        return str(path)

    def configure(self, *, rclone="rclone v1.75.2", compressor="7-Zip (z) 25.01 (x64)", logic="batch"):
        job = replace(self.config.jobs[0], logic=logic, compressor=self.program("compressor", compressor))
        self.config = replace(self.config, jobs=(job,), rclone=self.program("rclone", rclone))

    def error_text(self, events):
        return " ".join(e["error"] for e in events if e["event"] == "error")

    def test_backup_stops_on_old_rclone_before_touching_anything(self):
        self.file("file")
        self.configure(rclone="rclone v1.53.3-DEV")
        self.assertEqual(self.execute(), 1)
        self.assertIn("rclone 1.53 é anterior", self.error_text(self.events))
        self.assertFalse(Path(self.config.jobs[0].destination).exists())
        self.assertFalse((self.config.state_dir / "test" / "state.json").exists())

    def test_batch_backup_stops_on_old_7zip_before_touching_anything(self):
        self.file("file")
        self.configure(compressor="7-Zip [64] 16.02 : Copyright (c) 1999-2016 Igor Pavlov")
        self.assertEqual(self.execute(), 1)
        self.assertIn("7-Zip 16.02 é anterior", self.error_text(self.events))
        self.assertFalse(Path(self.config.jobs[0].destination).exists())
        self.assertFalse((self.config.state_dir / "test" / "state.json").exists())

    def test_simple_jobs_do_not_check_the_compressor(self):
        self.file("file")
        self.configure(compressor="7-Zip [64] 16.02 : Copyright", logic="simple")
        self.execute()
        self.assertNotIn("7-Zip", self.error_text(self.events))

    def recover_action(self, action):
        output = io.StringIO()
        with redirect_stdout(output):
            result = recover(validate(self.config), action)
        events = [json.loads(line) for line in output.getvalue().splitlines()]
        return result, events

    def test_recovery_checks_both_programs(self):
        self.configure(rclone="rclone v1.53.3-DEV")
        result, events = self.recover_action("verify-destination-integrity")
        self.assertEqual(result, 1)
        self.assertIn("rclone 1.53 é anterior", self.error_text(events))
        self.configure(compressor="7-Zip [64] 16.02 : Copyright")
        result, events = self.recover_action("verify-destination-integrity")
        self.assertEqual(result, 1)
        self.assertIn("7-Zip 16.02 é anterior", self.error_text(events))


if __name__ == "__main__":
    unittest.main()
