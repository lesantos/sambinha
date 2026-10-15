"""Single-pass inventories, stable ordering and descriptor-anchored race guards."""
from dataclasses import replace
import os
import unittest
from unittest.mock import patch

from sambinha.discovery import Discovery
from sambinha.errors import BackupError
from sambinha.filtering import Selection
from sambinha.inventory import fingerprint, metadata, scan
from sambinha.preflight import _read_inventory
from sambinha.process import Runner
from tests.helpers import REAL_TOOLS, Sandbox


class InventoryTests(Sandbox):
    def test_walk_inspects_files_before_listing_the_next_directory(self):
        first = self.file('first/a')
        self.file('second/b')
        os.utime(first.parent, ns=(10, 10))
        os.utime(self.source / 'second', ns=(20, 20))
        calls = []
        def listing(prefix):
            calls.append(prefix)
            if prefix == 'second':
                self.assertEqual(inspected, ['first/a'])
            with os.scandir(self.source / prefix) as entries:
                return [{'Path': (prefix + '/' if prefix else '') + e.name,
                         'IsDir': e.is_dir(follow_symlinks=False)} for e in entries]
        inspected = []
        def record(root, relative, **kwargs):
            inspected.append(relative)
            return metadata(root, relative, **kwargs)
        with patch('sambinha.inventory.metadata', side_effect=record):
            files = scan(self.source, mode='simple', listing=listing)
        self.assertEqual(calls, ['', 'first', 'second'])
        self.assertEqual([f.path for f in files], ['first/a', 'second/b'])

    def test_directory_swapped_for_symlink_is_never_followed(self):
        self.file('dir/file', b'original')
        outside = self.root / 'outside'
        outside.mkdir()
        (outside / 'secret').write_bytes(b'external')
        original = os.open
        def swapped(path, flags, *args, **kwargs):
            if path == 'dir':
                (self.source / 'dir').rename(self.root / 'original')
                (self.source / 'dir').symlink_to(outside, target_is_directory=True)
            return original(path, flags, *args, **kwargs)
        with patch('sambinha.inventory.os.open', side_effect=swapped), \
             patch('sambinha.inventory.fingerprint', side_effect=AssertionError('external content')), \
             self.assertRaises(BackupError):
            scan(self.source)

    def test_replacing_an_open_ancestor_does_not_redirect_reads(self):
        self.file('dir/file', b'original')
        outside = self.root / 'outside'
        outside.mkdir()
        (outside / 'file').write_bytes(b'external')
        def swapped(root, relative, **kwargs):
            (self.source / 'dir').rename(self.root / 'original')
            (self.source / 'dir').symlink_to(outside, target_is_directory=True)
            actual = fingerprint(root, relative, **kwargs)
            expected = fingerprint(self.root / 'original', 'file')
            self.assertEqual(actual.sha256, expected.sha256)
            return actual
        with patch('sambinha.inventory.fingerprint', side_effect=swapped), \
             self.assertRaisesRegex(BackupError, 'Diretório mudou'):
            scan(self.source)

    def test_file_swapped_to_link_after_listing_is_never_opened(self):
        path = self.file('file')
        outside = self.root / 'outside'
        outside.write_bytes(b'external')
        def swapped(root, relative, **kwargs):
            path.unlink()
            path.symlink_to(outside)
            return fingerprint(root, relative, **kwargs)
        with patch('sambinha.inventory.fingerprint', side_effect=swapped), self.assertRaises(BackupError):
            scan(self.source)

    def test_hash_cache_reuses_unchanged_files_but_detects_replacements(self):
        path = self.file('dir/file', b'original')
        cache = {}
        expected = scan(self.source, cache_out=cache)
        with patch('sambinha.inventory.fingerprint', side_effect=AssertionError('unneeded read')):
            self.assertEqual(scan(self.source, cache=cache), expected)
        path.write_bytes(b'changed!')
        os.utime(path, ns=(expected[0].mtime_ns, expected[0].mtime_ns))
        self.assertNotEqual(scan(self.source, cache=cache)[0].sha256, expected[0].sha256)

    def test_no_per_file_ancestor_checks_and_no_content_opens_in_simple_mode(self):
        for i in range(25):
            self.file(f'a/b/c/{i}')
        with patch('sambinha.inventory.path_has_symlink', side_effect=AssertionError('ancestor rescan')), \
             patch('sambinha.inventory.os.open', wraps=os.open) as opened:
            self.assertEqual(len(scan(self.source, mode='simple')), 25)
        self.assertTrue(all(call.args[1] & os.O_DIRECTORY for call in opened.call_args_list))


