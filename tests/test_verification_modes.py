from contextlib import redirect_stdout
from dataclasses import replace
import io
import json
import os
import unittest
from unittest.mock import Mock, patch

from sambinha.cli import parser
from sambinha.config import load, validate
from sambinha.errors import BackupError, ConfigError
from sambinha.inventory import fingerprint, scan, verify
from sambinha.recovery import recover, verify_job
from sambinha.state import load_state, validate_file
from sambinha.storage import Storage
from tests.helpers import REAL_TOOLS, Sandbox, contents


class VerificationModeTests(Sandbox):
    def test_default_toml_and_cli_select_simple_or_hash(self):
        path = self.root / 'config.toml'
        path.write_text('[[jobs]]\nname="test"\nsource="source"\ndestination="destination"\n')
        self.assertEqual(load(path).verification_mode, 'simple')
        self.assertEqual(load(path, {'verification_mode': 'hash'}).verification_mode, 'hash')
        path.write_text('[backup]\nverification_mode="hash"\n' + path.read_text())
        self.assertEqual(load(path).verification_mode, 'hash')
        self.assertEqual(parser().parse_args(['--verification-mode', 'simple']).verification_mode, 'simple')
        for mode in ('unknown', None, True, [], {}):
            with self.subTest(mode=mode), self.assertRaises(ConfigError):
                validate(replace(self.config, verification_mode=mode))

    def test_simple_inventory_never_opens_contents_even_without_cache(self):
        self.file('dir/file', b'original')
        cache = {}
        with patch('sambinha.inventory.fingerprint', side_effect=AssertionError('hash read')):
            files = scan(self.source, mode='simple', cache_out=cache)
            self.assertEqual(scan(self.source, mode='simple', cache=cache), files)
            verify(self.source, files, mode='simple')
        self.assertEqual((files[0].sha256, files[0].md5), ('', ''))
        validate_file(files[0].metadata())

    def test_simple_detects_size_and_nanosecond_mtime_but_not_hidden_content_changes(self):
        path = self.file('file', b'old')
        before = scan(self.source, mode='simple')
        stamp = before[0].mtime_ns
        path.write_bytes(b'new')
        os.utime(path, ns=(stamp, stamp))
        self.assertEqual(scan(self.source, mode='simple'), before)
        verify(self.source, before, mode='simple')
        os.utime(path, ns=(stamp + 1, stamp + 1))
        with self.assertRaises(BackupError):
            verify(self.source, before, mode='simple')
        path.write_bytes(b'longer')
        os.utime(path, ns=(stamp, stamp))
        with self.assertRaises(BackupError):
            verify(self.source, before, mode='simple')

    def test_switching_modes_does_not_trust_absent_or_stale_hashes(self):
        path = self.file('file', b'old')
        cache = {}
        initial = scan(self.source, cache_out=cache)[0]
        path.write_bytes(b'new')
        os.utime(path, ns=(initial.mtime_ns, initial.mtime_ns))
        simple_cache = {}
        simple = scan(self.source, mode='simple', cache=cache, cache_out=simple_cache)[0]
        self.assertEqual(simple.md5, '')
        with patch('sambinha.inventory.fingerprint', wraps=fingerprint) as hashing:
            hashed = scan(self.source, cache=simple_cache)[0]
        hashing.assert_called_once()
        self.assertNotEqual(initial.sha256, hashed.sha256)

    def test_partial_or_malformed_hashes_remain_invalid(self):
        self.file('file')
        item = scan(self.source, mode='simple')[0].metadata()
        for changes in ({'md5': 'a' * 32}, {'sha256': None}, {'md5': 123}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_file({**item, **changes})

    def test_simple_still_rejects_symlinks_and_races(self):
        path = self.file('file')
        (self.source / 'link').symlink_to(path)
        with self.assertRaises(BackupError):
            scan(self.source, mode='simple')
        (self.source / 'link').unlink()
        from sambinha.inventory import metadata
        def changed(root, relative):
            item = metadata(root, relative)
            path.write_bytes(b'changed during scan')
            return item
        with patch('sambinha.inventory.metadata', side_effect=changed), self.assertRaises(BackupError):
            scan(self.source, mode='simple')

    def test_simple_destination_inventory_and_copy_do_not_request_hashes(self):
        self.destination.mkdir()
        runner = Mock()
        runner.run.return_value = '[]'
        runner.last_stderr = None
        storage = Storage(self.config.jobs[0], replace(self.config, verification_mode='simple'), runner)
        storage.inventory()
        self.assertNotIn('--hash', runner.run.call_args.args[0])
        storage.put(self.file('file'), 'file', protected_mtime_ns=0)
        args = runner.run.call_args.args[0]
        self.assertNotIn('--checksum', args)
        self.assertIn('--ignore-checksum', args)
        self.assertEqual(args[args.index('--modify-window') + 1], '1ns')

    def test_simple_s3_verification_uses_size_and_modtime_without_download(self):
        self.file('file')
        item = scan(self.source, mode='simple')[0].metadata()
        job = replace(self.config.jobs[0], type='s3', destination='aws:bucket')
        config = replace(self.config, jobs=(job,), verification_mode='simple')
        from datetime import datetime, timezone
        seconds, ns = divmod(item['mtime_ns'], 10**9)
        stamp = datetime.fromtimestamp(seconds, timezone.utc).strftime('%Y-%m-%dT%H:%M:%S') + f'.{ns:09d}Z'
        remote = {'Size': item['size'], 'ModTime': stamp}
        with patch.object(Storage, 'stat', return_value=remote) as stat, \
             patch.object(Storage, 'get', side_effect=AssertionError('download')):
            verify_job(config, job, Mock(), Mock(), {'active': {'file': item}})
        stat.assert_called_once_with('file', hashes=False)
        for change in ({'Size': item['size'] + 1}, {'ModTime': '2000-01-01T00:00:00Z'}):
            with patch.object(Storage, 'stat', return_value={**remote, **change}), self.assertRaises(BackupError):
                verify_job(config, job, Mock(), Mock(), {'active': {'file': item}})


@unittest.skipUnless(REAL_TOOLS, 'rclone and 7z required')
class SimpleModeIntegrationTests(Sandbox):
    def setUp(self):
        super().setUp()
        self.config = replace(self.config, verification_mode='simple')

    def test_first_copy_repeat_and_size_or_timestamp_changes_without_hash_reads(self):
        path = self.file('dir/file', b'old')
        self.file('dir/other', b'other')  # Exercise bulk copy as well as single copy.
        with patch('sambinha.inventory.fingerprint', side_effect=AssertionError('hash read')):
            self.assertEqual(self.execute(), 0, self.output.getvalue())
            self.assertEqual(contents(self.destination), contents(self.source))
            self.assertEqual(self.execute(now=1001), 0, self.output.getvalue())
            self.assertFalse(any(e['event'] == 'transferred' for e in self.events))
            stamp = path.stat().st_mtime_ns
            path.write_bytes(b'new')
            os.utime(path, ns=(stamp + 1, stamp + 1))
            self.assertEqual(self.execute(now=1002), 0, self.output.getvalue())
            self.assertEqual((self.destination / 'dir/file').read_bytes(), b'new')
            path.write_bytes(b'longer')
            os.utime(path, ns=(stamp + 1, stamp + 1))
            self.assertEqual(self.execute(now=1003), 0, self.output.getvalue())
            self.assertEqual((self.destination / 'dir/file').read_bytes(), b'longer')
        self.assertEqual(len(self.state()['retired']), 2)

    def test_hidden_edit_is_skipped_in_simple_and_found_after_switching_to_hash(self):
        path = self.file('file', b'old')
        self.assertEqual(self.execute(), 0)
        stamp = path.stat().st_mtime_ns
        path.write_bytes(b'new')
        os.utime(path, ns=(stamp, stamp))
        self.assertEqual(self.execute(now=1001), 0)
        self.assertEqual((self.destination / 'file').read_bytes(), b'old')
        self.config = replace(self.config, verification_mode='hash')
        self.assertEqual(self.execute(now=1002), 0, self.output.getvalue())
        self.assertEqual((self.destination / 'file').read_bytes(), b'new')
        self.assertTrue(self.state()['active']['file']['sha256'])
        self.config = replace(self.config, verification_mode='simple')
        with patch('sambinha.inventory.fingerprint', side_effect=AssertionError('hash read')):
            self.assertEqual(self.execute(now=1003), 0, self.output.getvalue())
        self.assertEqual(self.state()['active']['file']['sha256'], '')

    def test_simple_verify_and_restore_and_hash_verify_without_index_hashes(self):
        self.file('file', b'original')
        self.assertEqual(self.execute(), 0)
        with redirect_stdout(io.StringIO()), patch('sambinha.recovery.fingerprint', side_effect=AssertionError('hash read')):
            self.assertEqual(recover(self.config, 'verify'), 0)
            restored = self.root / 'restored'
            self.assertEqual(recover(self.config, 'restore', output=restored), 0)
        self.assertEqual(contents(restored), contents(self.source))
        with redirect_stdout(io.StringIO()):
            self.assertEqual(recover(replace(self.config, verification_mode='hash'), 'verify'), 1)
        target = self.destination / 'file'
        stamp = target.stat().st_mtime_ns
        os.utime(target, ns=(stamp + 1, stamp + 1))
        with redirect_stdout(io.StringIO()):
            self.assertEqual(recover(self.config, 'verify'), 1)

    def test_simple_batch_reuse_change_and_restore_keep_archive_integrity(self):
        self.file('a.txt', b'a')
        changed = self.file('b.txt', b'b')
        self.config = replace(self.config, jobs=(replace(self.config.jobs[0], logic='batch'),))
        with patch('sambinha.inventory.fingerprint', side_effect=AssertionError('source hash read')):
            self.assertEqual(self.execute(), 0, self.output.getvalue())
            self.assertEqual(self.execute(now=1001), 0, self.output.getvalue())
        self.assertEqual(next(e for e in self.events if e['event'] == 'summary')['rebuilt_batches'], 0)
        changed.write_bytes(b'changed')
        self.assertEqual(self.execute(now=1002), 0, self.output.getvalue())
        self.assertEqual(len(self.state()['retired']), 1)
        with redirect_stdout(io.StringIO()):
            self.assertEqual(recover(self.config, 'verify'), 0)
            restored = self.root / 'restored'
            self.assertEqual(recover(self.config, 'restore', output=restored), 0)
        self.assertEqual(contents(restored), contents(self.source))

    def test_simple_keeps_newer_destination_protection(self):
        self.file('file', b'old')
        self.assertEqual(self.execute(), 0)
        (self.destination / 'file').write_bytes(b'newer')
        self.assertEqual(self.execute(now=1001), 1)
        self.assertEqual((self.destination / 'file').read_bytes(), b'newer')
