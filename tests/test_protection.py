from contextlib import redirect_stdout
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import unittest

from sambinha.engine import run
from sambinha.errors import BackupError
from sambinha.protection import timestamp_ns
from tests.helpers import REAL_TOOLS, Sandbox, contents


class TimestampTests(unittest.TestCase):
    def test_nanoseconds_timezone_and_whole_seconds(self):
        self.assertEqual(timestamp_ns('1970-01-01T00:00:00.123456789Z'), 123456789)
        self.assertEqual(timestamp_ns('1970-01-01T03:00:00.123456789+03:00'), 123456789)
        self.assertEqual(timestamp_ns('1970-01-01T00:00:01Z'), 1000000000)

    def test_invalid_timestamps_fail_closed(self):
        for value in (None, '', 'yesterday', '2026-99-01T00:00:00Z'):
            with self.subTest(value=value), self.assertRaises(BackupError):
                timestamp_ns(value)


@unittest.skipUnless(REAL_TOOLS, 'rclone and 7z required')
class NewerDestinationTests(Sandbox):
    def test_newer_destination_is_untouched_and_cronitor_gets_details(self):
        source = self.file('file', b'old source')
        self.assertEqual(self.execute(), 0)
        target = self.destination / 'file'
        target.write_bytes(b'new destination')
        ns = source.stat().st_mtime_ns + 1000000000
        os.utime(target, ns=(ns, ns))
        before = {p.relative_to(self.destination): p.read_bytes() for p in self.destination.rglob('*') if p.is_file()}
        self.file('new', b'must not transfer before conflict detection')
        self.assertEqual(self.execute(), 1)
        self.assertEqual(before, {p.relative_to(self.destination): p.read_bytes() for p in self.destination.rglob('*') if p.is_file()})
        self.assertEqual(target.stat().st_mtime_ns, ns)
        self.assertEqual(self.notifications[-1][1], 'fail')
        self.assertIn('file', self.notifications[-1][3])
        self.assertIn('mais novos', self.notifications[-1][3])

    def test_identical_but_newer_keeps_modification_time(self):
        source = self.file('file')
        self.assertEqual(self.execute(), 0)
        target = self.destination / 'file'
        ns = source.stat().st_mtime_ns + 1
        os.utime(target, ns=(ns, ns))
        self.assertEqual(self.execute(), 1)
        self.assertEqual(target.stat().st_mtime_ns, ns)

    def test_batch_older_source_preserves_all_confirmed_lots(self):
        source = self.file('file', b'confirmed')
        self.file('other', b'other')
        self.config = replace(self.config, jobs=(replace(self.config.jobs[0], logic='batch'),))
        self.assertEqual(self.execute(), 0)
        before = {p.relative_to(self.destination): p.read_bytes() for p in self.destination.rglob('*') if p.is_file()}
        ns = source.stat().st_mtime_ns - 1000000000
        source.write_bytes(b'older restored source')
        os.utime(source, ns=(ns, ns))
        self.assertEqual(self.execute(), 1)
        self.assertEqual(before, {p.relative_to(self.destination): p.read_bytes() for p in self.destination.rglob('*') if p.is_file()})
        self.assertEqual(self.notifications[-1][1], 'fail')

    def test_conflict_in_simulation_returns_error_without_notification(self):
        source = self.file('file')
        self.assertEqual(self.execute(), 0)
        ns = source.stat().st_mtime_ns + 1000000000
        os.utime(self.destination / 'file', ns=(ns, ns))
        self.notifications.clear()
        self.assertEqual(self.execute(replace(self.config, dry_run=True)), 1)
        self.assertEqual(self.notifications, [])

    def test_concurrent_newer_destination_is_skipped_by_rclone_and_notified(self):
        from unittest.mock import patch
        from sambinha.storage import Storage
        source = self.file('file', b'first')
        self.assertEqual(self.execute(), 0)
        source.write_bytes(b'updated source')
        original = Storage.put
        def concurrent_change(storage, local, key, **kwargs):
            if key == 'file':
                target = self.destination / key
                target.write_bytes(b'concurrent newer destination')
                ns = source.stat().st_mtime_ns + 1000000000
                os.utime(target, ns=(ns, ns))
            return original(storage, local, key, **kwargs)
        with patch.object(Storage, 'put', concurrent_change):
            self.assertEqual(self.execute(), 1)
        self.assertEqual((self.destination / 'file').read_bytes(), b'concurrent newer destination')
        self.assertEqual(self.notifications[-1][1], 'fail')
        self.assertIn('durante a cópia', self.notifications[-1][3])
