import os
import unittest
from dataclasses import replace

from sambinha.config import with_job_overrides
from sambinha.process import Runner
from tests.helpers import REAL_TOOLS, Sandbox


@unittest.skipUnless(REAL_TOOLS, 'rclone and 7z required')
class NewerDestinationTests(Sandbox):
    def test_newer_destination_is_untouched_and_cronitor_gets_details(self):
        source = self.file('file', b'old source')
        self.assertEqual(self.execute(), 0)
        target = self.destination / 'file'
        target.write_bytes(b'new destination')
        ns = source.stat().st_mtime_ns + 1000000000
        os.utime(target, ns=(ns, ns))
        self.file('new', b'must not transfer before conflict detection')
        self.assertEqual(self.execute(), 1)
        self.assertTrue((self.destination / 'new').is_file())
        self.assertEqual(target.stat().st_mtime_ns, ns)
        self.assertEqual(self.notifications[-1][1], 'fail')
        self.assertIn('file', self.notifications[-1][3])
        self.assertIn('revisão manual', self.notifications[-1][3])

    def test_identical_but_newer_keeps_modification_time(self):
        source = self.file('file')
        self.assertEqual(self.execute(), 0)
        target = self.destination / 'file'
        ns = source.stat().st_mtime_ns + 1
        os.utime(target, ns=(ns, ns))
        self.assertEqual(self.execute(), 1)
        self.assertEqual(target.stat().st_mtime_ns, ns)

    def test_batch_older_source_preserves_all_confirmed_lots(self):
        source = self.file('file', b'confirmed')
        self.file('other', b'other')
        self.config = replace(self.config, jobs=(replace(self.config.jobs[0], logic='batch'),))
        self.assertEqual(self.execute(), 0)
        before = {p.relative_to(self.destination): p.read_bytes() for p in self.destination.rglob('*') if p.is_file()}
        ns = source.stat().st_mtime_ns - 1000000000
        source.write_bytes(b'older restored source')
        os.utime(source, ns=(ns, ns))
        self.assertEqual(self.execute(), 1)
        self.assertEqual(before, {p.relative_to(self.destination): p.read_bytes() for p in self.destination.rglob('*') if p.is_file()})
        self.assertEqual(self.notifications[-1][1], 'fail')

    def test_conflict_in_simulation_returns_error_without_notification(self):
        source = self.file('file')
        self.assertEqual(self.execute(), 0)
        ns = source.stat().st_mtime_ns + 1000000000
        os.utime(self.destination / 'file', ns=(ns, ns))
        self.notifications.clear()
        self.assertEqual(self.execute(with_job_overrides(self.config, dry_run=True)), 1)
        self.assertEqual(self.notifications, [])

    def test_concurrent_newer_destination_is_skipped_by_rclone_and_notified(self):
        from unittest.mock import patch
        source = self.file('file', b'first')
        self.assertEqual(self.execute(), 0)
        source.write_bytes(b'updated source')
        original = Runner.run
        def concurrent_change(runner, args, **kwargs):
            if args[1] == 'sync':
                target = self.destination / 'file'
                target.write_bytes(b'concurrent newer destination')
                ns = source.stat().st_mtime_ns + 1000000000
                os.utime(target, ns=(ns, ns))
            return original(runner, args, **kwargs)
        with patch.object(Runner, 'run', concurrent_change):
            self.assertEqual(self.execute(), 1)
        self.assertEqual((self.destination / 'file').read_bytes(), b'concurrent newer destination')
        self.assertEqual(self.notifications[-1][1], 'fail')
        self.assertIn('revisão manual', self.notifications[-1][3])
