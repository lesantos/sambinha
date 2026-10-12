from contextlib import redirect_stdout
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import shutil
import unittest
from unittest.mock import Mock, patch

from sambinha.batches import plan
from sambinha.config import validate
from sambinha.errors import BackupError
from sambinha.inventory import scan
from sambinha.recovery import recover, verify_job
from tests.helpers import REAL_TOOLS, Sandbox, contents


class S3MetadataVerificationTests(Sandbox):
    def test_simple_verification_never_downloads_objects(self):
        self.file('dir/file', b'original')
        item = scan(self.source)[0].metadata()
        job = replace(self.config.jobs[0], type='s3', destination='aws:bucket/prefix')
        remote = {'Size': item['size'], 'Hashes': {'md5': item['md5']}}
        runner = Mock()
        with patch('sambinha.storage.Storage.stat', return_value=remote) as stat, \
             patch('sambinha.storage.Storage.get', side_effect=AssertionError('download')):
            result = verify_job(self.config, job, runner, Mock(), {'active': {item['path']: item}})
        self.assertEqual(result['mode'], 's3_metadata')
        stat.assert_called_once_with('dir/file')
        runner.run.assert_not_called()

    def test_batch_archive_and_control_are_checked_without_download(self):
        self.file('file', b'original')
        batch = plan(scan(self.source), 1000, 2)[0]
        control = batch.control()
        control.update(archive_size=200, archive_md5='a' * 32)
        encoded = (json.dumps(control, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode()
        import hashlib
        remotes = [{'Size': 200, 'Hashes': {'md5': 'a' * 32}},
                   {'Size': len(encoded), 'Hashes': {'md5': hashlib.md5(encoded).hexdigest()}}]
        job = replace(self.config.jobs[0], type='s3', logic='batch', destination='aws:bucket')
        with patch('sambinha.storage.Storage.stat', side_effect=remotes) as stat, \
             patch('sambinha.storage.Storage.get', side_effect=AssertionError('download')):
            verify_job(self.config, job, Mock(), Mock(), {'active': {batch.key: control}})
        self.assertEqual([c.args[0] for c in stat.call_args_list], [batch.key, batch.key + '.json'])

    def test_missing_hash_or_wrong_checksum_fails_without_download(self):
        self.file('file')
        item = scan(self.source)[0].metadata()
        job = replace(self.config.jobs[0], type='s3', destination='aws:bucket')
        for remote in ({'Size': item['size'], 'Hashes': {}},
                       {'Size': item['size'], 'Hashes': {'md5': '0' * 32}}):
            with self.subTest(remote=remote), patch('sambinha.storage.Storage.stat', return_value=remote), \
                 patch('sambinha.storage.Storage.get', side_effect=AssertionError('download')), \
                 self.assertRaises(BackupError):
                verify_job(self.config, job, Mock(), Mock(), {'active': {'file': item}})

    def test_missing_local_s3_index_fails_without_downloading_recovery_archive(self):
        job = replace(self.config.jobs[0], type='s3', destination='aws:bucket', logic='batch')
        with patch('sambinha.storage.Storage.get', side_effect=AssertionError('download')), self.assertRaises(BackupError):
            verify_job(self.config, job, Mock(), Mock())

    def test_legacy_batch_without_archive_hash_fails_closed(self):
        self.file('file')
        batch = plan(scan(self.source), 1000, 2)[0]
        job = replace(self.config.jobs[0], type='s3', destination='aws:bucket', logic='batch')
        with patch('sambinha.storage.Storage.get', side_effect=AssertionError('download')), self.assertRaises(BackupError):
            verify_job(self.config, job, Mock(), Mock(), {'active': {batch.key: batch.control()}})


@unittest.skipUnless(REAL_TOOLS, 'rclone and 7z required')
class RecoveryIntegrationTests(Sandbox):
    def recover(self, action, **kwargs):
        output = io.StringIO()
        with redirect_stdout(output):
            result = recover(validate(self.config), action, **kwargs)
        self.recovery_events = [json.loads(line) for line in output.getvalue().splitlines()]
        return result

    def test_destination_option_controls_restore_output_but_not_temporary_space(self):
        from tests.test_disks import Usage
        self.file('file')
        self.assertEqual(self.execute(), 0)
        self.config = replace(self.config, jobs=(replace(self.config.jobs[0],
                              check_source_disk=False, check_destination_disk=False),))
        restored = self.root / 'restored'
        measured = []
        def usage(path):
            measured.append(Path(path))
            return Usage(0, 0, 0) if Path(path) in (restored, self.root) else Usage(100, 10, 90)
        with patch('shutil.disk_usage', side_effect=usage):
            self.assertEqual(self.recover('restore', output=restored), 0)
        self.assertEqual(contents(restored), contents(self.source))
        self.assertNotIn(restored, measured)
        self.assertNotIn(self.root, measured)
        self.assertTrue(any('sambinha-restore-' in str(path) for path in measured))
        with patch('shutil.disk_usage', return_value=Usage(100, 96, 4)):
            self.assertEqual(self.recover('restore', output=self.root / 'full-temp'), 1)
        error = next(e for e in self.recovery_events if e['event'] == 'error')
        self.assertEqual(error['job'], 'test')
        self.assertIn('temporários', error['error'])
        self.assertFalse((self.root / 'full-temp').exists())

    def test_disabling_output_disk_check_preserves_restore_path_validation(self):
        self.file('file')
        self.assertEqual(self.execute(), 0)
        self.config = replace(self.config, jobs=(replace(self.config.jobs[0], check_destination_disk=False),))
        self.assertEqual(self.recover('restore', output=self.source), 1)
        self.assertEqual((self.source / 'file').read_bytes(), b'test')

    def test_simple_restore_selected_directory_preserves_binary_and_permissions(self):
        self.file('dir/binary.dat', bytes(range(256)), mode=0o750)
        self.file('dir/ação.txt', 'Olá'.encode())
        self.file('other', b'not selected')
        self.assertEqual(self.execute(), 0)
        expected = {p: b for p, b in contents(self.source).items() if p.startswith('dir/')}
        shutil.rmtree(self.source)
        restored = self.root / 'restored'
        self.assertEqual(self.recover('restore', output=restored, selectors=('dir',)), 0)
        self.assertEqual(contents(restored), expected)
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

    def test_corruption_is_detected_before_any_restore_file_is_published(self):
        self.file('a', b'original')
        self.file('b', b'original')
        self.assertEqual(self.execute(), 0)
        (self.destination / 'b').write_bytes(b'corrupt!')
        restored = self.root / 'restored'
        self.assertEqual(self.recover('restore', output=restored), 1)
        self.assertFalse(restored.exists())
        self.assertEqual(self.recover('verify'), 1)

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
        self.config = replace(self.config, dry_run=True)
        restored = self.root / 'restored'
        with patch('sambinha.storage.Storage.get', side_effect=AssertionError('download')):
            self.assertEqual(self.recover('restore', output=restored), 0)
        self.assertFalse(restored.exists())

    def test_periodic_verification_only_runs_when_due_and_failure_keeps_last_success(self):
        self.file('file', b'original')
        self.config = replace(self.config, verify_interval_days=7)
        self.assertEqual(self.execute(now=1000), 0)
        self.assertEqual(self.state()['last_verified'], 1000)
        self.assertEqual(self.execute(now=1001), 0)
        self.assertFalse(any(e['event'] == 'verification_complete' for e in self.events))
        with patch('sambinha.recovery.verify_job', side_effect=BackupError('corrupt')):
            self.assertEqual(self.execute(now=1000 + 7 * 86400), 1)
        self.assertEqual(self.state()['last_verified'], 1000)

    def test_summary_counts_new_modified_removed_reused_and_planned_bytes(self):
        changed = self.file('changed', b'old')
        deleted = self.file('deleted', b'old')
        self.file('unchanged', b'old')
        self.assertEqual(self.execute(), 0)
        changed.write_bytes(b'new content')
        deleted.unlink()
        self.file('new', b'new')
        self.assertEqual(self.execute(replace(self.config, dry_run=True)), 0)
        summary = next(e for e in self.events if e['event'] == 'summary')
        self.assertEqual((summary['new_files'], summary['modified_files'], summary['removed_files'],
                          summary['unchanged_files']), (1, 1, 1, 1))
        self.assertEqual(summary['bytes_planned'], len(b'new content') + len(b'new'))
        self.assertEqual(summary['bytes_transferred'], 0)

    def test_cli_dispatches_restore_and_verify_with_configuration_inheritance(self):
        from sambinha.cli import main
        self.file('file', b'original')
        self.assertEqual(self.execute(), 0)
        config_file = self.root / 'config.toml'
        config_file.write_text('[[jobs]]\nname="test"\nsource="source"\ndestination="destination"\n')
        output = self.root / 'restored'
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main(['verify', '--config', str(config_file), '--job', 'test']), 0)
            self.assertEqual(main(['restore', '--config', str(config_file), '--job', 'test',
                                  '--output', str(output), '--path', 'file']), 0)
        self.assertEqual((output / 'file').read_bytes(), b'original')
