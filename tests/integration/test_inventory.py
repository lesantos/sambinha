"""Single-pass inventories, stable ordering and descriptor-anchored race guards."""


import os
import unittest
from dataclasses import replace
from unittest.mock import patch

from sambinha.discovery import Discovery
from sambinha.errors import BackupError
from sambinha.filtering import Selection
from sambinha.inventory import scan
from sambinha.preflight import _read_inventory
from sambinha.process import Runner
from tests.helpers import REAL_TOOLS, Sandbox


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
