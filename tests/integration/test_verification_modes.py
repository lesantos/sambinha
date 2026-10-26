import io
import os
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from unittest.mock import patch

from sambinha.config import with_job_overrides
from sambinha.recovery import recover
from tests.helpers import REAL_TOOLS, Sandbox, contents


@unittest.skipUnless(REAL_TOOLS, 'rclone and 7z required')
class SimpleModeIntegrationTests(Sandbox):
    def setUp(self):
        super().setUp()
        self.config = with_job_overrides(self.config, verification_mode='simple')

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
        history = list(self.destination.glob('.sambinha/history/*/dir/file'))
        self.assertEqual(len(history), 2)

    def test_hidden_edit_is_skipped_in_simple_and_found_after_switching_to_hash(self):
        path = self.file('file', b'old')
        self.assertEqual(self.execute(), 0)
        stamp = path.stat().st_mtime_ns
        path.write_bytes(b'new')
        os.utime(path, ns=(stamp, stamp))
        self.assertEqual(self.execute(now=1001), 0)
        self.assertEqual((self.destination / 'file').read_bytes(), b'old')
        self.config = with_job_overrides(self.config, verification_mode='hash')
        self.assertEqual(self.execute(now=1002), 0, self.output.getvalue())
        self.assertEqual((self.destination / 'file').read_bytes(), b'new')
        history = list(self.destination.glob('.sambinha/history/*/file'))
        self.assertEqual([path.read_bytes() for path in history], [b'old'])
        self.config = with_job_overrides(self.config, verification_mode='simple')
        with patch('sambinha.inventory.fingerprint', side_effect=AssertionError('hash read')):
            self.assertEqual(self.execute(now=1003), 0, self.output.getvalue())
        self.assertEqual((self.destination / 'file').read_bytes(), b'new')

    def test_simple_verify_and_restore_and_hash_verify_without_index_hashes(self):
        self.file('file', b'original')
        self.assertEqual(self.execute(), 0)
        with redirect_stdout(io.StringIO()), patch('sambinha.archive_recovery.fingerprint', side_effect=AssertionError('hash read')):
            self.assertEqual(recover(self.config, 'verify-local-metadata'), 0)
            restored = self.root / 'restored'
            self.assertEqual(recover(self.config, 'restore', output=restored), 0)
        self.assertEqual(contents(restored), contents(self.source))
        with redirect_stdout(io.StringIO()):
            self.assertEqual(recover(with_job_overrides(self.config, verification_mode='hash'), 'verify-local-metadata'), 0)
        target = self.destination / 'file'
        target.write_bytes(b'corrupt!')
        with redirect_stdout(io.StringIO()):
            self.assertEqual(recover(self.config, 'verify-local-metadata'), 1)

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
            self.assertEqual(recover(self.config, 'verify-local-metadata'), 0)
            restored = self.root / 'restored'
            self.assertEqual(recover(self.config, 'restore', output=restored), 0)
        self.assertEqual(contents(restored), contents(self.source))

    def test_simple_keeps_newer_destination_protection(self):
        self.file('file', b'old')
        self.assertEqual(self.execute(), 0)
        (self.destination / 'file').write_bytes(b'newer')
        self.assertEqual(self.execute(now=1001), 1)
        self.assertEqual((self.destination / 'file').read_bytes(), b'newer')