@unittest.skipUnless(REAL_TOOLS, 'rclone and 7z required')
class FilteredInventoryTests(Sandbox):
    def rules(self):
        path = self.root / 'exclude.txt'
        path.write_text('*.tmp\n/cache/**\n/root.txt\nIcon[\\x0d␍]\n')
        return path

    def test_fused_inventory_matches_selection_order_metadata_and_cached_fingerprints(self):
        rules = self.rules()
        for name in ('old/recent', 'root.txt', 'a/root.txt', 'cache/private', 'a/skip.tmp',
                     'ação.txt', 'a/[literal]*?.txt', 'a/Icon\r', 'new/ancient'):
            self.file(name)
        (self.source / 'empty').mkdir()
        (self.source / 'link').symlink_to(self.source / 'old', target_is_directory=True)
        for name, stamp in (('old', 10), ('ação.txt', 20), ('a', 30), ('new', 40)):
            os.utime(self.source / name, ns=(stamp, stamp))
        selection = Selection(rules, self.config, Runner())
        selected, directories = selection.source(self.source)
        for mode in ('simple', 'hash'):
            with self.subTest(mode=mode):
                expected_cache, actual_cache = {}, {}
                expected = scan(self.source, selected, directories, mode=mode, cache_out=expected_cache)
                actual_dirs = set()
                with selection.source_listing(self.source) as listing:
                    actual = scan(self.source, mode=mode, listing=listing,
                                  selected_dirs_out=actual_dirs, cache_out=actual_cache)
                self.assertEqual(actual, expected)
                self.assertEqual(actual_dirs, directories)
                self.assertEqual(actual_cache, expected_cache)
                self.assertNotIn('empty', actual_dirs)

    def test_preflight_combines_discovery_and_metadata_without_second_local_walk(self):
        self.file('a/keep')
        self.file('a/skip.tmp')
        self.file('cache/private')
        job = replace(self.config.jobs[0], logic='batch', exclude_files=self.rules())
        visited = []
        original = Discovery.listing
        def listing(server, relative):
            visited.append(relative)
            return original(server, relative)
        original_scandir = os.scandir
        source_inodes = {(p.stat().st_dev, p.stat().st_ino) for p in (self.source, self.source / 'a')}
        def scandir(path):
            info = os.fstat(path) if isinstance(path, int) else os.stat(path)
            if (info.st_dev, info.st_ino) in source_inodes:
                raise AssertionError('second local walk')
            return original_scandir(path)
        with patch('sambinha.preflight.backup_state', return_value={}), \
             patch.object(Selection, 'source', side_effect=AssertionError('separate selection walk')), \
             patch('sambinha.inventory.os.scandir', side_effect=scandir), \
             patch.object(Discovery, 'listing', listing):
            inventory = _read_inventory(self.config, job, Runner(), 1000, None)
        self.assertEqual([f.path for f in inventory.files], ['a/keep'])
        self.assertEqual(inventory.selected_dirs, {'a'})
        self.assertEqual(visited, ['', 'a'])

    def test_empty_or_all_excluded_source_and_reserved_metadata(self):
        rules = self.rules()
        self.file('a/skip.tmp')
        selection = Selection(rules, self.config, Runner())
        directories = set()
        with selection.source_listing(self.source) as listing:
            self.assertEqual(scan(self.source, listing=listing, selected_dirs_out=directories), [])
        self.assertEqual(directories, set())
        self.file('.sambinha/private')
        with selection.source_listing(self.source) as listing, self.assertRaisesRegex(BackupError, 'reservado'):
            scan(self.source, listing=listing)

    def test_failure_in_later_directory_does_not_publish_partial_backup(self):
        self.file('first/a')
        self.file('second/b')
        os.utime(self.source / 'first', ns=(10, 10))
        os.utime(self.source / 'second', ns=(20, 20))
        job = replace(self.config.jobs[0], logic='batch', exclude_files=self.rules())
        self.config = replace(self.config, jobs=(job,))
        original = Discovery.listing
        def listing(server, relative):
            if relative == 'second':
                raise BackupError('later directory failed')
            return original(server, relative)
        with patch.object(Discovery, 'listing', listing):
            self.assertEqual(self.execute(), 1)
        self.assertFalse((self.config.state_dir / 'test/state.json').exists())
        self.assertFalse(self.destination.exists())
