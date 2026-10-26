import json
import os
import shutil
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from sambinha.batches import plan
from sambinha.errors import BackupError
from sambinha.inventory import scan
from sambinha.metadata import control_directory, prepare_controls
from sambinha.recovery import recover
from sambinha.state import atomic_json, identity
from sambinha.storage import Storage
from sambinha.verification import verify_destination_metadata_job, verify_local_metadata_job
from tests.helpers import REAL_TOOLS, Sandbox


class DailyMetadataTests(Sandbox):
    def baseline(self):
        self.file('a.txt', b'alpha')
        self.file('b.txt', b'beta')
        job = replace(self.config.jobs[0], logic='batch', type='s3', destination='aws:bucket/prefix')
        self.config = replace(self.config, jobs=(job,), metadata_dir=self.root / 'metadata')
        batches = plan(scan(self.source, mode='simple'), job.max_bytes)
        active = {}
        for batch in batches:
            active[batch.key] = {**batch.control(), 'archive_size': 200, 'archive_md5': 'a' * 32}
        state = {'schema': 1, 'identity': identity(job), 'active': active, 'retired': []}
        atomic_json(self.config.state_dir / job.name / 'state.json', state)
        prepare_controls(self.config, job, state)
        return job, state

    def daily(self, job):
        runner = Mock()
        with patch.object(Storage, 'get', side_effect=AssertionError('remote read')), \
             patch.object(Storage, 'stat', side_effect=AssertionError('remote stat')), \
             patch('sambinha.inventory.fingerprint', side_effect=AssertionError('source hash')):
            result = verify_local_metadata_job(self.config, job, runner, Mock())
        runner.run.assert_not_called()
        return result

    def test_daily_uses_configurable_local_jsons_without_remote_access(self):
        job, state = self.baseline()
        result = self.daily(job)
        self.assertFalse(result['needs_update'])
        self.assertEqual(result['unchanged_files'], 2)
        for key, control in state['active'].items():
            self.assertEqual(json.loads((self.root / 'metadata' / job.name / (key + '.json')).read_text()), control)

    def test_daily_reports_new_changed_deleted_files_and_affected_batches(self):
        job, _ = self.baseline()
        before = (self.config.state_dir / job.name / 'state.json').read_bytes()
        (self.source / 'a.txt').write_bytes(b'changed')
        (self.source / 'b.txt').unlink()
        self.file('c.txt', b'new')
        result = self.daily(job)
        self.assertTrue(result['needs_update'])
        self.assertEqual((result['new_files'], result['modified_files'], result['removed_files']), (1, 1, 1))
        self.assertEqual(result['batches_to_update'], 1)
        self.assertEqual(result['new_batches'], 1)
        self.assertEqual((self.config.state_dir / job.name / 'state.json').read_bytes(), before)

    def test_daily_deliberately_ignores_content_changes_with_same_size_and_date(self):
        job, _ = self.baseline()
        path = self.source / 'a.txt'
        stamp = path.stat().st_mtime_ns
        path.write_bytes(b'other')
        os.utime(path, ns=(stamp, stamp))
        self.assertFalse(self.daily(job)['needs_update'])

    def test_daily_reports_format_changes_but_limit_changes_preserve_groups(self):
        job, _ = self.baseline()
        for changed, needs_update in ((replace(job, archive_format='zip'), True), (replace(job, max_megabytes=4 / 1024 ** 2), False)):
            with self.subTest(job=changed):
                self.config = replace(self.config, jobs=(changed,))
                result = self.daily(changed)
                self.assertEqual(result['needs_update'], needs_update)
                self.assertEqual(result['modified_files'], 0)
                self.assertEqual(result['batches_to_update'], int(needs_update))

    def test_daily_rejects_missing_source_and_preserves_confirmed_catalog(self):
        job, _ = self.baseline()
        root = control_directory(self.config, job)
        before = (root / '.index.json').read_bytes()
        shutil.rmtree(self.source)
        with self.assertRaises(BackupError):
            self.daily(job)
        self.assertEqual((root / '.index.json').read_bytes(), before)

    def test_daily_can_use_cache_manifest_without_backup_state(self):
        job, _ = self.baseline()
        shutil.rmtree(self.config.state_dir)
        self.assertFalse(self.daily(job)['needs_update'])
        self.assertFalse(self.config.state_dir.exists())

    def test_first_daily_check_reports_initial_backup_without_remote_access(self):
        self.file('new.txt', b'new')
        job = replace(self.config.jobs[0], logic='batch', type='s3', destination='aws:bucket')
        self.config = replace(self.config, jobs=(job,), metadata_dir=self.root / 'metadata')
        result = self.daily(job)
        self.assertTrue(result['needs_update'])
        self.assertEqual(result['new_files'], 1)

    def test_monthly_checks_json_sync_without_downloading_archives(self):
        job, state = self.baseline()
        key = next(iter(state['active']))
        def download(storage, name, target):
            self.assertEqual(name, key + '.json')
            Path(target).write_text(json.dumps(state['active'][key]))
        with patch.object(Storage, 'get', download), patch.object(Storage, 'stat', return_value={'Size': 200}):
            result = verify_destination_metadata_job(self.config, job, Mock(), Mock())
        self.assertEqual(result['batches'], 1)

    def test_monthly_detects_remote_json_changes_and_missing_controls(self):
        job, state = self.baseline()
        key = next(iter(state['active']))
        def changed(storage, name, target):
            control = json.loads(json.dumps(state['active'][key]))
            control['files'][0]['mtime_ns'] += 1000
            Path(target).write_text(json.dumps(control))
        with patch.object(Storage, 'get', changed), patch.object(Storage, 'stat', return_value={'Size': 200}), \
             self.assertRaises(BackupError):
            verify_destination_metadata_job(self.config, job, Mock(), Mock())
        with patch.object(Storage, 'get', side_effect=BackupError('missing JSON')), \
             patch.object(Storage, 'stat', return_value={'Size': 200}), self.assertRaises(BackupError):
            verify_destination_metadata_job(self.config, job, Mock(), Mock())

    def test_cache_manifest_rejects_another_jobs_identity_and_symlinks(self):
        job, _ = self.baseline()
        shutil.rmtree(self.config.state_dir)
        manifest = control_directory(self.config, job) / '.index.json'
        data = json.loads(manifest.read_text())
        data['identity']['destination'] = 'aws:other'
        manifest.write_text(json.dumps(data))
        with self.assertRaises(BackupError):
            self.daily(job)
        manifest.unlink()
        manifest.symlink_to(self.source / 'a.txt')
        with self.assertRaises(BackupError):
            self.daily(job)


