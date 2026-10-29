import json
import os
import shutil
from dataclasses import replace
from datetime import UTC
from pathlib import Path
from unittest.mock import Mock, patch

from sambinha.batches import plan
from sambinha.config import with_job_overrides
from sambinha.errors import BackupError
from sambinha.inventory import scan
from sambinha.verification import verify_destination_metadata_job, verify_local_metadata_job
from tests.helpers import Sandbox


class RestorePublicationTests(Sandbox):
    def test_existing_file_is_never_overwritten_with_or_without_hard_links(self):
        import errno

        from sambinha.restore import publish_file
        source = self.file('file', b'restored')
        metadata = scan(self.source)[0].metadata()
        output = self.root / 'restored'
        output.mkdir()
        target = output / 'file'
        target.write_bytes(b'existing')
        for fallback in (False, True):
            with self.subTest(fallback=fallback):
                if fallback:
                    with patch('sambinha.restore.os.link', side_effect=OSError(errno.EOPNOTSUPP, 'no links')):
                        with self.assertRaises(BackupError):
                            publish_file(source, output, metadata, preserve_permissions=True)
                else:
                    with self.assertRaises(BackupError):
                        publish_file(source, output, metadata, preserve_permissions=True)
                self.assertEqual(target.read_bytes(), b'existing')
                self.assertEqual(source.read_bytes(), b'restored')

    def test_file_mode_overrides_the_recorded_permissions_only_when_given(self):
        from sambinha.restore import publish_file
        for override, expected in ((None, 0o600), (0o644, 0o644)):
            with self.subTest(file_mode=override):
                name = f'f{expected}'
                source = self.file(name, b'restored', mode=0o600)
                metadata = {'path': name, 'mode': 0o600, 'mtime_ns': 10**18}
                output = self.root / f'restored{expected}'
                output.mkdir()
                publish_file(source, output, metadata, preserve_permissions=True, file_mode=override)
                self.assertEqual((output / name).stat().st_mode & 0o777, expected)

    def test_parent_changed_to_symlink_is_rejected_without_writing_outside_output(self):
        from sambinha.restore import publish_file
        source = self.file('dir/file', b'restored')
        metadata = scan(self.source)[0].metadata()
        output = self.root / 'restored'
        output.mkdir()
        outside = self.root / 'outside'
        outside.mkdir()
        (output / 'dir').symlink_to(outside, target_is_directory=True)
        with self.assertRaises(BackupError):
            publish_file(source, output, metadata, preserve_permissions=True)
        self.assertEqual(list(outside.iterdir()), [])
        self.assertEqual(source.read_bytes(), b'restored')


