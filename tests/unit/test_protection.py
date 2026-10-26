import unittest

from sambinha.errors import BackupError
from sambinha.protection import timestamp_ns


class TimestampTests(unittest.TestCase):
    def test_nanoseconds_timezone_and_whole_seconds(self):
        self.assertEqual(timestamp_ns('1970-01-01T00:00:00.123456789Z'), 123456789)
        self.assertEqual(timestamp_ns('1970-01-01T03:00:00.123456789+03:00'), 123456789)
        self.assertEqual(timestamp_ns('1970-01-01T00:00:01Z'), 1000000000)

    def test_invalid_timestamps_fail_closed(self):
        for value in (None, '', 'yesterday', '2026-99-01T00:00:00Z'):
            with self.subTest(value=value), self.assertRaises(BackupError):
                timestamp_ns(value)
