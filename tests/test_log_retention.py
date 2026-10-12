from contextlib import redirect_stdout
from dataclasses import replace
from datetime import datetime, timezone
import io
import os
from pathlib import Path
import time
import unittest
from unittest.mock import Mock, patch

from sambinha.engine import run
from sambinha.logging import RunLog
from sambinha.recovery import recover
from tests.helpers import REAL_TOOLS, Sandbox

DAY = 86400


class LogRetentionTests(Sandbox):
    def old_log(self, age, *, name=None, now=1700000000):
        directory = self.config.log_dir
        directory.mkdir(exist_ok=True)
        name = name or (datetime.fromtimestamp(now - age, timezone.utc).strftime('%Y%m%dT%H%M%S')
                        + '-' + 'a' * 32 + '.jsonl')
        path = directory / name
        path.write_text('{"event":"start"}\n')
        os.utime(path, (now - age, now - age))
        return path

    def test_default_period_keeps_recent_and_exact_boundary_but_removes_older(self):
        now = 1700000000
        expired = self.old_log(14 * DAY + 1, now=now)
        boundary = self.old_log(14 * DAY, now=now)
        recent = self.old_log(13 * DAY, now=now)
        future = self.old_log(-DAY, now=now)
        log = RunLog(self.config.log_dir)
        self.addCleanup(log.close)
        self.assertEqual(log.prune(self.config.log_retention_days, now=now), 1)
        self.assertFalse(expired.exists())
        for path in (boundary, recent, future, log.path):
            self.assertTrue(path.exists(), path)
        self.assertEqual(log.prune(14, now=now), 0)

    def test_configurable_period_and_zero_keep_forever(self):
        old = self.old_log(365 * DAY)
        log = RunLog(self.config.log_dir)
        self.addCleanup(log.close)
        for invalid in (-1, True, 1.5, '14'):
            with self.subTest(days=invalid), self.assertRaises(ValueError):
                log.prune(invalid, now=1700000000)
            self.assertTrue(old.exists())
        self.assertEqual(log.prune(0, now=1700000000), 0)
        self.assertTrue(old.exists())
        self.assertEqual(log.prune(400, now=1700000000), 0)
        self.assertEqual(log.prune(7, now=1700000000), 1)

    def test_recently_written_old_named_log_is_kept_and_current_log_never_removed(self):
        now = 1700000000
        old_name = '20000101T000000-' + 'a' * 32 + '.jsonl'
        recent = self.old_log(0, name=old_name, now=now)
        log = RunLog(self.config.log_dir)
        self.addCleanup(log.close)
        os.utime(log.path, (now - 100 * DAY, now - 100 * DAY))
        self.assertEqual(log.prune(14, now=now), 0)
        self.assertTrue(recent.exists())
        self.assertTrue(log.path.exists())

    def test_open_log_is_protected_until_writer_closes_even_after_abrupt_interruption(self):
        now = 1700000000
        writer = RunLog(self.config.log_dir)
        os.utime(writer.path, (now - 30 * DAY, now - 30 * DAY))
        collector = RunLog(self.config.log_dir)
        self.addCleanup(collector.close)
        try:
            self.assertEqual(collector.prune(14, now=now), 0)
            self.assertTrue(writer.path.exists())
        finally:
            writer.close()
        self.assertEqual(collector.prune(14, now=now), 1)
        self.assertFalse(writer.path.exists())

    def test_foreign_files_symlinks_directories_and_nested_logs_are_preserved(self):
        now = 1700000000
        foreign = self.old_log(30 * DAY, name='notes.jsonl', now=now)
        outside = self.root / 'important'
        outside.write_text('keep')
        link = self.config.log_dir / ('20000101T000000-' + 'b' * 32 + '.jsonl')
        link.symlink_to(outside)
        directory = self.config.log_dir / ('20000101T000000-' + 'c' * 32 + '.jsonl')
        directory.mkdir()
        nested = directory / ('20000101T000000-' + 'd' * 32 + '.jsonl')
        nested.write_text('keep')
        log = RunLog(self.config.log_dir)
        self.addCleanup(log.close)
        self.assertEqual(log.prune(14, now=now), 0)
        self.assertTrue(foreign.exists())
        self.assertTrue(link.is_symlink())
        self.assertEqual(outside.read_text(), 'keep')
        self.assertTrue(nested.exists())

    def test_path_replacement_race_does_not_remove_replacement(self):
        old = self.old_log(30 * DAY)
        replacement = self.root / 'replacement'
        replacement.write_text('keep')
        real_open = os.open
        log = RunLog(self.config.log_dir)
        self.addCleanup(log.close)
        def change(path, flags, *args, **kwargs):
            if Path(path) == old:
                old.unlink()
                os.replace(replacement, old)
            return real_open(path, flags, *args, **kwargs)
        with patch('sambinha.logging.os.open', side_effect=change):
            self.assertEqual(log.prune(14, now=1700000000), 0)
        self.assertEqual(old.read_text(), 'keep')

    def test_cleanup_race_and_symlink_replacement_are_safe(self):
        old = self.old_log(30 * DAY)
        log = RunLog(self.config.log_dir)
        self.addCleanup(log.close)
        with patch('sambinha.logging.os.open', side_effect=FileNotFoundError(2, 'removed')):
            self.assertEqual(log.prune(14, now=1700000000), 0)
        outside = self.root / 'important'
        outside.write_text('keep')
        real_open = os.open
        def change(path, flags, *args, **kwargs):
            if Path(path) == old:
                old.unlink()
                old.symlink_to(outside)
            return real_open(path, flags, *args, **kwargs)
        with patch('sambinha.logging.os.open', side_effect=change):
            self.assertEqual(log.prune(14, now=1700000000), 0)
        self.assertTrue(old.is_symlink())
        self.assertEqual(outside.read_text(), 'keep')

    def test_normal_backup_cleans_logs_and_dry_run_preserves_them(self):
        expired = self.old_log(30 * DAY, now=time.time())
        with redirect_stdout(io.StringIO()):
            self.assertEqual(run(replace(self.config, dry_run=True), runner=Mock()), 0)
        self.assertTrue(expired.exists())
        with redirect_stdout(io.StringIO()):
            self.assertEqual(run(self.config, runner=Mock()), 0)
        self.assertFalse(expired.exists())

    def test_restore_and_verify_cleanup_and_simulation(self):
        for action in ('restore', 'verify'):
            for dry_run in (True, False):
                with self.subTest(action=action, dry_run=dry_run):
                    expired = self.old_log(30 * DAY, now=time.time())
                    config = replace(self.config, dry_run=dry_run)
                    with redirect_stdout(io.StringIO()), patch('sambinha.recovery.restore_job'), \
                         patch('sambinha.recovery.verify_job'):
                        self.assertEqual(recover(config, action, runner=Mock(), output=self.root / 'restored'), 0)
                    self.assertEqual(expired.exists(), dry_run)
