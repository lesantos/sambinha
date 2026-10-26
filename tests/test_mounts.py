import shutil
import unittest
from dataclasses import replace
from unittest.mock import Mock, patch

from sambinha.config import load, with_job_overrides
from sambinha.config_validation import validate
from sambinha.errors import BackupError, ConfigError
from sambinha.mounts import check_mounts
from sambinha.process import Runner
from tests.helpers import REAL_TOOLS, Sandbox


def setUpModule():
    # These tests drive the util-linux `mountpoint` path (mocked or real);
    # pin the Linux backend so they also run on macOS. tests/test_macos.py
    # covers the Darwin backend.
    global _platform
    _platform = patch("sambinha.mounts._PLATFORM", "linux")
    _platform.start()


def tearDownModule():
    _platform.stop()


class MountUnitTests(Sandbox):
    def test_same_nonempty_strategy_and_subdirectories(self):
        volume = self.root / 'volume'
        volume.mkdir()
        source = volume / 'documents'
        source.mkdir()
        runner = Mock()
        job = replace(self.config.jobs[0], source=source, source_mount=volume)
        check_mounts(job, runner)
        runner.run.assert_called_once_with(['mountpoint', '-q', str(volume)])
        # Empty subdirectories remain legitimate: mounted_nonempty applies to
        # the selected volume, just like the original shell helper.
        self.assertEqual(list(source.iterdir()), [])

    def test_missing_empty_unmounted_and_wrong_location_are_refused(self):
        volume = self.root / 'volume'
        volume.mkdir()
        job = replace(self.config.jobs[0], source=volume, source_mount=volume)
        with self.assertRaises(BackupError):
            check_mounts(job, Mock())
        (volume / 'entry').touch()
        runner = Mock()
        runner.run.side_effect = BackupError('not mounted')
        with self.assertRaises(BackupError):
            check_mounts(job, runner)
        with self.assertRaises(BackupError):
            check_mounts(replace(job, source_mount=self.root / 'missing'), Mock())
        with self.assertRaises(BackupError):
            check_mounts(replace(job, source=self.source), Mock())

    def test_destination_volume_can_have_new_empty_backup_subdirectory(self):
        volume = self.root / 'volume'
        volume.mkdir()
        (volume / 'existing').mkdir()
        job = replace(self.config.jobs[0], destination=str(volume / 'new-backup'), destination_mount=volume)
        check_mounts(job, Mock())
        self.assertFalse((volume / 'new-backup').exists())

    def test_optional_for_regular_folders_and_s3_has_no_local_destination_mount(self):
        runner = Mock()
        check_mounts(self.config.jobs[0], runner)
        runner.run.assert_not_called()
        bad = replace(self.config.jobs[0], type='s3', destination='aws:bucket', destination_mount=self.root)
        with self.assertRaises(ConfigError):
            validate(replace(self.config, jobs=(bad,)))

    def test_config_mount_paths_relative_and_override(self):
        config = self.root / 'config.toml'
        config.write_text('[[jobs]]\nname="test"\nsource="source"\ndestination="destination"\n'
                          'source_mount="."\ndestination_mount="."\n')
        loaded = load(config)
        self.assertEqual(loaded.jobs[0].source_mount, self.root)
        self.assertEqual(loaded.jobs[0].destination_mount, self.root)
        self.assertIsNone(load(config, {'source_mount': ''}).jobs[0].source_mount)


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
