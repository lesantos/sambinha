import os
import subprocess
import time
import unittest
from dataclasses import replace
from unittest.mock import patch

from sambinha.archives import compress
from sambinha.tempdirs import prefix, sweep
from tests.helpers import REAL_TOOLS, Sandbox

HOUR = 3600


def dead_pid():
    child = subprocess.Popen(['true'])
    child.wait()
    return child.pid


class SweepTests(Sandbox):
    def make(self, name, age=2 * HOUR):
        path = self.root / 'tmp' / name
        path.mkdir(parents=True)
        (path / 'staged.bin').write_bytes(b'x' * 100)
        stamp = time.time() - age
        os.utime(path, (stamp, stamp))
        return path

    def sweep(self, **kwargs):
        return sweep(self.root / 'tmp', **kwargs)

    def test_prefix_carries_the_pid(self):
        self.assertEqual(prefix('batch'), f'sambinha-batch-{os.getpid()}-')

    def test_removes_old_dead_pid_directories_keeps_live_and_young_ones(self):
        pid = dead_pid()
        dead = self.make(f'sambinha-batch-{pid}-abc123')
        dead_hyphenated = self.make(f'sambinha-filter-dirs-{pid}-abc123')
        live = self.make(f'sambinha-batch-{os.getpid()}-abc123')
        young = self.make(f'sambinha-index-{pid}-abc123', age=60)
        legacy = self.make('sambinha-batch-abc123')
        other = self.make(f'unrelated-{pid}-abc123')
        self.assertEqual(self.sweep(), (2, 200))
        for gone in (dead, dead_hyphenated):
            self.assertFalse(gone.exists())
        for kept in (live, young, legacy, other):
            self.assertTrue(kept.exists())

    def test_min_age_zero_removes_young_dead_pid_directories(self):
        young = self.make(f'sambinha-index-{dead_pid()}-abc123', age=1)
        self.assertEqual(self.sweep(min_age=0)[0], 1)
        self.assertFalse(young.exists())

    def test_permission_denied_pid_counts_as_alive(self):
        directory = self.make('sambinha-batch-424242-abc123')
        with patch('os.kill', side_effect=PermissionError):
            self.assertEqual(self.sweep(), (0, 0))
        self.assertTrue(directory.exists())

    def test_symlinks_are_never_followed_or_removed(self):
        pid = dead_pid()
        target = self.root / 'precious'
        target.mkdir()
        (target / 'keep.txt').write_text('keep')
        link = self.root / 'tmp' / f'sambinha-batch-{pid}-link'
        link.parent.mkdir(exist_ok=True)
        link.symlink_to(target, target_is_directory=True)
        stale = self.make(f'sambinha-batch-{pid}-real')
        (stale / 'inner-link').symlink_to(target, target_is_directory=True)
        stamp = time.time() - 2 * HOUR
        os.utime(stale, (stamp, stamp))
        self.assertEqual(self.sweep()[0], 1)
        self.assertTrue(link.is_symlink())
        self.assertEqual((target / 'keep.txt').read_text(), 'keep')
        self.assertFalse(stale.exists())

    def test_missing_directory_is_not_an_error(self):
        self.assertEqual(sweep(self.root / 'absent'), (0, 0))


@unittest.skipUnless(REAL_TOOLS, 'rclone and 7z required')
class SweepIntegrationTests(Sandbox):
    def setUp(self):
        super().setUp()
        self.temp_dir = self.root / 'tmp'
        self.temp_dir.mkdir()
        job = replace(self.config.jobs[0], logic='batch')
        self.config = replace(self.config, jobs=(job,), temp_dir=self.temp_dir)
        self.file('a', b'aaaa')

    def stale(self):
        path = self.temp_dir / f'sambinha-batch-{dead_pid()}-left'
        path.mkdir()
        (path / 'half.7z').write_bytes(b'z' * 50)
        stamp = time.time() - 2 * HOUR
        os.utime(path, (stamp, stamp))
        return path

    def test_backup_sweeps_leftovers_logs_event_and_names_staging_by_pid(self):
        left = self.stale()
        seen = []

        def spy(worker, item, temp):
            seen.append(temp.name)
            return compress(worker, item, temp)
        with patch('sambinha.strategies.batch.compress', spy):
            self.assertEqual(self.execute(), 0, self.output.getvalue())
        self.assertFalse(left.exists())
        (event,) = [e for e in self.events if e['event'] == 'temp_cleanup']
        self.assertEqual((event['removed'], event['bytes']), (1, 50))
        self.assertTrue(seen and all(name.startswith(f'sambinha-batch-{os.getpid()}-') for name in seen))

    def test_dry_run_leaves_leftovers_alone(self):
        left = self.stale()
        self.assertEqual(self.execute(config=replace(self.config, dry_run=True)), 0)
        self.assertTrue(left.exists())
        self.assertFalse([e for e in self.events if e['event'] == 'temp_cleanup'])
