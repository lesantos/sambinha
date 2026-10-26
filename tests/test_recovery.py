import io
import json
import os
import shutil
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from datetime import UTC
from pathlib import Path
from unittest.mock import Mock, patch

from sambinha.batches import plan
from sambinha.config import with_job_overrides
from sambinha.config_validation import validate
from sambinha.errors import BackupError
from sambinha.inventory import scan
from sambinha.recovery import recover
from sambinha.verification import verify_destination_metadata_job, verify_local_metadata_job
from tests.helpers import REAL_TOOLS, Sandbox, contents


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


@unittest.skipUnless(REAL_TOOLS, 'rclone and 7z required')
class RecoveryIntegrationTests(Sandbox):
    def recover(self, action, **kwargs):
        output = io.StringIO()
        with redirect_stdout(output):
            result = recover(validate(self.config), action, **kwargs)
        self.recovery_events = [json.loads(line) for line in output.getvalue().splitlines()]
        return result

    def test_destination_option_keeps_temporary_space_check_on_output_disk(self):
        from tests.test_disks import Usage
        self.file('file')
        self.config = replace(self.config, jobs=(replace(self.config.jobs[0], logic='batch'),))
        self.assertEqual(self.execute(), 0)
        self.config = replace(self.config, jobs=(replace(self.config.jobs[0],
                              check_source_disk=False, check_destination_disk=False),))
        restored = self.root / 'restored'
        measured = []
        def usage(path):
            measured.append(Path(path))
            return Usage(100, 10, 90)
        with patch('shutil.disk_usage', side_effect=usage):
            self.assertEqual(self.recover('restore', output=restored), 0)
        self.assertEqual(contents(restored), contents(self.source))
        self.assertNotIn(restored, measured)
        self.assertIn(self.root, measured)
        self.assertTrue(any('sambinha-restore-' in str(path) for path in measured))
        with patch('shutil.disk_usage', return_value=Usage(100, 96, 4)):
            self.assertEqual(self.recover('restore', output=self.root / 'full-temp'), 1)
        error = next(e for e in self.recovery_events if e['event'] == 'error')
        self.assertEqual(error['job'], 'test')
        self.assertIn('temporários', error['error'])
        self.assertFalse((self.root / 'full-temp').exists())

    def test_local_integrity_check_accepts_passthrough_archives(self):
        self.file('dir/backup.zip', bytes(range(256)) * 4)
        self.file('note.txt', b'note')
        job = replace(self.config.jobs[0], logic='batch', max_megabytes=100 / 1024 ** 2, log_terminal_level='detailed')
        self.config = replace(self.config, jobs=(job,))
        self.assertEqual(self.execute(), 0)
        state = self.state()
        self.assertTrue(any(control.get('passthrough') for control in state['active'].values()))
        self.assertEqual(self.recover('verify-destination-integrity'), 0)
        tested = [e['path'] for e in self.recovery_events if e['event'] == 'tested_archive']
        self.assertEqual(sorted(tested), sorted(state['active']))
        archive = next(key for key, control in state['active'].items() if control.get('passthrough'))
        (self.destination / archive).write_bytes(b'corrupt')
        self.assertEqual(self.recover('verify-destination-integrity'), 1)
        error = next(e for e in self.recovery_events if e['event'] == 'error')
        self.assertIn('Arquivo compactado individual divergente', error['error'])

    def test_unexpected_job_exception_is_logged_as_error_instead_of_traceback(self):
        self.file('file')
        self.config = replace(self.config, jobs=(replace(self.config.jobs[0], logic='batch'),))
        self.assertEqual(self.execute(), 0)
        with patch('sambinha.recovery.verify_destination_integrity_job',
                   side_effect=AttributeError("'str' object has no attribute 'parent'")):
            self.assertEqual(self.recover('verify-destination-integrity'), 1)
        error = next(e for e in self.recovery_events if e['event'] == 'error')
        self.assertEqual(error['job'], 'test')
        self.assertIn("'parent'", error['error'])
        self.assertEqual(self.recovery_events[-1]['event'], 'end')
        self.assertFalse(self.recovery_events[-1]['success'])

    def test_disabling_output_disk_check_preserves_restore_path_validation(self):
        self.file('file')
        self.assertEqual(self.execute(), 0)
        self.config = replace(self.config, jobs=(replace(self.config.jobs[0], check_destination_disk=False),))
        self.assertEqual(self.recover('restore', output=self.source), 1)
        self.assertEqual((self.source / 'file').read_bytes(), b'test')

    def test_simple_restore_selected_directory_and_file_preserves_contents_and_metadata(self):
        self.file('dir/binary.dat', bytes(range(256)), mode=0o750)
        self.file('dir/ação.txt', 'Olá'.encode())
        self.file('other', b'not selected')
        self.assertEqual(self.execute(), 0)
        shutil.rmtree(self.source)
        restored = self.root / 'restored'
        self.assertEqual(self.recover('restore', output=restored, selectors=('dir', 'other')), 0)
        self.assertEqual(contents(restored), {'dir/binary.dat': bytes(range(256)),
                                              'dir/ação.txt': 'Olá'.encode(),
                                              'other': b'not selected'})
        self.assertEqual((restored / 'dir/binary.dat').stat().st_mode & 0o777, 0o750)

    def test_batch_restore_recovers_index_when_local_state_is_lost(self):
        self.file('a.txt', b'a')
        self.file('b.txt', b'b')
        self.config = replace(self.config, jobs=(replace(self.config.jobs[0], logic='batch'),))
        self.assertEqual(self.execute(), 0)
        expected = contents(self.source)
        shutil.rmtree(self.config.state_dir)
        shutil.rmtree(self.source)
        restored = self.root / 'restored'
        self.assertEqual(self.recover('restore', output=restored), 0)
        self.assertEqual(contents(restored), expected)
        self.assertFalse(self.config.state_dir.exists())

    def test_local_batch_integrity_reads_archives_in_place_without_hashes_or_extraction(self):
        from sambinha.process import Runner
        from sambinha.state import atomic_json
        path = self.file('file.txt', b'payload')
        stamp = 1700000000123456789
        os.utime(path, ns=(stamp, stamp))
        for archive_format in ('7z', 'zip'):
            with self.subTest(archive_format=archive_format):
                job = replace(self.config.jobs[0], logic='batch', archive_format=archive_format,
                              destination=str(self.root / archive_format))
                self.config = replace(self.config, jobs=(job,), state_dir=self.root / f'state-{archive_format}')
                self.assertEqual(self.execute(), 0)
                state = self.state()
                for control in state['active'].values():
                    control['archive_md5'] = '0' * 32
                    for member in control['files']:
                        member['sha256'], member['md5'] = '0' * 64, '0' * 32
                atomic_json(self.config.state_dir / job.name / 'state.json', state)
                commands = []
                original = Runner.run
                def record(runner, args, **kwargs):
                    commands.append(args)
                    return original(runner, args, **kwargs)
                with patch.object(Runner, 'run', record), \
                     patch('sambinha.storage.Storage.get', side_effect=AssertionError('archive download')), \
                     patch('sambinha.archive_recovery.fingerprint', side_effect=AssertionError('hash read')):
                    self.assertEqual(self.recover('verify-destination-integrity'), 0)
                compressor_calls = [args for args in commands if args[0] == job.compressor]
                self.assertEqual([args[1:] for args in compressor_calls if args[1] == '-h'], [['-h']])
                archive_calls = [args for args in compressor_calls if args[1] != '-h']
                self.assertEqual([args[1] for args in archive_calls], ['l', 't'])
                self.assertTrue(all(Path(args[-1]).is_relative_to(job.destination) for args in archive_calls))

    def test_archive_dates_match_in_utc_across_timezones_and_historical_dst(self):
        path = self.file('file.txt', b'payload')
        # January 2009 had UTC-2 daylight time in Sao Paulo; today it uses UTC-3.
        stamp = 1231295252123456789
        os.utime(path, ns=(stamp, stamp))
        script = '''
import json, os, sys
from types import SimpleNamespace
from sambinha.archive_recovery import check_archive
from sambinha.process import Runner
archive, key, control = sys.argv[1:]
environment = dict(os.environ)
runner = Runner(env=environment)
storage = SimpleNamespace(job=SimpleNamespace(compressor='7z'), path=lambda key: archive)
check_archive(storage, runner, key, json.loads(control))
assert runner.env == environment == dict(os.environ)
'''
        for archive_format in ('7z', 'zip'):
            job = replace(self.config.jobs[0], logic='batch', archive_format=archive_format,
                          destination=str(self.root / archive_format))
            self.config = replace(self.config, jobs=(job,), state_dir=self.root / f'state-{archive_format}')
            self.assertEqual(self.execute(), 0)
            key, control = next(iter(self.state()['active'].items()))
            for zone in ('America/Sao_Paulo', 'America/New_York', 'Asia/Kolkata', 'UTC'):
                with self.subTest(archive_format=archive_format, timezone=zone):
                    result = subprocess.run([sys.executable, '-c', script, str(Path(job.destination) / key),
                                             key, json.dumps(control)], capture_output=True, text=True,
                                            env={**os.environ, 'TZ': zone})
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_batch_restore_uses_output_disk_and_removes_each_lots_temporary_files(self):
        from sambinha.archive_recovery import unpack
        from sambinha.storage import Storage
        from tests.test_disks import Usage
        for i in range(3):
            self.file(f'{i}/selected.txt', b'selected')
            self.file(f'{i}/unselected.txt', b'unselected')
        self.config = replace(self.config, jobs=(replace(self.config.jobs[0], logic='batch', max_megabytes=2 / 1024 ** 2),))
        self.assertEqual(self.execute(), 0)
        restored = self.root / 'mounted-output'
        restored.mkdir()
        lots, downloads = [], []
        get = Storage.get
        def download(storage, key, target):
            target = Path(target)
            self.assertTrue(target.is_relative_to(restored))
            downloads.append(target)
            return get(storage, key, target)
        def extract(storage, runner, key, control, work):
            self.assertTrue(work.is_relative_to(restored))
            self.assertTrue(all(not previous.exists() for previous in lots))
            self.assertFalse(any((restored / f'{i}/selected.txt').exists() for i in range(3)))
            lots.append(work)
            return unpack(storage, runner, key, control, work)
        def usage(path):
            self.assertNotEqual(Path(path), Path('/tmp'))
            return Usage(100, 10, 90)
        selectors = tuple(f'{i}/selected.txt' for i in range(3))
        with patch('sambinha.storage.Storage.get', download), patch('sambinha.restore.unpack', extract), \
             patch('shutil.disk_usage', side_effect=usage), \
             patch('sambinha.restore.shutil.copyfileobj', side_effect=AssertionError('unnecessary copy')):
            self.assertEqual(self.recover('restore', output=restored, selectors=selectors), 0)
        self.assertEqual(len(lots), 3)
        self.assertEqual(len(downloads), 3)
        self.assertEqual(contents(restored), {path: b'selected' for path in selectors})
        self.assertTrue(all(not path.exists() for path in lots))
        self.assertFalse(list(restored.glob('.sambinha-restore-*')))

    def test_late_batch_corruption_leaves_output_empty_and_cleans_workspace(self):
        self.file('a/file.txt', b'first')
        self.file('b/file.txt', b'second')
        self.config = replace(self.config, jobs=(replace(self.config.jobs[0], logic='batch', max_megabytes=6 / 1024 ** 2),))
        self.assertEqual(self.execute(), 0)
        keys = list(self.state()['active'])
        self.assertEqual(len(keys), 2)
        (self.destination / keys[-1]).write_bytes(b'corrupt')
        restored = self.root / 'restored'
        restored.mkdir()
        self.assertEqual(self.recover('restore', output=restored), 1)
        self.assertEqual(list(restored.iterdir()), [])

    def test_batch_restore_from_s3_downloads_into_output_without_system_temporary_space(self):
        from sambinha.state import atomic_json, identity
        from tests.test_disks import Usage
        self.file('dir/file.txt', b'payload')
        self.config = replace(self.config, jobs=(replace(self.config.jobs[0], logic='batch'),))
        self.assertEqual(self.execute(), 0)
        state = self.state()
        job = replace(self.config.jobs[0], type='s3', destination='aws:bucket/backup')
        state['identity'] = identity(job)
        atomic_json(self.config.state_dir / job.name / 'state.json', state)
        self.config = replace(self.config, jobs=(job,))
        restored = self.root / 'restored'
        def download(storage, key, target):
            target = Path(target)
            self.assertTrue(target.is_relative_to(restored))
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.destination / key, target)
        def usage(path):
            return Usage(100, 96, 4) if Path(path) == Path('/tmp') else Usage(100, 10, 90)
        with patch('sambinha.storage.Storage.get', download), patch('shutil.disk_usage', side_effect=usage):
            self.assertEqual(self.recover('restore', output=restored), 0)
        self.assertEqual(contents(restored), contents(self.source))

    def test_batch_restore_simulation_does_not_create_output_or_download(self):
        self.file('file.txt')
        self.config = replace(self.config, jobs=(replace(self.config.jobs[0], logic='batch'),))
        self.assertEqual(self.execute(), 0)
        self.config = with_job_overrides(self.config, dry_run=True)
        restored = self.root / 'missing-parent' / 'restored'
        with patch('sambinha.storage.Storage.get', side_effect=AssertionError('download')):
            self.assertEqual(self.recover('restore', output=restored), 0)
        self.assertFalse(restored.parent.exists())

    def test_corruption_is_detected_before_any_restore_file_is_published(self):
        self.file('a', b'original')
        self.file('b', b'original')
        self.assertEqual(self.execute(), 0)
        (self.destination / 'b').write_bytes(b'corrupt!')
        self.assertEqual(self.recover('verify-local-metadata'), 1)
        restored = self.root / 'restored'
        self.assertEqual(self.recover('restore', output=restored), 0)
        self.assertEqual((restored / 'b').read_bytes(), b'corrupt!')

    def test_restore_refuses_overwrite_overlap_and_invalid_selection(self):
        self.file('file')
        self.assertEqual(self.execute(), 0)
        existing = self.root / 'existing'
        existing.mkdir()
        (existing / 'keep').write_bytes(b'keep')
        for output, selectors in ((existing, ()), (self.source, ()),
                                  (self.root / 'restored', ('../escape',)),
                                  (self.root / 'restored', ('missing',))):
            with self.subTest(output=output, selectors=selectors):
                self.assertEqual(self.recover('restore', output=output, selectors=selectors), 1)
        self.assertEqual((existing / 'keep').read_bytes(), b'keep')

    def test_restore_simulation_has_no_downloads_or_output_writes(self):
        self.file('file')
        self.assertEqual(self.execute(), 0)
        self.config = with_job_overrides(self.config, dry_run=True)
        restored = self.root / 'restored'
        with patch('sambinha.storage.Storage.get', side_effect=AssertionError('download')):
            self.assertEqual(self.recover('restore', output=restored), 0)
        self.assertFalse(restored.exists())

    def test_periodic_verification_only_runs_when_due_and_failure_keeps_last_success(self):
        self.file('file', b'original')
        self.config = with_job_overrides(replace(self.config, jobs=(replace(self.config.jobs[0], logic='batch'),)), verify_interval_days=7)
        self.assertEqual(self.execute(now=1000), 0)
        self.assertEqual(self.state()['last_verified'], 1000)
        self.assertEqual(self.execute(now=1001), 0)
        self.assertFalse(any(e['event'] == 'verification_complete' for e in self.events))
        with patch('sambinha.verification.verify_local_metadata_job', side_effect=BackupError('corrupt')):
            self.assertEqual(self.execute(now=1000 + 7 * 86400), 1)
        self.assertEqual(self.state()['last_verified'], 1000)

    def test_summary_counts_new_modified_removed_reused_and_planned_bytes(self):
        changed = self.file('changed', b'old')
        deleted = self.file('deleted', b'old')
        self.file('unchanged', b'old')
        self.config = replace(self.config, jobs=(replace(self.config.jobs[0], logic='batch'),))
        self.assertEqual(self.execute(), 0)
        changed.write_bytes(b'new content')
        deleted.unlink()
        self.file('new', b'new')
        self.assertEqual(self.execute(with_job_overrides(self.config, dry_run=True)), 0)
        summary = next(e for e in self.events if e['event'] == 'summary')
        self.assertEqual((summary['new_files'], summary['modified_files'], summary['removed_files'],
                          summary['unchanged_files']), (1, 1, 1, 1))
        self.assertEqual(summary['bytes_planned'], len(b'new content') + len(b'new') + len(b'old'))
        self.assertEqual(summary['bytes_transferred'], 0)

    def test_cli_dispatches_restore_and_verify_with_configuration_inheritance(self):
        from sambinha.cli import main
        self.file('file', b'original')
        self.assertEqual(self.execute(), 0)
        config_file = self.root / 'config.toml'
        config_file.write_text('[[jobs]]\nname="test"\nsource="source"\ndestination="destination"\n')
        output = self.root / 'restored'
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main(['verify-local-metadata', '--config', str(config_file), '--job', 'test']), 0)
            self.assertEqual(main(['restore', '--config', str(config_file), '--job', 'test',
                                  '--output', str(output), '--path', 'file']), 0)
        self.assertEqual((output / 'file').read_bytes(), b'original')
