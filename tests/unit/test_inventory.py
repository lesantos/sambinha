"""Single-pass inventories, stable ordering and descriptor-anchored race guards."""


import hashlib
import os
import threading
import zlib
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sambinha import inventory
from sambinha.errors import BackupError
from sambinha.inventory import fingerprint, metadata, scan
from tests.helpers import Sandbox, tick


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
        tick()
        path.write_bytes(b'changed!')
        os.utime(path, ns=(expected[0].mtime_ns, expected[0].mtime_ns))
        self.assertNotEqual(scan(self.source, cache=cache)[0].sha256, expected[0].sha256)

    def test_no_per_file_ancestor_checks_and_no_content_opens_in_simple_mode(self):
        for i in range(25):
            self.file(f'a/b/c/{i}')
        with patch('sambinha.inventory.path_has_symlink', side_effect=AssertionError('ancestor rescan')), \
             patch('sambinha.inventory.os.read', side_effect=AssertionError('content read')), \
             patch('sambinha.inventory.os.open', wraps=os.open) as opened:
            self.assertEqual(len(scan(self.source, mode='simple')), 25)
        self.assertTrue(all(call.args[1] & (os.O_DIRECTORY | inventory._METADATA_FLAGS) for call in opened.call_args_list))


def altered(info, **changes):
    fields = ('st_dev', 'st_ino', 'st_mode', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
    return SimpleNamespace(**{**{field: getattr(info, field) for field in fields}, **changes})


class VolatileInodeTests(Sandbox):
    def listing(self, prefix):
        with os.scandir(self.source / prefix) as entries:
            return [{'Path': (prefix + '/' if prefix else '') + entry.name,
                     'IsDir': entry.is_dir(follow_symlinks=False)} for entry in entries]

    def stale_listing_inodes(self):
        original = os.stat
        seen = set()
        def observe(path, *args, **kwargs):
            info = original(path, *args, **kwargs)
            parent = kwargs.get('dir_fd')
            key = (parent, str(path))
            if parent is not None and key not in seen:
                seen.add(key)
                return altered(info, st_ino=info.st_ino + 10000000)
            return info
        return observe

    def test_historical_directory_and_file_inodes_can_change_on_fat_without_content_changes(self):
        self.file('lu/first', b'one')
        self.file('lu/second', b'two')
        for mode in ('simple', 'hash'):
            with self.subTest(mode=mode):
                expected = scan(self.source, mode=mode)
                cache = {}
                with patch('sambinha.inventory.volatile_inode_devices', return_value={self.source.stat().st_dev}), \
                     patch('sambinha.inventory.os.stat', side_effect=self.stale_listing_inodes()):
                    actual = scan(self.source, listing=self.listing, mode=mode, cache_out=cache)
                self.assertEqual(actual, expected)
                for item in actual:
                    self.assertEqual(cache[item.path]['signature'][1], (self.source / item.path).stat().st_ino)

    def test_historical_inode_changes_still_fail_on_other_or_unknown_filesystems(self):
        self.file('dir/file')
        for path in ('dir', 'file'):
            with self.subTest(path=path), \
                 patch('sambinha.inventory.volatile_inode_devices', return_value=frozenset()), \
                 patch('sambinha.inventory.os.stat', side_effect=self.stale_listing_inodes()), \
                 self.assertRaisesRegex(BackupError, '"stage": "open"'):
                if path == 'dir':
                    scan(self.source, listing=self.listing)
                else:
                    info = (self.source / 'dir/file').stat()
                    metadata(self.source / 'dir', 'file', before=altered(info, st_ino=info.st_ino + 1))

    def test_standalone_metadata_and_hash_use_the_opened_file_as_baseline(self):
        path = self.file('file', b'content')
        original = os.stat
        for inspect in (metadata, fingerprint):
            with self.subTest(inspect=inspect.__name__):
                calls = [0]
                def observe(name, *args, **kwargs):
                    info = original(name, *args, **kwargs)
                    if Path(name) == path and 'dir_fd' in kwargs:
                        calls[0] += 1
                        if calls[0] == 1:
                            return altered(info, st_ino=info.st_ino + 10000000)
                    return info
                expected = inspect(self.source, 'file')
                with patch('sambinha.inventory.volatile_inode_devices', return_value={path.stat().st_dev}), \
                     patch('sambinha.inventory.os.stat', side_effect=observe):
                    self.assertEqual(inspect(self.source, 'file'), expected)

    def test_fat_policy_never_ignores_other_metadata_changes(self):
        path = self.file('file')
        before = path.stat()
        from sambinha.inventory import _same_observation
        changes = ({'st_dev': before.st_dev + 1}, {'st_size': before.st_size + 1},
                   {'st_mode': before.st_mode ^ 0o100}, {'st_mtime_ns': before.st_mtime_ns + 1},
                   {'st_ctime_ns': before.st_ctime_ns + 1}, {'st_mode': 0o120777})
        with patch('sambinha.inventory.volatile_inode_devices', return_value={before.st_dev}):
            for change in changes:
                with self.subTest(change=change):
                    after = altered(before, st_ino=before.st_ino + 1, **change)
                    self.assertFalse(_same_observation(before, after, historical=True))

    def test_replaced_open_directory_is_rejected_even_on_fat(self):
        self.file('dir/file', b'original')
        def replaced(root, relative, **kwargs):
            (self.source / 'dir').rename(self.root / 'old-dir')
            (self.source / 'dir').mkdir()
            return metadata(root, relative, **kwargs)
        with patch('sambinha.inventory.volatile_inode_devices', return_value={self.source.stat().st_dev}), \
             patch('sambinha.inventory.metadata', side_effect=replaced), \
             self.assertRaisesRegex(BackupError, 'Diretório mudou.*"stage": "path"'):
            scan(self.source, mode='simple')

    def test_replaced_open_file_is_rejected_even_with_same_size_and_mtime(self):
        path = self.file('file', b'old')
        original = path.stat()
        def replaced(root, relative, **kwargs):
            path.rename(self.root / 'old-file')
            path.write_bytes(b'new')
            path.chmod(original.st_mode & 0o777)
            os.utime(path, ns=(original.st_mtime_ns, original.st_mtime_ns))
            return metadata(root, relative, **kwargs)
        with patch('sambinha.inventory.volatile_inode_devices', return_value={original.st_dev}), \
             patch('sambinha.inventory.metadata', side_effect=replaced), \
             self.assertRaisesRegex(BackupError, 'Arquivo mudou.*"stage": "(descriptor|path)"'):
            scan(self.source, mode='simple')

    def test_inode_change_while_file_is_pinned_is_never_ignored(self):
        path = self.file('file')
        original = os.stat
        calls = [0]
        def observe(name, *args, **kwargs):
            info = original(name, *args, **kwargs)
            if Path(name) == path and 'dir_fd' in kwargs:
                calls[0] += 1
                if calls[0] == 2:
                    return altered(info, st_ino=info.st_ino + 1)
            return info
        with patch('sambinha.inventory.volatile_inode_devices', return_value={path.stat().st_dev}), \
             patch('sambinha.inventory.os.stat', side_effect=observe), \
             self.assertRaisesRegex(BackupError, '"stage": "path"'):
            metadata(self.source, 'file')

    def test_files_and_directories_are_closed_when_inspection_fails(self):
        self.file('dir/file')
        original = os.open
        descriptors = set()
        def opened(*args, **kwargs):
            fd = original(*args, **kwargs)
            descriptors.add(fd)
            return fd
        with patch('sambinha.inventory.os.open', side_effect=opened), \
             patch('sambinha.inventory.metadata', side_effect=BackupError('injected')), \
             self.assertRaises(BackupError):
            scan(self.source, mode='simple')
        for fd in descriptors:
            with self.assertRaises(OSError):
                os.fstat(fd)

    def test_metadata_edit_during_read_is_rejected_with_before_and_after_values(self):
        path = self.file('file', b'old')
        def modified(root, relative, **kwargs):
            item = metadata(root, relative, **kwargs)
            tick()
            path.write_bytes(b'changed size')
            return item
        with patch('sambinha.inventory.metadata', side_effect=modified), \
             self.assertRaisesRegex(BackupError, '"after".*"before".*"stage": "descriptor"'):
            scan(self.source, mode='simple')

    def test_metadata_edit_during_read_retries_and_accepts_stable_latest_version(self):
        path = self.file('file', b'old')
        calls = 0
        def modified(root, relative, **kwargs):
            nonlocal calls
            item = metadata(root, relative, **kwargs)
            if calls == 0:
                path.write_bytes(b'latest')
            calls += 1
            return item
        with patch('sambinha.inventory.metadata', side_effect=modified):
            result = scan(self.source, mode='simple')
        self.assertEqual((result[0].size, path.stat().st_size, calls), (6, 6, 2))

    def test_disabled_source_change_check_skips_race_validation(self):
        path = self.file('file', b'old')
        def modified(root, relative, **kwargs):
            item = metadata(root, relative, **kwargs)
            path.write_bytes(b'changed size')
            return item
        with patch('sambinha.inventory.metadata', side_effect=modified):
            result = scan(self.source, mode='simple', verify_source_changes=False)
        self.assertEqual(result[0].size, 3)

    def test_hash_cache_does_not_reuse_fingerprints_after_inode_renumbering(self):
        self.file('file', b'original')
        cache, new_cache = {}, {}
        expected = scan(self.source, cache_out=cache)
        original_stat, original_fstat = os.stat, os.fstat
        def renamed_stat(*args, **kwargs):
            info = original_stat(*args, **kwargs)
            return altered(info, st_ino=info.st_ino + 10000000)
        def renamed_fstat(*args, **kwargs):
            info = original_fstat(*args, **kwargs)
            return altered(info, st_ino=info.st_ino + 10000000)
        with patch('sambinha.inventory.os.stat', side_effect=renamed_stat), \
             patch('sambinha.inventory.os.fstat', side_effect=renamed_fstat), \
             patch('sambinha.inventory.fingerprint', wraps=fingerprint) as hashing:
            actual = scan(self.source, listing=self.listing, cache=cache, cache_out=new_cache)
        hashing.assert_called_once()
        self.assertEqual(actual, expected)
        self.assertNotEqual(new_cache['file']['signature'], cache['file']['signature'])


class VerifyWalkTests(Sandbox):
    """verify() walks real directories by descriptor and reuses scan signatures."""

    def inventory(self, mode='hash'):
        cache = {}
        return scan(self.source, mode=mode, cache_out=cache), cache

    def test_matching_signatures_read_no_contents_in_hash_mode(self):
        for i in range(5):
            self.file(f'a/b/f{i}', b'x' * (i + 1))
        files, cache = self.inventory()
        with patch('sambinha.inventory._fingerprint', side_effect=AssertionError('read')), \
             patch('sambinha.inventory.os.read', side_effect=AssertionError('read')):
            inventory.verify(self.source, files, mode='hash', signatures=cache)

    def test_without_signatures_hash_mode_rereads_everything(self):
        self.file('a/f', b'abc')
        files, _ = self.inventory()
        with patch('sambinha.inventory._fingerprint', wraps=inventory._fingerprint) as read:
            inventory.verify(self.source, files, mode='hash')
        self.assertEqual(read.call_count, 1)

    def test_content_change_with_same_size_and_mtime_is_detected_via_ctime(self):
        path = self.file('a/f', b'old')
        files, cache = self.inventory()
        before = path.stat()
        tick()
        path.write_bytes(b'new')
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        with self.assertRaisesRegex(BackupError, 'alterada durante backup'):
            inventory.verify(self.source, files, mode='hash', signatures=cache)

    def test_file_replaced_with_same_size_and_mtime_is_detected(self):
        path = self.file('a/f', b'old')
        files, cache = self.inventory()
        before = path.stat()
        path.rename(self.root / 'moved')
        path.write_bytes(b'new')
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        for signatures in (cache, None):
            with self.assertRaises(BackupError):
                inventory.verify(self.source, files, mode='hash', signatures=signatures)

    def test_simple_mode_detects_size_and_mtime_changes(self):
        path = self.file('a/f', b'old')
        files, _ = self.inventory('simple')
        inventory.verify(self.source, files, mode='simple')
        tick()
        path.write_bytes(b'longer')
        with self.assertRaisesRegex(BackupError, 'alterada durante backup'):
            inventory.verify(self.source, files, mode='simple')

    def test_directory_replaced_by_symlink_is_never_followed(self):
        self.file('a/b/f', b'data')
        files, cache = self.inventory()
        outside = self.root / 'outside'
        (outside / 'b').mkdir(parents=True)
        (outside / 'b/f').write_bytes(b'data')
        (self.source / 'a').rename(self.root / 'moved')
        (self.source / 'a').symlink_to(outside)
        for mode, signatures in (('hash', cache), ('hash', None), ('simple', None)):
            with self.assertRaisesRegex(BackupError, 'link simbólico'):
                inventory.verify(self.source, files, mode=mode, signatures=signatures)

    def test_nested_directory_replaced_by_another_directory_is_detected(self):
        self.file('a/b/f', b'data')
        files, cache = self.inventory()
        (self.source / 'a/b').rename(self.root / 'moved')
        tick()
        self.file('a/b/f', b'data')
        with self.assertRaises(BackupError):
            inventory.verify(self.source, files, mode='hash', signatures=cache)

    def test_directory_swapped_while_it_is_being_verified_is_detected(self):
        self.file('a/b/f', b'data')
        files, _cache = self.inventory()
        calls = []
        original = os.stat
        def swapping(path, *args, **kwargs):
            if path == 'f' and not calls:
                calls.append(path)
                (self.source / 'a/b').rename(self.root / 'moved')
                (self.source / 'a/b').mkdir()
            return original(path, *args, **kwargs)
        with patch('sambinha.inventory.os.stat', side_effect=swapping), \
             self.assertRaisesRegex(BackupError, 'Diretório mudou.*"stage": "path"'):
            inventory.verify(self.source, files, mode='simple')

    def test_no_per_file_ancestor_chain_remains(self):
        for i in range(30):
            self.file(f'a/b/c/{i}', b'x')
        files, cache = self.inventory()
        for mode, signatures in (('hash', cache), ('hash', None), ('simple', None)):
            items = files if mode == 'hash' else scan(self.source, mode='simple')
            with patch('sambinha.inventory.path_has_symlink', side_effect=AssertionError('ancestor chain')), \
                 patch('sambinha.inventory.os.lstat', side_effect=AssertionError('lstat')), \
                 patch('sambinha.inventory.os.stat', wraps=os.stat) as stats:
                inventory.verify(self.source, items, mode=mode, signatures=signatures)
            self.assertLessEqual(stats.call_count, 4 * len(items) + 10)

    def test_disabled_source_change_check_skips_verification(self):
        self.file('a/f')
        files, _ = self.inventory()
        (self.source / 'a/f').unlink()
        inventory.verify(self.source, files, verify_source_changes=False)


class HashingTests(Sandbox):
    def test_all_digests_match_hashlib_and_zlib_for_threaded_and_inline_sizes(self):
        for size in (0, 1, 1000, inventory.BLOCK_SIZE, inventory.THREADED_MD5_MIN + 12345):
            data = os.urandom(size)
            item = fingerprint(self.source, self.file(f'f{size}', data).name)
            self.assertEqual((item.sha256, item.md5, item.crc32), (
                hashlib.sha256(data).hexdigest(), hashlib.md5(data).hexdigest(), zlib.crc32(data)))

    def test_requested_subset_leaves_other_digests_empty(self):
        self.file('f', b'abc')
        item = fingerprint(self.source, 'f', digests={'md5'})
        self.assertEqual((item.sha256, item.md5, item.crc32),
                         ('', hashlib.md5(b'abc').hexdigest(), None))

    def test_crc32_is_memory_only_and_never_compared_or_persisted(self):
        self.file('f', b'abc')
        cache = {}
        (item,) = scan(self.source, cache_out=cache)
        self.assertEqual(item.crc32, zlib.crc32(b'abc'))
        self.assertNotIn('crc32', item.metadata())
        self.assertNotIn('crc32', cache['f']['file'])
        self.assertEqual(item, replace(item, crc32=None))
        (cached,) = scan(self.source, cache=cache)
        self.assertIsNone(cached.crc32)

    def test_read_errors_propagate_from_threaded_hashing_without_leaking_threads(self):
        data = os.urandom(inventory.THREADED_MD5_MIN + 5 * inventory.BLOCK_SIZE)
        self.file('big', data)
        baseline = threading.active_count()
        real_read = os.read
        reads = []
        def failing(fd, size):
            reads.append(size)
            if len(reads) == 6:
                raise OSError(5, 'injected I/O error')
            return real_read(fd, size)
        with patch('sambinha.inventory.os.read', side_effect=failing), self.assertRaisesRegex(OSError, 'injected'):
            fingerprint(self.source, 'big')
        self.assertEqual(threading.active_count(), baseline)
        self.assertEqual(fingerprint(self.source, 'big').md5, hashlib.md5(data).hexdigest())
        self.assertEqual(threading.active_count(), baseline)

    def test_md5_failure_in_the_helper_thread_is_raised_and_thread_stops(self):
        self.file('big', os.urandom(inventory.THREADED_MD5_MIN + 10))
        baseline = threading.active_count()
        class Broken:
            def update(self, _block):
                raise ValueError('md5 broke')
        with patch('sambinha.inventory.hashlib.md5', return_value=Broken()), \
             self.assertRaisesRegex(ValueError, 'md5 broke'):
            fingerprint(self.source, 'big')
        self.assertEqual(threading.active_count(), baseline)

    def test_reads_use_one_mib_blocks_for_large_files(self):
        self.file('big', b'x' * (2 * inventory.BLOCK_SIZE + 1))
        sizes = []
        real_read = os.read
        def spy(fd, size):
            sizes.append(size)
            return real_read(fd, size)
        with patch('sambinha.inventory.os.read', side_effect=spy):
            fingerprint(self.source, 'big')
        self.assertEqual(set(sizes), {inventory.BLOCK_SIZE})
