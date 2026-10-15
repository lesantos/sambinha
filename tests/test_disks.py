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
        with patch('shutil.disk_usage', return_value=Usage(100 * 1024**2, 10 * 1024**2, 90 * 1024**2)) as usage:
            check_disk(target, 'destino')
        usage.assert_called_once_with(self.root)
        self.assertFalse(target.exists())

    def test_required_space_uses_available_bytes_and_accepts_exact_fit(self):
        with patch('shutil.disk_usage', return_value=Usage(100, 50, 40)):
            check_disk(self.source, 'temporários', required_bytes=40)
            with self.assertRaisesRegex(BackupError, 'necessários 41 bytes, disponíveis 40 bytes'):
                check_disk(self.source, 'temporários', required_bytes=41)

    def test_s3_checks_source_and_local_temporary_space_without_bucket_disk_query(self):
        job = replace(self.config.jobs[0], type='s3', logic='batch', destination='aws:bucket')
        with patch('sambinha.disks.check_disk') as check:
            check_disks(job)
        self.assertEqual([c.args[1] for c in check.call_args_list], ['origem', 'temporários'])

    def test_checks_can_be_disabled_independently_and_keep_batch_temporary_check(self):
        for source in (True, False):
            for destination in (True, False):
                for kind in ('local', 's3'):
                    with self.subTest(source=source, destination=destination, kind=kind):
                        job = replace(self.config.jobs[0], type=kind, logic='batch',
                                      check_source_disk=source, check_destination_disk=destination)
                        with patch('sambinha.disks.check_disk') as check:
                            check_disks(job)
                        expected = (['origem'] if source else [])
                        expected += ['destino'] if kind == 'local' and destination else []
                        expected += ['temporários']
                        self.assertEqual([c.args[1] for c in check.call_args_list], expected)

    def test_restore_temporary_path_replaces_system_temporary_check(self):
        job = replace(self.config.jobs[0], logic='batch')
        output = self.root / 'restored'
        with patch('sambinha.disks.check_disk') as check:
            check_disks(job, temporary_path=output)
        self.assertEqual(check.call_args_list[-1].args, (output, 'temporários', None))

    def test_unknown_capacity_and_os_errors_include_source_path(self):
        for failure in (Usage(0, 0, 0), PermissionError('sem permissão')):
            with self.subTest(failure=failure):
                options = {'side_effect': failure} if isinstance(failure, Exception) else {'return_value': failure}
                with patch('shutil.disk_usage', **options), self.assertRaises(BackupError) as error:
                    check_disk(self.source, 'origem')
                self.assertIn(str(self.source), str(error.exception))
                self.assertIn('origem', str(error.exception))
                self.assertIn('0 bytes' if not isinstance(failure, Exception) else 'sem permissão', str(error.exception))

    def test_disabled_checks_allow_backup_when_capacity_is_unavailable(self):
        if not REAL_TOOLS:
            self.skipTest('rclone and 7z required')
        self.file('file')
        for logic in ('simple', 'batch'):
            for role in ('source', 'destination'):
                with self.subTest(logic=logic, role=role):
                    destination = self.root / f'{logic}-{role}'
                    job = replace(self.config.jobs[0], logic=logic, destination=str(destination),
                                  **{f'check_{role}_disk': False})
                    config = replace(self.config, jobs=(job,), state_dir=self.root / f'state-{logic}-{role}')
                    measured = []
                    def usage(path):
                        measured.append(Path(path))
                        unavailable = (Path(path) == self.source if role == 'source' else
                                       Path(path) in (destination, self.root))
                        return Usage(0, 0, 0) if unavailable else Usage(100 * 1024**2, 10 * 1024**2, 90 * 1024**2)
                    # The nearest existing destination parent must also remain
                    # unmeasured; temporary storage always has a real capacity.
                    with patch('shutil.disk_usage', side_effect=usage):
                        self.assertEqual(self.execute(config), 0)
                    self.assertTrue(destination.exists())
                    if role == 'source':
                        self.assertNotIn(self.source, measured)
                    else:
                        self.assertNotIn(destination, measured)

    def test_full_source_stops_before_destination_write_and_notifies(self):
        if not REAL_TOOLS:
            self.skipTest('rclone required')
        self.file('file')
        with patch('shutil.disk_usage', return_value=Usage(100, 96, 4)):
            self.assertEqual(self.execute(), 1)
        self.assertFalse(self.destination.exists())
        self.assertEqual(self.notifications[-1][1], 'fail')
        self.assertIn('95%', self.notifications[-1][3])

    def test_disabling_source_check_does_not_disable_destination_protection(self):
        if not REAL_TOOLS:
            self.skipTest('rclone required')
        self.file('file')
        job = replace(self.config.jobs[0], check_source_disk=False)
        with patch('shutil.disk_usage', return_value=Usage(100, 96, 4)):
            self.assertEqual(self.execute(replace(self.config, jobs=(job,))), 1)
        self.assertFalse(self.destination.exists())
        self.assertIn('destino', self.notifications[-1][3])

    def test_disabling_both_checks_keeps_temporary_and_mount_protection(self):
        if not REAL_TOOLS:
            self.skipTest('rclone and 7z required')
        self.file('file')
        job = replace(self.config.jobs[0], logic='batch', check_source_disk=False,
                      check_destination_disk=False)
        with patch('shutil.disk_usage', return_value=Usage(100, 96, 4)):
            self.assertEqual(self.execute(replace(self.config, jobs=(job,))), 1)
        self.assertFalse(self.destination.exists())
        self.assertIn('temporários', self.notifications[-1][3])
        job = replace(job, source_mount=self.root / 'missing-mount')
        self.assertEqual(self.execute(replace(self.config, jobs=(job,))), 1)
        self.assertFalse(self.destination.exists())
        self.assertIn('Montagem de origem', self.notifications[-1][3])

    def test_destination_crossing_limit_on_final_transfer_is_reported_as_error(self):
        if not REAL_TOOLS:
            self.skipTest('rclone required')
        from sambinha.storage import Storage
        self.file('file')
        self.config = replace(self.config, jobs=(replace(self.config.jobs[0], logic='batch'),))
        original = Storage.put
        filled = False
        def put(storage, *args, **kwargs):
            nonlocal filled
            result = original(storage, *args, **kwargs)
            filled = True
            return result
        def usage(path):
            return Usage(100, 96, 4) if filled and Path(path) == self.destination else Usage(100 * 1024**2, 10 * 1024**2, 90 * 1024**2)
        with patch.object(Storage, 'put', put), patch('shutil.disk_usage', side_effect=usage):
            self.assertEqual(self.execute(), 1)
        self.assertIn('destino', self.notifications[-1][3])
        self.assertIn('95%', self.notifications[-1][3])
