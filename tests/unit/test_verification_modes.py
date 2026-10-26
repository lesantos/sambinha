import os
from dataclasses import replace
from unittest.mock import Mock, patch

from sambinha.cli import parser
from sambinha.config import load, with_job_overrides
from sambinha.config_validation import validate
from sambinha.errors import BackupError, ConfigError
from sambinha.inventory import fingerprint, scan, verify
from sambinha.state import validate_file
from sambinha.storage import Storage
from sambinha.verification import verify_local_metadata_job
from tests.helpers import Sandbox, tick


class VerificationModeTests(Sandbox):
    def test_default_toml_and_cli_select_simple_or_hash(self):
        path = self.root / 'config.toml'
        path.write_text('[[jobs]]\nname="test"\nsource="source"\ndestination="destination"\n')
        self.assertEqual(load(path).jobs[0].verification_mode, 'simple')
        self.assertEqual(load(path, {'verification_mode': 'hash'}).jobs[0].verification_mode, 'hash')
        path.write_text(path.read_text().replace('destination="destination"',
                                                 'destination="destination"\nverification_mode="hash"'))
        self.assertEqual(load(path).jobs[0].verification_mode, 'hash')
        self.assertEqual(parser().parse_args(['--verification-mode', 'simple']).verification_mode, 'simple')
        for mode in ('unknown', None, True, [], {}):
            with self.subTest(mode=mode), self.assertRaises(ConfigError):
                validate(replace(self.config, jobs=(replace(self.config.jobs[0], verification_mode=mode),)))

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

    def test_simple_ignores_symlinks_and_rejects_races(self):
        path = self.file('file')
        (self.source / 'link').symlink_to(path)
        self.assertEqual([f.path for f in scan(self.source, mode='simple')], ['file'])
        (self.source / 'link').unlink()
        from sambinha.inventory import metadata
        def changed(root, relative, **kwargs):
            item = metadata(root, relative, **kwargs)
            tick()
            path.write_bytes(b'changed during scan')
            return item
        with patch('sambinha.inventory.metadata', side_effect=changed), self.assertRaises(BackupError):
            scan(self.source, mode='simple')

    def test_simple_destination_inventory_and_copy_do_not_request_hashes(self):
        self.destination.mkdir()
        runner = Mock()
        runner.run.return_value = '[]'
        runner.last_stderr = None
        job = replace(self.config.jobs[0], verification_mode='simple')
        storage = Storage(job, replace(self.config, jobs=(job,)), runner)
        storage.inventory()
        self.assertNotIn('--hash', runner.run.call_args.args[0])
        storage.put(self.file('file'), 'file', protected_mtime_ns=0)
        args = runner.run.call_args.args[0]
        self.assertNotIn('--checksum', args)
        self.assertIn('--ignore-checksum', args)
        self.assertEqual(args[args.index('--modify-window') + 1], '1ns')

    def test_simple_s3_verification_delegates_to_rclone_check(self):
        self.file('file')
        scan(self.source, mode='simple')[0].metadata()
        job = replace(self.config.jobs[0], type='s3', destination='aws:bucket')
        config = with_job_overrides(replace(self.config, jobs=(job,)), verification_mode='simple')
        with patch.object(Storage, 'check_simple') as check, \
             patch.object(Storage, 'get', side_effect=AssertionError('download')):
            result = verify_local_metadata_job(config, job, Mock(), Mock())
        self.assertEqual(result['mode'], 'rclone_check')
        check.assert_called_once()
