from dataclasses import replace
from unittest.mock import patch

from sambinha.errors import BackupError
from sambinha.inventory import scan, verify
from sambinha.local_paths import child_directory, prepare_write
from tests.helpers import Sandbox


class SymlinkInventoryTests(Sandbox):
    def test_links_to_files_directories_cycles_and_missing_targets_are_ignored(self):
        self.file('real', b'payload')
        outside = self.root / 'outside'
        outside.mkdir()
        (outside / 'private').write_bytes(b'outside')
        (self.source / 'file-link').symlink_to(outside / 'private')
        (self.source / 'dir-link').symlink_to(outside, target_is_directory=True)
        (self.source / 'cycle').symlink_to(self.source, target_is_directory=True)
        (self.source / 'broken').symlink_to(self.root / 'missing')
        for mode in ('simple', 'hash'):
            with self.subTest(mode=mode):
                self.assertEqual([f.path for f in scan(self.source, mode=mode)], ['real'])

    def test_directory_replaced_by_link_after_inventory_is_rejected_without_reading_target(self):
        self.file('dir/file', b'original')
        files = scan(self.source)
        (self.source / 'dir').rename(self.root / 'original')
        (self.source / 'dir').symlink_to(self.root / 'original', target_is_directory=True)
        for mode in ('simple', 'hash'):
            with self.subTest(mode=mode), self.assertRaisesRegex(BackupError, 'link simbólico: dir'):
                verify(self.source, files, mode=mode)

    def test_destination_root_and_metadata_links_are_rejected_without_touching_targets(self):
        outside = self.root / 'outside'
        outside.mkdir()
        self.destination.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(BackupError):
            prepare_write(self.destination, 'file')
        self.destination.unlink()
        self.destination.mkdir()
        (self.destination / '.sambinha').symlink_to(outside, target_is_directory=True)
        with self.assertRaises(BackupError):
            prepare_write(self.destination, '.sambinha/control.tar.gz')
        self.assertTrue((self.destination / '.sambinha').is_symlink())
        self.assertEqual(list(outside.iterdir()), [])

    def test_missing_destination_below_a_link_is_rejected(self):
        outside = self.root / 'outside'
        outside.mkdir()
        linked = self.root / 'parent-link'
        linked.symlink_to(outside, target_is_directory=True)
        config = replace(self.config, jobs=(replace(self.config.jobs[0],
                         destination=str(linked / 'backup')),))
        self.file('file')
        self.assertEqual(self.execute(config), 1, self.output.getvalue())
        self.assertEqual(list(outside.iterdir()), [])

    def test_directory_changed_to_link_during_preparation_is_not_followed(self):
        self.destination.mkdir()
        (self.destination / 'dir').mkdir()
        outside = self.root / 'outside'
        outside.mkdir()
        def changed_directory(parent, name, **kwargs):
            if name == 'dir':
                (self.destination / 'dir').rename(self.root / 'original-dir')
                (self.destination / 'dir').symlink_to(outside, target_is_directory=True)
            return child_directory(parent, name, **kwargs)
        with patch('sambinha.local_paths.child_directory', side_effect=changed_directory), \
             self.assertRaises(BackupError):
            prepare_write(self.destination, 'dir/file')
        self.assertEqual(list(outside.iterdir()), [])
