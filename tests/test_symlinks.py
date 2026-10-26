import os
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from sambinha.errors import BackupError
from sambinha.inventory import scan, verify
from sambinha.local_paths import child_directory, prepare_write
from sambinha.process import Runner
from sambinha.storage import Storage
from tests.helpers import REAL_TOOLS, Sandbox, contents


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
            with self.subTest(mode=mode), self.assertRaisesRegex(BackupError, 'dir/file'):
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


@unittest.skipUnless(REAL_TOOLS, 'rclone and 7z required')
class SymlinkIntegrationTests(Sandbox):
    def job(self, logic):
        return replace(self.config.jobs[0], logic=logic, destination=str(self.root / ('backup-' + logic)))

    def test_real_backups_ignore_links_even_if_rclone_environment_enables_following(self):
        self.file('real', b'payload')
        self.file('dir/file', b'other')
        (self.source / 'empty').mkdir()
        outside = self.root / 'outside'
        outside.mkdir()
        (outside / 'secret').write_bytes(b'external')
        (self.source / 'file-link').symlink_to(outside / 'secret')
        (self.source / 'dir-link').symlink_to(outside, target_is_directory=True)
        (self.source / 'cycle').symlink_to(self.source, target_is_directory=True)
        (self.source / 'broken').symlink_to(outside / 'missing')
        environment = {'RCLONE_LOCAL_COPY_LINKS': 'true', 'RCLONE_COPY_LINKS': 'true',
                       'RCLONE_LOCAL_LINKS': 'true', 'RCLONE_LINKS': 'true'}
        for logic in ('simple', 'batch'):
            for mode in ('simple', 'hash'):
                with self.subTest(logic=logic, mode=mode):
                    job = replace(self.job(logic), destination=str(self.root / f'backup-{logic}-{mode}'))
                    config = replace(self.config, jobs=(job,), verification_mode=mode,
                                     state_dir=self.root / f'state-{logic}-{mode}')
                    with patch.dict(os.environ, environment):
                        self.assertEqual(self.execute(config), 0, self.output.getvalue())
                    import json
                    if logic == 'simple':
                        names = set(contents(Path(job.destination)))
                    else:
                        state = json.loads((config.state_dir / 'test/state.json').read_text())
                        names = {f['path'] for c in state['active'].values() for f in c['files']}
                    self.assertEqual(names, {'real', 'dir/file'})
                    destination = Path(job.destination)
                    self.assertFalse(any(p.is_symlink() or p.name.endswith('.rclonelink')
                                         for p in destination.rglob('*')))
                    if logic == 'simple':
                        self.assertTrue((destination / 'empty').is_dir())

    def test_source_links_protect_previous_copies_batches_and_retired_history(self):
        self.file('dir/file', b'old')
        self.file('other', b'keep')
        job = self.job('batch')
        config = replace(self.config, jobs=(job,), state_dir=self.root / 'state-batch',
                         deleted_days=0, version_days=0)
        self.assertEqual(self.execute(config), 0)
        destination = Path(job.destination)
        before = contents(destination)
        import shutil
        shutil.rmtree(self.source / 'dir')
        (self.source / 'dir').symlink_to(self.root / 'missing', target_is_directory=True)
        self.assertEqual(self.execute(config, now=100000), 0, self.output.getvalue())
        for key, value in before.items():
            self.assertEqual((destination / key).read_bytes(), value)
        self.assertFalse(any(e['event'] in ('preserve', 'expire') for e in self.events))

    def test_file_replaced_by_link_is_retired_and_old_simple_history_expires(self):
        original = self.file('file', b'old')
        self.file('keep', b'keep')
        self.assertEqual(self.execute(), 0)
        original.write_bytes(b'changed')
        self.assertEqual(self.execute(now=1100), 0)
        histories = list((self.destination / '.sambinha/history').iterdir())
        self.assertEqual(len(histories), 1)
        old = histories[0] / 'file'
        self.assertEqual(old.read_bytes(), b'old')
        original.unlink()
        original.symlink_to(self.root / 'missing')
        self.assertEqual(self.execute(now=1000000), 0, self.output.getvalue())
        self.assertFalse((self.destination / 'file').exists())
        self.assertFalse(histories[0].exists())
        latest = list((self.destination / '.sambinha/history').iterdir())
        self.assertEqual(len(latest), 1)
        self.assertEqual((latest[0] / 'file').read_bytes(), b'changed')
        self.assertTrue(any(e['event'] == 'expire_history' for e in self.events))

    def test_destination_links_are_replaced_only_on_source_conflict(self):
        self.file('dir/file', b'new')
        self.file('file', b'new')
        self.file('safe', b'copy')
        self.file('broken-file', b'new')
        self.file('broken-dir/file', b'new')
        self.file('file-over-dir', b'new')
        self.file('dir-over-file/file', b'new')
        self.destination.mkdir()
        outside = self.root / 'outside'
        outside.mkdir()
        (outside / 'file').write_bytes(b'untouched')
        (self.destination / 'dir').symlink_to(outside, target_is_directory=True)
        (self.destination / 'file').symlink_to(outside / 'file')
        (self.destination / 'broken-file').symlink_to(self.root / 'missing')
        (self.destination / 'broken-dir').symlink_to(self.root / 'missing', target_is_directory=True)
        (self.destination / 'file-over-dir').symlink_to(outside, target_is_directory=True)
        (self.destination / 'dir-over-file').symlink_to(outside / 'file')
        (self.destination / 'unused').symlink_to(outside, target_is_directory=True)
        (self.destination / 'unused-broken').symlink_to(self.root / 'missing')
        (self.source / 'source-link').symlink_to(outside / 'file')
        (self.destination / 'source-link').symlink_to(outside / 'file')
        self.assertEqual(self.execute(), 0, self.output.getvalue())
        for path in ('dir/file', 'file', 'broken-file', 'broken-dir/file',
                     'file-over-dir', 'dir-over-file/file'):
            self.assertEqual((self.destination / path).read_bytes(), b'new')
        self.assertEqual((self.destination / 'safe').read_bytes(), b'copy')
        self.assertEqual((outside / 'file').read_bytes(), b'untouched')
        self.assertEqual(set(outside.iterdir()), {outside / 'file'})
        self.assertFalse((self.destination / 'dir').is_symlink())
        self.assertFalse((self.destination / 'file').is_symlink())
        for name in ('unused', 'unused-broken', 'source-link'):
            self.assertTrue((self.destination / name).is_symlink())

    def test_destination_link_created_after_inventory_is_replaced_before_writes(self):
        self.file('dir/one')
        self.file('dir/two')
        files = scan(self.source)
        self.destination.mkdir()
        outside = self.root / 'outside'
        outside.mkdir()
        (self.destination / 'dir').symlink_to(outside, target_is_directory=True)
        storage = Storage(self.config.jobs[0], self.config, Runner())
        for item in files:
            storage.put(self.source / item.path, item.path)
        self.assertFalse((self.destination / 'dir').is_symlink())
        self.assertEqual(contents(self.destination), contents(self.source))
        self.assertEqual(list(outside.iterdir()), [])

    def test_excluded_and_simulated_destination_links_are_preserved(self):
        self.file('included/file')
        self.file('excluded/file')
        self.file('excluded-file')
        (self.source / 'empty').mkdir()
        self.destination.mkdir()
        outside = self.root / 'outside'
        outside.mkdir()
        for name in ('included', 'excluded', 'empty'):
            (self.destination / name).symlink_to(outside, target_is_directory=True)
        (self.destination / 'excluded-file').symlink_to(self.root / 'missing')
        rules = self.root / 'rules'
        rules.write_text('/excluded/**\n/excluded-file\n')
        config = replace(self.config, jobs=(replace(self.config.jobs[0], exclude_files=rules),))
        self.assertEqual(self.execute(replace(config, dry_run=True)), 0, self.output.getvalue())
        self.assertTrue(all((self.destination / name).is_symlink()
                            for name in ('included', 'excluded', 'excluded-file', 'empty')))
        self.assertEqual(self.execute(config), 0, self.output.getvalue())
        self.assertFalse((self.destination / 'included').is_symlink())
        self.assertFalse((self.destination / 'empty').is_symlink())
        self.assertTrue((self.destination / 'excluded').is_symlink())
        self.assertTrue((self.destination / 'excluded-file').is_symlink())
        self.assertEqual(list(outside.iterdir()), [])

    def test_batch_replaces_conflicting_directory_and_rebuilds_linked_archives(self):
        self.file('dir/file', b'payload')
        self.destination.mkdir()
        outside = self.root / 'outside'
        outside.mkdir()
        sentinel = outside / 'sentinel'
        sentinel.write_bytes(b'untouched')
        (self.destination / 'dir').symlink_to(outside, target_is_directory=True)
        (self.destination / 'unused').symlink_to(outside, target_is_directory=True)
        config = replace(self.config, jobs=(replace(self.config.jobs[0], logic='batch'),))
        self.assertEqual(self.execute(config), 0, self.output.getvalue())
        self.assertFalse((self.destination / 'dir').is_symlink())
        state = self.state()
        key = next(iter(state['active']))
        archive = self.destination / key
        saved = archive.read_bytes()
        archive.unlink()
        archive.symlink_to(sentinel)
        control = self.destination / (key + '.json')
        control.unlink()
        control.symlink_to(sentinel)
        self.assertEqual(self.execute(config), 0, self.output.getvalue())
        self.assertFalse(archive.is_symlink())
        self.assertFalse(control.is_symlink())
        self.assertEqual(archive.read_bytes(), saved)
        self.assertTrue((self.destination / 'unused').is_symlink())
        self.assertEqual(sentinel.read_bytes(), b'untouched')
        self.assertEqual(set(outside.iterdir()), {sentinel})

    def test_batch_expiry_ignores_nonconflicting_history_link(self):
        source = self.file('file', b'first')
        config = replace(self.config, jobs=(replace(self.config.jobs[0], logic='batch'),), version_days=0)
        self.assertEqual(self.execute(config), 0, self.output.getvalue())
        key = next(iter(self.state()['active']))
        archive = self.destination / key
        outside = self.root / 'outside'
        outside.write_bytes(b'untouched')
        archive.unlink()
        archive.symlink_to(outside)
        source.write_bytes(b'changed')
        self.assertEqual(self.execute(config, now=1100), 0, self.output.getvalue())
        self.assertTrue(archive.is_symlink())
        self.assertEqual(outside.read_bytes(), b'untouched')
        self.assertNotIn(key, self.state()['active'])