class ArchiveMetadataVerificationTests(Sandbox):
    def test_listing_checks_paths_sizes_and_dates_without_extraction(self):
        from datetime import datetime

        from sambinha.archive_recovery import check_archive
        path = self.file('file', b'payload')
        stamp = 1700000000123456789
        os.utime(path, ns=(stamp, stamp))
        batch = plan(scan(self.source), 1000, 2)[0]
        control = batch.control()
        modified = datetime.fromtimestamp(stamp // 10**9, UTC).strftime('%Y-%m-%d %H:%M:%S') + '.1234567'
        listing = f'Path = file\nSize = 7\nModified = {modified}\n'
        storage = Mock(job=replace(self.config.jobs[0], logic='batch'))
        storage.path.return_value = str(self.root / batch.key)
        runner = Mock()
        runner.run.return_value = listing
        with patch('sambinha.archive_recovery.fingerprint', side_effect=AssertionError('hash read')):
            check_archive(storage, runner, batch.key, control)
        self.assertEqual([call.args[0][1] for call in runner.run.call_args_list], ['l', 't'])
        self.assertEqual(runner.run.call_args_list[0].kwargs, {'env': {'TZ': 'UTC'}})
        for changed in (listing.replace('Path = file', 'Path = other'),
                        listing.replace('Size = 7', 'Size = 8'),
                        listing.replace('.1234567', '.1234568'),
                        listing.replace(f'Modified = {modified}', 'Modified = invalid'),
                        listing.replace('Path = file', 'Path = ../escape'),
                        listing + '\n' + listing):
            with self.subTest(listing=changed), self.assertRaises(BackupError):
                runner.run.return_value = changed
                check_archive(storage, runner, batch.key, control)

    def test_legacy_zip_two_second_date_precision_is_accepted(self):
        from datetime import datetime

        from sambinha.archive_recovery import archive_date_matches
        stamp = 1700000001123456789
        date = datetime.fromtimestamp(stamp // 10**9, UTC)
        date = date.replace(second=date.second // 2 * 2)
        fields = {'Modified': date.strftime('%Y-%m-%d %H:%M:%S')}
        self.assertTrue(archive_date_matches(fields, {'mtime_ns': stamp, 'path': 'file'}, 'zip'))
        self.assertFalse(archive_date_matches(fields, {'mtime_ns': stamp + 2 * 10**9, 'path': 'file'}, 'zip'))


class S3MetadataVerificationTests(Sandbox):
    def test_simple_s3_verification_delegates_to_rclone_check_without_downloads(self):
        self.file('dir/file', b'original')
        job = replace(self.config.jobs[0], type='s3', destination='aws:bucket/prefix')
        runner = Mock()
        with patch('sambinha.storage.Storage.check_simple') as check, \
             patch('sambinha.storage.Storage.get', side_effect=AssertionError('download')):
            result = verify_local_metadata_job(self.config, job, runner, Mock())
        self.assertEqual(result['mode'], 'rclone_check')
        check.assert_called_once()
        runner.run.assert_not_called()

    def test_batch_archive_and_control_are_checked_with_only_metadata_downloads(self):
        self.file('file', b'original')
        batch = plan(scan(self.source), 1000, 2)[0]
        control = batch.control()
        control.update(archive_size=200, archive_md5='a' * 32)
        job = replace(self.config.jobs[0], type='s3', logic='batch', destination='aws:bucket')
        def download(storage, key, target):
            self.assertEqual(key, batch.key + '.json')
            Path(target).write_text(json.dumps(control), encoding='utf-8')
        with patch('sambinha.storage.Storage.stat', return_value={'Size': 200}) as stat, \
             patch('sambinha.storage.Storage.get', download):
            verify_destination_metadata_job(self.config, job, Mock(), Mock(), {'active': {batch.key: control}})
        self.assertEqual([c.args[0] for c in stat.call_args_list], [batch.key])
        self.assertTrue(all(c.kwargs == {'hashes': False} for c in stat.call_args_list))

    def test_simple_s3_verification_uses_rclone_check_in_hash_mode_too(self):
        job = replace(self.config.jobs[0], type='s3', destination='aws:bucket')
        config = with_job_overrides(replace(self.config, jobs=(job,)), verification_mode='hash')
        with patch('sambinha.storage.Storage.check_simple') as check, \
             patch('sambinha.storage.Storage.get', side_effect=AssertionError('download')):
            result = verify_local_metadata_job(config, job, Mock(), Mock())
        self.assertEqual(result['mode'], 'rclone_check')
        check.assert_called_once()

    def test_missing_local_s3_index_is_recovered_from_immediately_accessible_metadata(self):
        import tarfile

        from sambinha.state import identity
        self.file('file')
        batch = plan(scan(self.source), 1000, 2)[0]
        control = {**batch.control(), 'archive_size': 200, 'archive_md5': 'a' * 32}
        job = replace(self.config.jobs[0], type='s3', destination='aws:bucket', logic='batch')
        state = {'schema': 1, 'identity': identity(job), 'active': {batch.key: control}, 'retired': []}
        saved = self.root / 'state.json'
        saved.write_text(json.dumps(state))
        index = self.root / 'control.tar.gz'
        with tarfile.open(index, 'w:gz') as tar:
            tar.add(saved, arcname='state.json')
        downloads = []
        def download(storage, key, target):
            downloads.append(key)
            if key == '.sambinha/control.tar.gz':
                shutil.copyfile(index, target)
            else:
                self.assertEqual(key, batch.key + '.json')
                Path(target).write_text(json.dumps(control))
        with patch('sambinha.storage.Storage.get', download), \
             patch('sambinha.index_generations.listing', return_value={'.sambinha/control.tar.gz': index.stat().st_size, batch.key: 200, batch.key + '.json': 200}), \
             patch('sambinha.storage.Storage.stat', return_value={'Size': 200}):
            verify_destination_metadata_job(self.config, job, Mock(), Mock())
        self.assertEqual(downloads, ['.sambinha/control.tar.gz', batch.key + '.json'])
        self.assertFalse((self.config.state_dir / job.name / 'state.json').exists())

    def test_legacy_batch_without_archive_size_fails_closed(self):
        self.file('file')
        batch = plan(scan(self.source), 1000, 2)[0]
        job = replace(self.config.jobs[0], type='s3', destination='aws:bucket', logic='batch')
        with patch('sambinha.storage.Storage.get', side_effect=AssertionError('download')), self.assertRaises(BackupError):
            verify_destination_metadata_job(self.config, job, Mock(), Mock(), {'active': {batch.key: batch.control()}})

    def test_s3_size_mismatch_fails_without_hashes_or_downloads(self):
        self.file('file')
        batch = plan(scan(self.source), 1000, 2)[0]
        control = {**batch.control(), 'archive_size': 200}
        job = replace(self.config.jobs[0], type='s3', destination='aws:bucket', logic='batch')
        with patch('sambinha.storage.Storage.stat', return_value={'Size': 201}), \
             patch('sambinha.storage.Storage.get', side_effect=AssertionError('download')), \
             self.assertRaises(BackupError):
            verify_destination_metadata_job(self.config, job, Mock(), Mock(), {'active': {batch.key: control}})

    def test_s3_control_checks_all_json_fields_without_calculating_hashes(self):
        import copy
        self.file('file')
        batch = plan(scan(self.source), 1000, 2)[0]
        control = {**batch.control(), 'archive_size': 200, 'archive_md5': 'a' * 32}
        job = replace(self.config.jobs[0], type='s3', destination='aws:bucket', logic='batch')
        for field, value, fails in (('path', 'other', True), ('size', 99, True),
                                    ('mtime_ns', 0, True), ('md5', '0' * 32, True),
                                    ('sha256', '0' * 64, True)):
            saved = copy.deepcopy(control)
            saved['files'][0][field] = value
            def download(storage, key, target):
                self.assertEqual(key, batch.key + '.json')
                Path(target).write_text(json.dumps(saved))
            with self.subTest(field=field), patch('sambinha.storage.Storage.get', download), \
                 patch('sambinha.storage.Storage.stat', return_value={'Size': 200}):
                if fails:
                    with self.assertRaises(BackupError):
                        verify_destination_metadata_job(self.config, job, Mock(), Mock(), {'active': {batch.key: control}})
                else:
                    verify_destination_metadata_job(self.config, job, Mock(), Mock(), {'active': {batch.key: control}})

    def test_invalid_remote_control_is_rejected_without_downloading_batch(self):
        self.file('file')
        batch = plan(scan(self.source), 1000, 2)[0]
        control = {**batch.control(), 'archive_size': 200}
        job = replace(self.config.jobs[0], type='s3', destination='aws:bucket', logic='batch')
        def download(storage, key, target):
            self.assertEqual(key, batch.key + '.json')
            Path(target).write_text('{invalid')
        with patch('sambinha.storage.Storage.get', download), \
             patch('sambinha.storage.Storage.stat', return_value={'Size': 200}), self.assertRaises(BackupError):
            verify_destination_metadata_job(self.config, job, Mock(), Mock(), {'active': {batch.key: control}})
