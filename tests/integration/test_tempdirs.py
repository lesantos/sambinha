import os
import subprocess
import time
import unittest
from dataclasses import replace
from unittest.mock import patch

from sambinha.archives import compress
from sambinha.config import with_job_overrides
from tests.helpers import REAL_TOOLS, Sandbox

HOUR = 3600


def dead_pid():
    child = subprocess.Popen(['true'])
    child.wait()
    return child.pid


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

    def test_fresh_start_also_sweeps_young_leftovers(self):
        left = self.stale()
        os.utime(left, None)
        self.assertEqual(self.execute(), 0)
        self.assertTrue(left.exists())  # an ordinary run keeps a recent directory
        self.assertEqual(self.execute(now=2000, fresh_start=True), 0, self.output.getvalue())
        self.assertFalse(left.exists())

    def test_dry_run_leaves_leftovers_alone(self):
        left = self.stale()
        self.assertEqual(self.execute(config=with_job_overrides(self.config, dry_run=True)), 0)
        self.assertTrue(left.exists())
        self.assertFalse([e for e in self.events if e['event'] == 'temp_cleanup'])
