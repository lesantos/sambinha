from dataclasses import replace
from collections import namedtuple
from pathlib import Path
from unittest.mock import Mock, patch

from sambinha.disks import check_disk, check_disks
from sambinha.errors import BackupError
from tests.helpers import REAL_TOOLS, Sandbox

Usage = namedtuple('Usage', 'total used free')


class DiskTests(Sandbox):
    def test_strict_limit_and_reserved_space(self):
        for free, fails in ((6, False), (5, False), (4, True)):
            with self.subTest(free=free), patch('shutil.disk_usage', return_value=Usage(100, 90, free)):
                if fails:
                    with self.assertRaisesRegex(BackupError, '95%'):
                        check_disk(self.source, 'origem')
                else:
                    check_disk(self.source, 'origem')

    def test_nonexistent_destination_checks_nearest_existing_filesystem(self):
        target = self.root / 'new' / 'backup'
        with patch('shutil.disk_usage', return_value=Usage(100, 10, 90)) as usage:
            check_disk(target, 'destino')
        usage.assert_called_once_with(self.root)
        self.assertFalse(target.exists())

    def test_s3_checks_source_and_local_temporary_space_without_bucket_disk_query(self):
        job = replace(self.config.jobs[0], type='s3', logic='batch', destination='aws:bucket')
        with patch('sambinha.disks.check_disk') as check:
            check_disks(job)
        self.assertEqual([c.args[1] for c in check.call_args_list], ['origem', 'temporários'])

    def test_full_source_stops_before_destination_write_and_notifies(self):
        if not REAL_TOOLS:
            self.skipTest('rclone required')
        self.file('file')
        with patch('shutil.disk_usage', return_value=Usage(100, 96, 4)):
            self.assertEqual(self.execute(), 1)
        self.assertFalse(self.destination.exists())
        self.assertEqual(self.notifications[-1][1], 'fail')
        self.assertIn('95%', self.notifications[-1][3])

    def test_destination_crossing_limit_on_final_transfer_is_reported_as_error(self):
        if not REAL_TOOLS:
            self.skipTest('rclone required')
        from sambinha.storage import Storage
        self.file('file')
        original = Storage.put
        filled = False
        def put(storage, *args, **kwargs):
            nonlocal filled
            result = original(storage, *args, **kwargs)
            filled = True
            return result
        def usage(path):
            return Usage(100, 96, 4) if filled and Path(path) == self.destination else Usage(100, 10, 90)
        with patch.object(Storage, 'put', put), patch('shutil.disk_usage', side_effect=usage):
            self.assertEqual(self.execute(), 1)
        self.assertIn('destino', self.notifications[-1][3])
        self.assertIn('95%', self.notifications[-1][3])
