import shutil
import unittest
from dataclasses import replace
from unittest.mock import patch

from sambinha.config import with_job_overrides
from sambinha.errors import BackupError
from sambinha.process import Runner
from tests.helpers import REAL_TOOLS, Sandbox


@unittest.skipUnless(REAL_TOOLS, 'rclone and 7z required')
@unittest.skipUnless(shutil.which('mountpoint'), 'util-linux mountpoint required')
class MountIntegrationTests(Sandbox):
    def test_real_mountpoint_refuses_ordinary_directory_and_notifies(self):
        self.file('file')
        self.config = replace(self.config, jobs=(replace(self.config.jobs[0], source_mount=self.source),))
        self.assertEqual(self.execute(), 1)
        self.assertFalse(self.destination.exists())
        self.assertEqual(self.notifications[-1][1], 'fail')
        self.assertIn('Montagem de origem', self.notifications[-1][3])

    def test_destination_unmounted_no_write_and_cronitor_gets_error(self):
        self.file('file')
        self.destination.mkdir()
        (self.destination / 'existing').write_bytes(b'untouched')
        self.config = replace(self.config, jobs=(replace(self.config.jobs[0], destination_mount=self.destination),))
        self.assertEqual(self.execute(), 1)
        self.assertEqual((self.destination / 'existing').read_bytes(), b'untouched')
        self.assertFalse((self.destination / 'file').exists())
        self.assertIn('Montagem de destino', self.notifications[-1][3])

    def test_both_expected_volumes_are_checked_before_and_during_copy(self):
        self.file('file')
        self.destination.mkdir()
        (self.destination / 'existing').write_bytes(b'old')
        self.config = replace(self.config, jobs=(replace(self.config.jobs[0], source_mount=self.source,
                                                        destination_mount=self.destination),))
        original = Runner.run
        checks = []
        def mounted(runner, args, **kwargs):
            if args[0] == 'mountpoint':
                checks.append(args[-1])
                return ''
            return original(runner, args, **kwargs)
        with patch.object(Runner, 'run', mounted):
            self.assertEqual(self.execute(), 0, self.output.getvalue())
        self.assertGreater(checks.count(str(self.source)), 1)
        self.assertGreater(checks.count(str(self.destination)), 1)
        self.assertEqual((self.destination / 'file').read_bytes(), b'test')

    def test_mount_lost_during_backup_stops_before_copy_and_notifies(self):
        self.file('file')
        self.config = replace(self.config, jobs=(replace(self.config.jobs[0], source_mount=self.source),))
        original = Runner.run
        checks = 0
        def lost(runner, args, **kwargs):
            nonlocal checks
            if args[0] == 'mountpoint':
                checks += 1
                if checks >= 2:
                    raise BackupError('mount disappeared')
                return ''
            return original(runner, args, **kwargs)
        with patch.object(Runner, 'run', lost):
            self.assertEqual(self.execute(), 1)
        self.assertFalse((self.destination / 'file').exists())
        self.assertEqual(self.notifications[-1][1], 'fail')

    def test_empty_expected_volume_stops_even_if_mountpoint_succeeds(self):
        volume = self.root / 'volume'
        volume.mkdir()
        self.file('file')
        self.config = replace(self.config, jobs=(replace(self.config.jobs[0], destination=str(volume / 'backup'),
                                                        destination_mount=volume),))
        original = Runner.run
        def mounted(runner, args, **kwargs):
            if args[0] == 'mountpoint':
                return ''
            return original(runner, args, **kwargs)
        with patch.object(Runner, 'run', mounted):
            self.assertEqual(self.execute(), 1)
        self.assertEqual(list(volume.iterdir()), [])
        self.assertEqual(self.notifications[-1][1], 'fail')

    def test_simulation_still_checks_mounts_but_does_not_notify(self):
        self.file('file')
        self.config = with_job_overrides(replace(self.config, jobs=(replace(self.config.jobs[0], source_mount=self.source),)), dry_run=True)
        self.assertEqual(self.execute(), 1)
        self.assertEqual(self.notifications, [])
