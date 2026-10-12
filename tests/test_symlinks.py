from dataclasses import replace
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from sambinha.errors import BackupError
from sambinha.inventory import scan, verify
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
                    state = json.loads((config.state_dir / 'test/state.json').read_text())
                    names = (set(state['active']) if logic == 'simple' else
                             {f['path'] for c in state['active'].values() for f in c['files']})
                    self.assertEqual(names, {'real', 'dir/file'})
                    destination = Path(job.destination)
                    self.assertFalse(any(p.is_symlink() or p.name.endswith('.rclonelink')
                                         for p in destination.rglob('*')))
                    if logic == 'simple':
                        self.assertTrue((destination / 'empty').is_dir())

    def test_source_links_protect_previous_copies_batches_and_retired_history(self):
        for logic in ('simple', 'batch'):
            with self.subTest(logic=logic):
                self.file('dir/file', b'old')
                self.file('other', b'keep')
                job = self.job(logic)
                config = replace(self.config, jobs=(job,), state_dir=self.root / ('state-' + logic),
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
                (self.source / 'dir').unlink()

    def test_file_replaced_by_link_protects_expired_history(self):
        original = self.file('file', b'old')
        self.file('keep', b'keep')
        self.assertEqual(self.execute(), 0)
        original.write_bytes(b'changed')
        self.assertEqual(self.execute(now=1100), 0)
        before = {p.relative_to(self.destination): p.read_bytes()
                  for p in self.destination.rglob('*') if p.is_file()}
        original.unlink()
        original.symlink_to(self.root / 'missing')
        self.assertEqual(self.execute(now=1000000), 0, self.output.getvalue())
        for path, value in before.items():
            self.assertEqual((self.destination / path).read_bytes(), value)
        self.assertTrue(self.state()['retired'])
        self.assertFalse(any(e['event'] == 'expire' for e in self.events))

    def test_destination_links_and_colliding_source_paths_are_ignored(self):
        self.file('dir/file', b'new')
        self.file('file', b'new')
        self.file('safe', b'copy')
        self.destination.mkdir()
        outside = self.root / 'outside'
        outside.mkdir()
        (outside / 'file').write_bytes(b'untouched')
        (self.destination / 'dir').symlink_to(outside, target_is_directory=True)
        (self.destination / 'file').symlink_to(outside / 'file')
        self.assertEqual(self.execute(), 0, self.output.getvalue())
        self.assertEqual((self.destination / 'safe').read_bytes(), b'copy')
        self.assertEqual((outside / 'file').read_bytes(), b'untouched')
        self.assertTrue((self.destination / 'dir').is_symlink())
        self.assertTrue((self.destination / 'file').is_symlink())
        self.assertEqual(set(self.state()['active']), {'safe'})

    def test_destination_link_created_after_inventory_cannot_redirect_bulk_writes(self):
        self.file('dir/one')
        self.file('dir/two')
        files = scan(self.source)
        self.destination.mkdir()
        outside = self.root / 'outside'
        outside.mkdir()
        (self.destination / 'dir').symlink_to(outside, target_is_directory=True)
        storage = Storage(self.config.jobs[0], self.config, None)
        with self.assertRaisesRegex(BackupError, 'dir/'):
            storage.put_many(self.source, files)
        self.assertEqual(list(outside.iterdir()), [])
