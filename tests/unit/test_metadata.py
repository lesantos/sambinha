import json
import os
import shutil
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from sambinha.batches import plan
from sambinha.context import Context
from sambinha.errors import BackupError
from sambinha.filtering import Selection
from sambinha.inventory import scan
from sambinha.metadata import cached_control, control_directory, prepare_controls, read_control, write_control
from sambinha.state import atomic_json, identity
from sambinha.storage import Storage
from sambinha.verification import verify_destination_metadata_job, verify_local_metadata_job
from tests.helpers import Sandbox


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

    def test_daily_follows_the_job_source_check_setting(self):
        for setting in (True, False):
            with self.subTest(verify_source_changes=setting):
                job, _ = self.baseline()
                job = replace(job, verify_source_changes=setting)
                with patch('sambinha.verification.scan', wraps=scan) as walk:
                    verify_local_metadata_job(self.config, job, Mock(), Mock())
                self.assertIs(walk.call_args.kwargs['verify_source_changes'], setting)

    def test_daily_uses_the_listing_inventory_and_skips_link_checks_for_present_paths(self):
        job, _ = self.baseline()
        job = replace(job, inventory_from_listing=True)
        seen = []
        original = Selection.include_paths
        def spy(selection, paths, current=()):
            seen.append(set(current))
            return original(selection, paths, current)
        with patch('sambinha.verification.scan_from_listing', side_effect=lambda *a, **k: scan(self.source, mode='simple')) as listed, \
             patch('sambinha.verification.scan', side_effect=AssertionError('walk')), \
             patch.object(Selection, 'include_paths', spy):
            result = verify_local_metadata_job(self.config, job, Mock(), Mock())
        listed.assert_called_once()
        self.assertEqual(seen, [{'a.txt', 'b.txt'}])
        self.assertFalse(result['needs_update'])

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


class ControlMemoTests(Sandbox):
    def setup_job(self):
        self.file('a.txt', b'alpha')
        job = replace(self.config.jobs[0], logic='batch', type='s3', destination='aws:bucket/prefix')
        self.config = replace(self.config, jobs=(job,), metadata_dir=self.root / 'metadata')
        (batch,) = plan(scan(self.source, mode='simple'), job.max_bytes)
        control = {**batch.control(), 'archive_size': 200, 'archive_md5': 'a' * 32}
        return job, batch.key, control

    def test_memoized_control_is_read_once_per_execution(self):
        job, key, control = self.setup_job()
        write_control(self.config, job, key, control)
        memo = {}
        with patch('sambinha.metadata.read_control', wraps=read_control) as reads:
            for _ in range(3):
                self.assertEqual(cached_control(self.config, job, key, control, memo=memo), control)
        self.assertEqual(reads.call_count, 1)
        with patch('sambinha.metadata.read_control', wraps=read_control) as reads:
            cached_control(self.config, job, key, control)
            cached_control(self.config, job, key, control)
        self.assertEqual(reads.call_count, 2)

    def test_writing_a_control_invalidates_the_memo_and_revalidates(self):
        job, key, control = self.setup_job()
        write_control(self.config, job, key, control)
        memo = {}
        cached_control(self.config, job, key, control, memo=memo)
        path = control_directory(self.config, job) / (key + '.json')
        write_control(self.config, job, key, {**control, 'archive_size': 999}, memo=memo)
        self.assertNotIn(key, memo)
        with self.assertRaisesRegex(BackupError, 'diverge do índice'):
            cached_control(self.config, job, key, control, memo=memo)
        write_control(self.config, job, key, control, memo=memo)
        with patch('sambinha.metadata.read_control', wraps=read_control) as reads:
            cached_control(self.config, job, key, control, memo=memo)
        self.assertEqual(reads.call_count, 1)
        self.assertTrue(path.is_file())

    def test_memo_does_not_hide_a_different_expectation(self):
        job, key, control = self.setup_job()
        write_control(self.config, job, key, control)
        memo = {}
        cached_control(self.config, job, key, control, memo=memo)
        with self.assertRaisesRegex(BackupError, 'diverge do índice'):
            cached_control(self.config, job, key, {**control, 'archive_size': 1}, memo=memo)

    def test_context_validates_controls_once_and_batch_reuses_the_result(self):
        job, key, control = self.setup_job()
        state = {'schema': 1, 'identity': identity(job), 'active': {key: control}, 'retired': []}
        write_control(self.config, job, key, control)
        with patch('sambinha.metadata.read_control', wraps=read_control) as reads:
            ctx = Context(self.config, job, Mock(), Mock(), scan(self.source, mode='simple'), 1000, state=state)
            cached_control(ctx.config, ctx.job, key, control, memo=ctx.control_memo)
        self.assertEqual(reads.call_count, 1)
