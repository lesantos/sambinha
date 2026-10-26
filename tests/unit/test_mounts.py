from dataclasses import replace
from unittest.mock import Mock

from sambinha.config import load
from sambinha.config_validation import validate
from sambinha.errors import BackupError, ConfigError
from sambinha.mounts import check_mounts
from tests.helpers import Sandbox


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
