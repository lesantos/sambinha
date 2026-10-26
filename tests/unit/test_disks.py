from dataclasses import replace
from unittest.mock import patch

from sambinha.disks import check_disk, check_disks
from sambinha.errors import BackupError
from tests.helpers import Sandbox, Usage


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

    def test_configured_global_temporary_path_is_used_for_batch_jobs(self):
        job = replace(self.config.jobs[0], logic='batch')
        config = replace(self.config, jobs=(job,), temp_dir=self.root / 'fast-tmp')
        config.temp_dir.mkdir()
        with patch('sambinha.disks.check_disk') as check:
            check_disks(job, config=config)
        self.assertEqual(check.call_args_list[-1].args, (config.temp_dir, 'temporários', None))

    def test_configured_global_temporary_path_must_exist_and_be_writable(self):
        job = replace(self.config.jobs[0], logic='batch')
        config = replace(self.config, jobs=(job,), temp_dir=self.root / 'missing-tmp')
        with self.assertRaisesRegex(BackupError, 'Diretório temporário configurado'):
            check_disks(job, config=config)

    def test_unknown_capacity_and_os_errors_include_source_path(self):
        for failure in (Usage(0, 0, 0), PermissionError('sem permissão')):
            with self.subTest(failure=failure):
                options = {'side_effect': failure} if isinstance(failure, Exception) else {'return_value': failure}
                with patch('shutil.disk_usage', **options), self.assertRaises(BackupError) as error:
                    check_disk(self.source, 'origem')
                self.assertIn(str(self.source), str(error.exception))
                self.assertIn('origem', str(error.exception))
                self.assertIn('0 bytes' if not isinstance(failure, Exception) else 'sem permissão', str(error.exception))
