from contextlib import redirect_stdout
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import shutil
import tarfile
import unittest
import zipfile
from unittest.mock import patch

from sambinha.errors import BackupError
from sambinha.config import load, validate
from sambinha.engine import DAY
from sambinha.process import Runner
from sambinha.recovery import recover
from sambinha.storage import Storage
from tests.helpers import REAL_TOOLS, Sandbox, contents, restore_batches


@unittest.skipUnless(REAL_TOOLS, 'Install rclone and 7z for real local integration')
class LocalIntegrationTests(Sandbox):
    def fixture(self):
        fixture = Path(__file__).parent / 'fixtures' / 'source'
        shutil.copytree(fixture, self.source, dirs_exist_ok=True)
        self.file('empty', b'')
        self.file('ação/with spaces.txt', 'Olá!'.encode())
        self.file('ação/@leading.txt', b'@file')
        self.file('ação/-leading.txt', b'-file')
        self.file('ação/[wildcard]*?.txt', b'literal wildcards')
        self.file('binary.dat', bytes(range(256)) * 100)

    def use_batch(self, **values):
        self.config = replace(self.config, jobs=(replace(self.config.jobs[0], logic='batch',
                               max_megabytes=100 / 1024, **values),))

    def assert_complete(self, logic=None):
        logic = logic or self.config.jobs[0].logic
        if logic == 'simple':
            self.assertEqual(contents(self.destination), contents(self.source))
        else:
            restored = self.root / ('restored-' + str(len(list(self.root.glob('restored-*')))))
            actual = restore_batches(self.state(), self.destination, restored)
            self.assertEqual(actual, contents(self.source))
            active_keys = set(self.state()['active'])
            for key in active_keys:
                self.assertTrue((self.destination / key).is_file())
                self.assertTrue((self.destination / (key + '.json')).is_file())
            remote_files = contents(self.destination)
            original_paths = set(contents(self.source))
            self.assertFalse(original_paths.intersection(remote_files))
            for item in self.state()['active'].values():
                self.assertTrue(item['files'])

    def test_simple_complete_byte_exact_and_metadata(self):
        self.fixture()
        self.file('private.sh', b'#!/bin/sh\nexit 0\n', mode=0o750)
        (self.source / 'empty_directory').mkdir(mode=0o750)
        self.assertEqual(self.execute(), 0, self.output.getvalue())
        self.assert_complete()
        for source in self.source.rglob('*'):
            target = self.destination / source.relative_to(self.source)
            self.assertTrue(target.exists())
            if source.is_file():
                self.assertEqual(source.stat().st_mode & 0o777, target.stat().st_mode & 0o777)
                self.assertAlmostEqual(source.stat().st_mtime, target.stat().st_mtime, delta=1)
        self.assertEqual([e['event'] for e in self.events if e['event'] in ('start', 'end')], ['start', 'end'])
        self.assertEqual([n[1] for n in self.notifications], ['run', 'complete'])

    def test_simple_identical_skipped_and_same_size_edit_detected(self):
        target = self.file('file', b'abc')
        self.assertEqual(self.execute(), 0)
        old_time = target.stat().st_mtime_ns
        self.assertEqual(self.execute(), 0)
        self.assertTrue(any(e['event'] == 'sync_start' for e in self.events))
        target.write_bytes(b'xyz')
        os.utime(target, ns=(old_time, old_time))
        self.assertEqual(self.execute(now=2000), 0, self.output.getvalue())
        self.assert_complete()
        history = list(self.destination.glob('.sambinha/history/*/file'))
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].read_bytes(), b'abc')

    def test_simple_destination_corruption_repaired(self):
        self.file('file', b'original')
        self.assertEqual(self.execute(), 0)
        (self.destination / 'file').write_bytes(b'corrupt!')
        ns = (self.source / 'file').stat().st_mtime_ns
        os.utime(self.destination / 'file', ns=(ns, ns))
        self.assertEqual(self.execute(), 0)
        self.assert_complete()

    def test_simple_retention_deleted_10_days_versions_2_days(self):
        changed, deleted = self.file('changed', b'old'), self.file('deleted', b'keep')
        self.assertEqual(self.execute(now=1000), 0)
        changed.write_bytes(b'new')
        deleted.unlink()
        self.assertEqual(self.execute(now=2000), 0)
        history = list(self.destination.glob('.sambinha/history/*/changed'))
        self.assertEqual(len(history), 1)
        history_dir = history[0].parent
        self.assertEqual((history_dir / 'changed').read_bytes(), b'old')
        self.assertEqual((history_dir / 'deleted').read_bytes(), b'keep')
        self.assertEqual(self.execute(now=2000 + 10 * DAY - 1), 0)
        self.assertTrue(history_dir.exists())
        self.assertEqual(self.execute(now=2000 + 10 * DAY), 0)
        self.assertFalse(history_dir.exists())
        self.assert_complete()

    def test_simple_file_directory_replacements(self):
        self.file('node', b'file')
        self.assertEqual(self.execute(), 0)
        (self.source / 'node').unlink()
        self.file('node/child', b'child')
        self.assertEqual(self.execute(), 0, self.output.getvalue())
        self.assert_complete()
        history = list(self.destination.glob('.sambinha/history/*/node'))
        self.assertTrue(any(path.is_file() and path.read_bytes() == b'file' for path in history))
        shutil.rmtree(self.source / 'node')
        self.file('node', b'file again')
        self.assertEqual(self.execute(), 0, self.output.getvalue())
        self.assert_complete()
        self.assertTrue(any(path.is_dir() and (path / 'child').read_bytes() == b'child'
                            for path in self.destination.glob('.sambinha/history/*/node')))

    def test_simple_empty_source_retains_removed_files(self):
        self.file('a/b', b'backup')
        self.assertEqual(self.execute(), 0)
        shutil.rmtree(self.source / 'a')
        self.assertEqual(self.execute(), 0)
        self.assert_complete()
        history = list(self.destination.glob('.sambinha/history/*/a/b'))
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].read_bytes(), b'backup')

    def test_batch_roundtrip_completeness_and_control_backup(self):
        self.fixture()
        self.use_batch()
        self.assertEqual(self.execute(), 0, self.output.getvalue())
        self.assert_complete()
        mirror = self.config.state_dir / 'test' / 'controls'
        self.assertFalse(list(self.config.state_dir.rglob('*.7z')))
        for key, control in self.state()['active'].items():
            self.assertEqual(json.loads((mirror / (key + '.json')).read_text()), control)
        with tarfile.open(self.destination / '.sambinha/control.tar.gz') as tar:
            recovered = json.load(tar.extractfile('state.json'))
            self.assertEqual(recovered, self.state())
            self.assertTrue(all('controls/' + key + '.json' in tar.getnames() for key in recovered['active']))

    def test_batch_unchanged_second_run_has_no_transfers(self):
        self.fixture()
        self.use_batch()
        self.assertEqual(self.execute(), 0)
        self.assertEqual(self.execute(), 0)
        self.assertEqual(sum(e['event'] == 'identical' for e in self.events), len(contents(self.source)))
        self.assertFalse(any(e['event'] == 'transferred' for e in self.events))

    def test_batch_edit_add_delete_all_files_restorable(self):
        self.fixture()
        self.use_batch()
        self.assertEqual(self.execute(), 0)
        old = self.state()
        (self.source / 'binary.dat').write_bytes(b'new bytes')
        (self.source / 'empty').unlink()
        self.file('new_directory/new.txt', b'new file')
        self.assertEqual(self.execute(now=2000), 0, self.output.getvalue())
        self.assert_complete()
        self.assertTrue(self.state()['retired'])
        for record in self.state()['retired']:
            self.assertTrue((self.destination / record['key']).exists())
            self.assertEqual(json.loads((self.destination / (record['key'] + '.json')).read_text()), old['active'][record['key']])
        self.assertEqual(self.execute(now=2000 + 10 * DAY), 0)
        self.assertEqual(self.state()['retired'], [])
        self.assert_complete()

    def test_batch_deleted_and_modified_mixed_lot_longest_retention(self):
        self.file('a', b'old')
        self.file('b', b'deleted')
        self.use_batch()
        self.assertEqual(self.execute(), 0)
        (self.source / 'a').write_bytes(b'new')
        (self.source / 'b').unlink()
        self.assertEqual(self.execute(now=2000), 0)
        self.assertEqual(self.state()['retired'][0]['expires'], 2000 + 10 * DAY)
        self.assertEqual(self.execute(now=2000 + 2 * DAY), 0)
        self.assert_complete()

    def test_batch_oversized_and_same_stem_roundtrip(self):
        # A 100 KiB file exceeds a deliberately smaller 32 KiB batch limit.
        self.file('dir/big.txt', bytes(range(256)) * 400)
        self.file('dir/big.csv', bytes(range(256)) * 400)
        self.use_batch()
        self.config = replace(self.config, jobs=(replace(self.config.jobs[0], max_megabytes=32 / 1024),))
        self.assertEqual(self.execute(), 0, self.output.getvalue())
        self.assert_complete()
        self.assertEqual(len(self.state()['active']), 2)
        self.assertTrue(all(k.endswith('big.7z') for k in self.state()['active']))

    def test_oversized_zip_is_uploaded_unchanged_and_restores(self):
        original = self.source / 'backup.zip'
        with zipfile.ZipFile(original, 'w', compression=zipfile.ZIP_STORED) as archive:
            archive.writestr('inside.txt', 'conteúdo já compactado ' * 300)
        original_bytes = original.read_bytes()
        self.use_batch()
        self.config = replace(self.config, jobs=(replace(self.config.jobs[0], max_megabytes=1 / 1024),))
        self.assertEqual(self.execute(), 0, self.output.getvalue())
        key = next(iter(self.state()['active']))
        self.assertTrue(key.endswith('/backup.zip'))
        self.assertEqual((self.destination / key).read_bytes(), original_bytes)
        restored = self.root / 'restored'
        self.assertEqual(recover(validate(self.config), 'restore', output=restored), 0)
        self.assertEqual(contents(restored), contents(self.source))

    def test_zip_compression_roundtrip(self):
        self.fixture()
        self.use_batch(archive_format='zip')
        self.assertEqual(self.execute(), 0, self.output.getvalue())
        self.assert_complete()
        self.assertTrue(all(k.endswith('.zip') for k in self.state()['active']))

    def test_dry_run_simple_and_batch_no_mutations_or_notifications(self):
        self.fixture()
        for logic in ('simple', 'batch'):
            with self.subTest(logic=logic):
                config = replace(self.config, dry_run=True, jobs=(replace(self.config.jobs[0], logic=logic),))
                self.assertEqual(self.execute(config), 0)
                self.assertFalse(self.destination.exists())
                self.assertFalse(self.config.state_dir.exists())
                self.assertEqual(self.notifications, [])
                if logic == 'simple':
                    self.assertTrue(any(e['event'] == 'sync_start' for e in self.events))
                else:
                    self.assertTrue(any(e['event'] == 'would_batch' for e in self.events))

    def test_existing_backup_dry_run_preserves_all_bytes(self):
        for logic in ('simple', 'batch'):
            with self.subTest(logic=logic):
                self.config = replace(self.config, jobs=(replace(self.config.jobs[0], logic=logic),),
                                      state_dir=self.root / ('state-' + logic))
                self.destination = self.root / ('dest-' + logic)
                self.config = replace(self.config, jobs=(replace(self.config.jobs[0], destination=str(self.destination)),))
                self.file('file', b'old')
                self.assertEqual(self.execute(), 0)
                before = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob('*')
                          if p.is_file() and 'logs' not in p.parts and 'source' not in p.parts}
                (self.source / 'file').write_bytes(b'new')
                self.assertEqual(self.execute(replace(self.config, dry_run=True), now=1000000), 0)
                after = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob('*')
                         if p.is_file() and 'logs' not in p.parts and 'source' not in p.parts}
                self.assertEqual(before, after)

    def test_preflight_missing_dependency_never_creates_destination(self):
        self.file('a')
        config = replace(self.config, rclone='definitely-not-installed')
        self.assertEqual(self.execute(config), 1)
        self.assertFalse(self.destination.exists())
        self.assertEqual([n[1] for n in self.notifications], ['run', 'fail'])

    def test_later_missing_source_preserves_the_previous_jobs_completed_transfer(self):
        self.file('a')
        job = replace(self.config.jobs[0], name='missing', source=self.root / 'missing',
                      destination=str(self.root / 'other'), continue_on_job_error=False)
        self.assertEqual(self.execute(replace(self.config, jobs=self.config.jobs + (job,))), 1)
        self.assertEqual(contents(self.destination), contents(self.source))
        self.assertFalse(Path(job.destination).exists())

    def test_corrupt_state_recovers_and_destination_symlink_is_ignored(self):
        self.use_batch()
        self.file('a')
        self.assertEqual(self.execute(), 0)
        before = contents(self.destination)
        state = self.config.state_dir / 'test/state.json'
        state.write_text('corrupt')
        self.assertEqual(self.execute(), 0, self.output.getvalue())
        self.assertEqual(contents(self.destination), before)
        state.unlink()
        outside = self.root / 'outside'
        outside.write_bytes(b'untouched')
        (self.destination / 'link').symlink_to(outside)
        self.assertEqual(self.execute(), 0)
        self.assertEqual(outside.read_bytes(), b'untouched')

    def test_batch_transfer_failure_keeps_previous_committed_state(self):
        self.file('a', b'old')
        self.file('b', b'old')
        self.use_batch()
        self.assertEqual(self.execute(), 0)
        old = self.state()
        (self.source / 'a').write_bytes(b'changed')
        original = Storage.put
        def fail_control(storage, source, key, **kwargs):
            if key.endswith('.json'):
                raise BackupError('network failure')
            return original(storage, source, key, **kwargs)
        with patch.object(Storage, 'put', fail_control):
            self.assertEqual(self.execute(), 1)
        self.assertEqual(self.state(), old)
        self.assertTrue(all((self.destination / key).exists() for key in old['active']))
        self.assertEqual(self.execute(), 0)
        self.assert_complete()

    def test_failed_control_index_upload_never_commits_new_state(self):
        self.file('a')
        self.file('b')
        self.use_batch()
        original = Storage.put
        def fail_index(storage, source, key, **kwargs):
            if key == '.sambinha/control.tar.gz':
                raise BackupError('index failure')
            return original(storage, source, key, **kwargs)
        with patch.object(Storage, 'put', fail_index):
            self.assertEqual(self.execute(), 1)
        self.assertFalse((self.config.state_dir / 'test/state.json').exists())
        self.assertEqual(self.execute(), 0)
        self.assert_complete()

    def test_source_changed_during_direct_compression_aborts_before_upload(self):
        self.file('a', b'old')
        self.file('b', b'old')
        self.use_batch()
        original = Runner.run
        def changed(runner, arguments, **kwargs):
            result = original(runner, arguments, **kwargs)
            if len(arguments) > 1 and arguments[1] == 'a':
                (self.source / 'a').write_bytes(b'changed')
            return result
        with patch.object(Runner, 'run', changed):
            self.assertEqual(self.execute(), 1)
        self.assertFalse(self.destination.exists())

    def test_independent_jobs_continue_after_one_failure(self):
        self.file('a')
        first = replace(self.config.jobs[0], logic='batch', compressor='false')
        second = replace(self.config.jobs[0], name='second', destination=str(self.root / 'second'))
        self.assertEqual(self.execute(replace(self.config, jobs=(first, second))), 1)
        self.assertEqual(contents(self.root / 'second'), contents(self.source))

    def test_zero_day_retention(self):
        self.use_batch()
        self.file('a')
        self.assertEqual(self.execute(), 0)
        (self.source / 'a').unlink()
        config = replace(self.config, deleted_days=0)
        self.assertEqual(self.execute(config), 0)
        self.assertEqual(self.state()['retired'], [])

    def test_retired_batch_reactivation_never_expires_active_data(self):
        self.file('a', b'original')
        self.file('b', b'original')
        self.use_batch()
        self.assertEqual(self.execute(), 0)
        old = self.state()
        timestamps = {p: (self.source / p).stat().st_mtime_ns for p in ('a', 'b')}
        (self.source / 'a').write_bytes(b'changed')
        os.utime(self.source / 'a', ns=(timestamps['a'], timestamps['a']))
        self.assertEqual(self.execute(now=2000), 0)
        self.assertTrue(self.state()['retired'])
        (self.source / 'a').write_bytes(b'original')
        os.utime(self.source / 'a', ns=(timestamps['a'], timestamps['a']))
        self.assertEqual(self.execute(now=2000 + 10 * DAY), 0, self.output.getvalue())
        self.assertEqual(self.state()['active'], old['active'])
        self.assertFalse(any(r['key'] in self.state()['active'] for r in self.state()['retired']))
        self.assert_complete()

    def test_cleanup_index_failure_is_republished_on_next_run(self):
        self.file('a', b'old')
        self.file('b', b'old')
        self.use_batch()
        self.assertEqual(self.execute(), 0)
        (self.source / 'a').write_bytes(b'new')
        self.assertEqual(self.execute(now=2000), 0)
        original = Storage.put
        uploads = 0
        def fail_second_index(storage, source, key, **kwargs):
            nonlocal uploads
            if key == '.sambinha/control.tar.gz':
                uploads += 1
                if uploads == 2:
                    raise BackupError('cleanup index failure')
            return original(storage, source, key, **kwargs)
        with patch.object(Storage, 'put', fail_second_index):
            self.assertEqual(self.execute(now=2000 + 2 * DAY), 1)
        self.assertTrue(self.state()['index_pending'])
        self.assertEqual(self.execute(now=2000 + 2 * DAY), 0, self.output.getvalue())
        self.assertNotIn('index_pending', self.state())
        with tarfile.open(self.destination / '.sambinha/control.tar.gz') as tar:
            self.assertEqual(json.load(tar.extractfile('state.json')), self.state())
        self.assert_complete()

    def test_expiration_is_idempotent_after_partial_cleanup_failure(self):
        self.file('a')
        self.file('b')
        self.use_batch()
        self.assertEqual(self.execute(), 0)
        (self.source / 'a').write_bytes(b'new')
        self.assertEqual(self.execute(now=2000), 0)
        original = Storage.delete
        def fail_control(storage, key):
            if key.endswith('.json'):
                raise BackupError('failed to delete control')
            return original(storage, key)
        with patch.object(Storage, 'delete', fail_control):
            self.assertEqual(self.execute(now=2000 + 2 * DAY), 1)
        self.assertEqual(self.execute(now=2000 + 2 * DAY), 0, self.output.getvalue())
        self.assertEqual(self.state()['retired'], [])
        self.assert_complete()

    def test_notifier_failure_returns_failure_but_backup_stays_complete(self):
        from sambinha.engine import run
        self.file('a')
        with redirect_stdout(io.StringIO()):
            result = run(self.config, notifier=lambda *a: (_ for _ in ()).throw(BackupError('notification failed')))
        self.assertEqual(result, 1)
        self.assert_complete()

    def test_previous_metadata_namespace_cannot_modify_existing_backup(self):
        # An index from a different namespace must be refused before any remote
        # file can be reinterpreted as a document or removed as obsolete data.
        self.use_batch()
        self.file('a', b'original')
        self.assertEqual(self.execute(), 0)
        state = self.state()
        state['identity'].pop('namespace')
        path = self.config.state_dir / 'test/state.json'
        path.write_text(json.dumps(state))
        before = {p.relative_to(self.destination): p.read_bytes()
                  for p in self.destination.rglob('*') if p.is_file()}
        self.file('a', b'changed')
        self.assertEqual(self.execute(), 1)
        after = {p.relative_to(self.destination): p.read_bytes()
                 for p in self.destination.rglob('*') if p.is_file()}
        self.assertEqual(before, after)

    def test_secrets_file_backup_roundtrip_keeps_values_out_of_logs(self):
        self.file('file', b'original')
        (self.root / 'secrets.env').write_text(
            'AWS_SECRET_ACCESS_KEY=PRIVATE-TEST-SECRET\n'
            'CRONITOR_URL=https://cronitor.link/p/PRIVATE-TEST-TOKEN/monitor\n')
        path = self.root / 'config.toml'
        path.write_text('[backup]\nsecrets_file="secrets.env"\n'
            '[[jobs]]\nname="test"\nsource="source"\ndestination="destination"\n')
        self.assertEqual(self.execute(load(path)), 0, self.output.getvalue())
        self.assert_complete()
        self.assertEqual(self.notifications[0][0],
                         'https://cronitor.link/p/PRIVATE-TEST-TOKEN/monitor')
        for log in self.config.log_dir.glob('*.jsonl'):
            self.assertNotIn('PRIVATE-TEST-SECRET', log.read_text())
            self.assertNotIn('PRIVATE-TEST-TOKEN', log.read_text())