@unittest.skipUnless(REAL_TOOLS, 'rclone and 7z required')
class MetadataPublicationTests(Sandbox):
    def setUp(self):
        super().setUp()
        self.config = replace(self.config, metadata_dir=self.root / 'metadata',
                              jobs=(replace(self.config.jobs[0], logic='batch', max_megabytes=20 / 1024 ** 2),))
        self.file('a.txt', b'alpha')
        self.file('b.txt', b'beta')

    def assert_replicas(self):
        state = self.state()
        root = control_directory(self.config, self.config.jobs[0])
        for key in state['active']:
            self.assertEqual((root / (key + '.json')).read_bytes(), (self.destination / (key + '.json')).read_bytes())
        self.assertEqual(json.loads((root / '.index.json').read_text())['active'], sorted(state['active']))

    def test_updated_batches_publish_identical_local_and_remote_jsons(self):
        self.assertEqual(self.execute(), 0)
        self.assert_replicas()
        (self.source / 'a.txt').write_bytes(b'new content')
        self.assertEqual(self.execute(now=2000), 0)
        self.assert_replicas()
        original = Storage.put
        def forbid_control_update(storage, source, key, **kwargs):
            self.assertFalse(key.endswith('.json'))
            return original(storage, source, key, **kwargs)
        with patch.object(Storage, 'put', forbid_control_update):
            self.assertEqual(self.execute(now=2001), 0)

    def test_remote_json_failure_does_not_advance_local_state_or_manifest_and_retry_repairs(self):
        self.assertEqual(self.execute(), 0)
        before = self.state()
        root = control_directory(self.config, self.config.jobs[0])
        manifest = (root / '.index.json').read_bytes()
        (self.source / 'a.txt').write_bytes(b'new content')
        original = Storage.put
        def fail(storage, source, key, **kwargs):
            if key.endswith('.json'):
                raise BackupError('JSON remoto falhou')
            return original(storage, source, key, **kwargs)
        with patch.object(Storage, 'put', fail):
            self.assertEqual(self.execute(now=2000), 1)
        self.assertEqual(self.state(), before)
        self.assertEqual((root / '.index.json').read_bytes(), manifest)
        self.assertEqual(self.execute(now=2001), 0)
        self.assert_replicas()

    def test_local_json_failure_does_not_advance_state_and_retry_repairs(self):
        self.assertEqual(self.execute(), 0)
        before = self.state()
        root = control_directory(self.config, self.config.jobs[0])
        manifest = (root / '.index.json').read_bytes()
        (self.source / 'a.txt').write_bytes(b'new content')
        from sambinha.metadata import atomic_json as save
        def fail(path, control, **kwargs):
            if path.name != '.index.json' and path.is_relative_to(root):
                raise OSError('JSON local falhou')
            return save(path, control, **kwargs)
        with patch('sambinha.metadata.atomic_json', fail):
            self.assertEqual(self.execute(now=2000), 1)
        self.assertEqual(self.state(), before)
        self.assertEqual((root / '.index.json').read_bytes(), manifest)
        self.assertEqual(self.execute(now=2001), 0)
        self.assert_replicas()

    def test_manual_integrity_is_separate_from_daily_verification(self):
        self.assertEqual(self.execute(), 0)
        key = next(iter(self.state()['active']))
        (self.destination / key).write_bytes(b'corrupt')
        with patch.object(Storage, 'get', side_effect=AssertionError('daily archive read')):
            self.assertFalse(verify_local_metadata_job(self.config, self.config.jobs[0], Mock(), Mock())['needs_update'])
        self.assertEqual(recover(self.config, 'verify-destination-integrity'), 1)

    def test_recovery_index_failure_keeps_previous_active_catalog_until_retry(self):
        self.assertEqual(self.execute(), 0)
        before = self.state()
        root = control_directory(self.config, self.config.jobs[0])
        manifest = (root / '.index.json').read_bytes()
        (self.source / 'a.txt').write_bytes(b'new content')
        original = Storage.put
        def fail(storage, source, key, **kwargs):
            if key == '.sambinha/control.tar.gz':
                raise BackupError('índice remoto falhou')
            return original(storage, source, key, **kwargs)
        with patch.object(Storage, 'put', fail):
            self.assertEqual(self.execute(now=2000), 1)
        self.assertEqual(self.state(), before)
        self.assertEqual((root / '.index.json').read_bytes(), manifest)
        self.assertEqual(self.execute(now=2001), 0)
        self.assert_replicas()

    def test_archive_failure_does_not_publish_new_jsons_or_advance_state(self):
        self.assertEqual(self.execute(), 0)
        before = self.state()
        root = control_directory(self.config, self.config.jobs[0])
        controls = {p.relative_to(root): p.read_bytes() for p in root.rglob('*.json')}
        (self.source / 'a.txt').write_bytes(b'new content')
        called = []
        def fail(storage, source, key, **kwargs):
            called.append(key)
            raise BackupError('lote falhou')
        with patch.object(Storage, 'put', fail):
            self.assertEqual(self.execute(now=2000), 1)
        self.assertTrue(all(key.endswith('.7z') for key in called))
        self.assertEqual(self.state(), before)
        self.assertEqual({p.relative_to(root): p.read_bytes() for p in root.rglob('*.json')}, controls)
