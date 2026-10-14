from contextlib import redirect_stdout
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch

from sambinha.config import validate
from sambinha.engine import run
from sambinha.errors import BackupError, ConfigError
from sambinha.inventory import scan
from sambinha.recovery import recover
from sambinha.state import indexed_files
from sambinha.storage import Storage
from tests.helpers import REAL_TOOLS, Sandbox, contents, restore_batches


class BatchOptionsAndTraversalTests(Sandbox):
    def test_threshold_validation_and_uniform_defaults(self):
        job = self.config.jobs[0]
        self.assertEqual((job.compression_level, job.compression_threads), (3, 2))
        self.assertEqual((job.individual_batch_percent, job.batch_size_alert_percent), (100, 200))
        for name, values in {'individual_batch_percent': (0, -1, 101, True, float('nan'), float('inf')),
                             'batch_size_alert_percent': (-1, True, float('nan'), float('inf'))}.items():
            for value in values:
                with self.subTest(name=name, value=value), self.assertRaises(ConfigError):
                    validate(replace(self.config, jobs=(replace(job, **{name: value}),)))
        validate(replace(self.config, jobs=(replace(job, individual_batch_percent=0.5, batch_size_alert_percent=0),)))

    def test_mixed_sibling_mtimes_drive_depth_first_order(self):
        paths = [self.file('older-directory/recent-file'), self.file('root-old'),
                 self.file('newer-directory/ancient-file'), self.file('root-new')]
        for path, stamp in zip(paths, (900, 20, 1, 40)):
            os.utime(path, ns=(stamp, stamp))
        for directory, stamp in (('older-directory', 10), ('newer-directory', 30)):
            os.utime(self.source / directory, ns=(stamp, stamp))
        self.assertEqual([f.path for f in scan(self.source, mode='simple')],
                         ['older-directory/recent-file', 'root-old', 'newer-directory/ancient-file', 'root-new'])

    def test_mtime_ties_use_names_and_do_not_follow_links(self):
        for name in ('z/a', 'b', 'a'):
            path = self.file(name)
            os.utime(path, ns=(100, 100))
        os.utime(self.source / 'z', ns=(100, 100))
        (self.source / 'link').symlink_to(self.source / 'z', target_is_directory=True)
        self.assertEqual([f.path for f in scan(self.source)], ['a', 'b', 'z/a'])


@unittest.skipUnless(REAL_TOOLS, 'rclone and 7z required')
class StableBatchLifecycleTests(Sandbox):
    def setUp(self):
        super().setUp()
        self.config = replace(self.config, jobs=(replace(self.config.jobs[0], logic='batch', max_bytes=10),))

    def seed(self):
        for name in ('a', 'b', 'c', 'd'):
            self.file(name, name.encode() * 4)
        self.file('individual', b'i' * 30)
        self.assertEqual(self.execute(), 0, self.output.getvalue())
        self.assertEqual(self.state()['schema'], 2)
        return self.state()

    def restore(self, output):
        with redirect_stdout(io.StringIO()):
            return recover(self.config, 'restore', output=output)

    def test_growth_keeps_members_and_no_change_does_not_repeat_rebuild(self):
        before = self.seed()
        original = next(c for c in before['active'].values() if any(f['path'] == 'a' for f in c['files']))
        (self.source / 'a').write_bytes(b'a' * 25)
        self.assertEqual(self.execute(now=2000), 0, self.output.getvalue())
        after = self.state()
        current = next(c for c in after['active'].values() if c.get('batch_id') == original['batch_id'])
        self.assertEqual([f['path'] for f in current['files']], [f['path'] for f in original['files']])
        self.assertGreater(sum(f['size'] for f in current['files']), 10)
        self.assertEqual(len(set(before['active']) - set(after['active'])), 1)
        self.assertEqual(self.execute(now=2001), 0)
        summary = next(e for e in self.events if e['event'] == 'summary')
        self.assertEqual(summary['rebuilt_batches'], 0)

    def test_batch_alert_only_at_end_all_jobs_finish_and_exit_is_success(self):
        self.seed()
        (self.source / 'a').write_bytes(b'a' * 25)
        second = replace(self.config.jobs[0], name='second', destination=str(self.root / 'second'), logic='simple')
        config = replace(self.config, jobs=(self.config.jobs[0], second))
        states = []
        def notified(url, state, series, message=''):
            states.append(state)
            if state == 'fail':
                self.assertTrue((self.root / 'second' / 'a').exists())
                self.assertIn('200%', message)
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(run(config, notifier=notified, clock=lambda: 2000), 0)
        self.assertEqual(states, ['run', 'fail'])
        records = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertTrue(records[-1]['success'])
        self.assertTrue(records[-1]['batch_size_alert'])

    def test_individual_growth_never_triggers_alert_and_zero_disables_normal_alert(self):
        self.seed()
        (self.source / 'individual').write_bytes(b'i' * 300)
        self.notifications.clear()
        self.assertEqual(self.execute(now=2000), 0)
        self.assertEqual([n[1] for n in self.notifications], ['run', 'complete'])
        self.assertFalse(any(e['event'] == 'batch_size_alert' for e in self.events))
        (self.source / 'a').write_bytes(b'a' * 25)
        self.notifications.clear()
        config = replace(self.config, jobs=(replace(self.config.jobs[0], batch_size_alert_percent=0),))
        self.assertEqual(self.execute(config, now=2001), 0)
        self.assertEqual([n[1] for n in self.notifications], ['run', 'complete'])

    def test_alert_at_exact_threshold_and_dry_run_sends_nothing(self):
        self.file('a', b'a' * 4); self.file('b', b'b' * 4)
        self.assertEqual(self.execute(), 0)
        (self.source / 'a').write_bytes(b'a' * 16)
        self.notifications.clear()
        self.assertEqual(self.execute(replace(self.config, dry_run=True), now=2000), 0)
        self.assertEqual(self.notifications, [])
        self.assertEqual(self.execute(now=2001), 0)
        self.assertTrue(any(e['event'] == 'batch_size_alert' and e['bytes'] == 20 for e in self.events))

    def test_empty_groups_leave_active_index_and_expire_with_retention(self):
        before = self.seed()
        key, control = next((k,c) for k,c in before['active'].items() if len(c['files']) > 1)
        for f in control['files']:(self.source / f['path']).unlink()
        config = replace(self.config, deleted_days=0, version_days=0)
        self.assertEqual(self.execute(config, now=2000), 0, self.output.getvalue())
        self.assertNotIn(key, self.state()['active'])
        self.assertFalse((self.destination / key).exists())

    def test_rebalance_without_source_changes_only_oversized_groups(self):
        self.seed()
        (self.source / 'a').write_bytes(b'a' * 7)
        (self.source / 'b').write_bytes(b'b' * 7)
        self.assertEqual(self.execute(now=2000), 0)
        before = self.state()
        expected = contents(self.source)
        oversized = {k for k,c in before['active'].items() if len(c['files']) > 1 and sum(f['size'] for f in c['files']) > 10}
        preserved = {k:c for k,c in before['active'].items() if k not in oversized}
        shutil.rmtree(self.source)
        original = Storage.put
        def check_writes(storage, source, key, **kwargs):
            self.assertNotIn(key.removesuffix('.json'), preserved)
            return original(storage, source, key, **kwargs)
        with patch.object(Storage, 'put', check_writes):
            self.assertEqual(self.execute(now=3000, action='rebalance'), 0, self.output.getvalue())
        after = self.state()
        self.assertEqual({k:after['active'][k] for k in preserved}, preserved)
        self.assertEqual(set(before['active']) - set(after['active']), oversized)
        self.assertTrue(all(len(c['files']) == 1 or sum(f['size'] for f in c['files']) <= 10 for c in after['active'].values()))
        self.assertEqual(restore_batches(after, self.destination, self.root / 'rebalance-restored'), expected)
        self.assertEqual(set(indexed_files(before, 'batch')), set(indexed_files(after, 'batch')))

    def test_rebalance_respects_exclusions_when_expiring_history_without_source(self):
        self.seed()
        old = self.state()
        old_key = next(k for k,c in old['active'].items() if any(f['path']=='a' for f in c['files']))
        (self.source / 'a').write_bytes(b'a' * 7)
        (self.source / 'b').write_bytes(b'b' * 7)
        self.assertEqual(self.execute(now=2000), 0)
        rules = self.root / 'rules.txt'
        rules.write_text('a\n')
        config = replace(self.config, jobs=(replace(self.config.jobs[0], exclude_files=rules),))
        shutil.rmtree(self.source)
        self.assertEqual(self.execute(config, now=2000 + 2 * 86400, action='rebalance'), 0, self.output.getvalue())
        self.assertTrue((self.destination / old_key).exists())
        self.assertIn(old_key, {entry['key'] for entry in self.state()['retired']})

    def test_rebalance_dry_run_preserves_all_bytes_and_no_source_is_needed(self):
        self.seed()
        (self.source / 'a').write_bytes(b'a' * 20)
        self.assertEqual(self.execute(now=2000), 0)
        before = {p.relative_to(self.destination).as_posix():p.read_bytes() for p in self.destination.rglob('*') if p.is_file()}
        state = self.state()
        shutil.rmtree(self.source)
        self.assertEqual(self.execute(replace(self.config, dry_run=True), action='rebalance'), 0)
        self.assertEqual(self.state(), state)
        self.assertEqual({p.relative_to(self.destination).as_posix():p.read_bytes() for p in self.destination.rglob('*') if p.is_file()}, before)

    def test_restore_without_source_and_state_uses_confirmed_destination_index(self):
        self.seed()
        expected = contents(self.source)
        source_mount = self.source
        self.config = replace(self.config, jobs=(replace(self.config.jobs[0], source_mount=source_mount),))
        shutil.rmtree(self.source);shutil.rmtree(self.config.state_dir)
        output = self.root / 'restore-no-source'
        self.assertEqual(self.restore(output), 0)
        self.assertEqual(contents(output), expected)

    def test_missing_or_corrupt_local_state_bootstraps_before_backup_and_preserves_members(self):
        self.seed()
        before = self.state()['active']
        path = self.config.state_dir / 'test' / 'state.json'
        for corrupted in (False, True):
            if corrupted:path.write_text('invalid JSON')
            else:shutil.rmtree(self.config.state_dir / 'test')
            self.assertEqual(self.execute(now=2000), 0, self.output.getvalue())
            self.assertEqual(self.state()['active'], before)

    def test_missing_control_is_repaired_without_recompressing_or_overwriting_archive(self):
        before = self.seed()
        key = next(iter(before['active']))
        archive = self.destination / key
        payload = archive.read_bytes()
        (self.destination / (key + '.json')).unlink()
        config = replace(self.config, jobs=(replace(self.config.jobs[0], compression_level=9),))
        with patch('sambinha.strategies.batch.compress', side_effect=AssertionError('healthy archive rewritten')):
            self.assertEqual(self.execute(config, now=2000), 0, self.output.getvalue())
        self.assertEqual(archive.read_bytes(), payload)
        self.assertEqual(json.loads((self.destination / (key + '.json')).read_text()), before['active'][key])

    def test_missing_destination_is_recreated_from_surviving_source(self):
        self.seed()
        expected = contents(self.source)
        before = self.state()['active']
        shutil.rmtree(self.destination)
        self.assertEqual(self.execute(now=2000), 0, self.output.getvalue())
        self.assertEqual(self.state()['active'], before)
        self.assertEqual(restore_batches(self.state(), self.destination, self.root / 'recreated'), expected)

    def test_failed_first_archive_upload_is_retryable_without_guessing_unowned_data(self):
        self.file('a', b'a' * 4);self.file('b', b'b' * 4)
        original = Storage.put
        calls = 0
        def fail_control(storage, source, key, **kwargs):
            nonlocal calls
            if key.endswith('.json'):
                calls += 1
                if calls == 1:raise BackupError('control failed')
            return original(storage, source, key, **kwargs)
        with patch.object(Storage, 'put', fail_control):self.assertEqual(self.execute(), 1)
        self.assertEqual(self.execute(now=2000), 0, self.output.getvalue())

    def test_unowned_destination_without_recoverable_index_is_never_overwritten(self):
        self.file('a')
        self.destination.mkdir()
        unknown = self.destination / 'unowned.7z';unknown.write_bytes(b'keep')
        self.assertEqual(self.execute(), 1)
        self.assertEqual(unknown.read_bytes(), b'keep')
        self.assertEqual(list(self.destination.iterdir()), [unknown])

    def test_generations_are_bounded_and_corrupt_latest_has_valid_fallback(self):
        self.file('a', b'one');self.file('b', b'keep')
        self.assertEqual(self.execute(), 0)
        (self.source / 'a').write_bytes(b'two')
        self.assertEqual(self.execute(now=2000), 0)
        current = self.state()['generation']
        generation = self.destination / '.sambinha/indexes' / (current + '.tar.gz')
        generation.write_bytes(b'corrupt')
        # Compatibility index is the same confirmed generation and preserves latest data.
        shutil.rmtree(self.config.state_dir)
        output = self.root / 'latest-alias-restored'
        self.assertEqual(self.restore(output), 0)
        self.assertEqual((output / 'a').read_bytes(), b'two')
        (self.destination / '.sambinha/control.tar.gz').write_bytes(b'corrupt too')
        output = self.root / 'older-restored'
        self.assertEqual(self.restore(output), 0)
        self.assertEqual((output / 'a').read_bytes(), b'one')

    def test_power_loss_after_confirmation_before_local_commit_recovers_new_generation(self):
        self.seed()
        (self.source / 'a').write_bytes(b'a' * 7)
        original = Storage.put
        def fail_alias(storage, source, key, **kwargs):
            if key == '.sambinha/control.tar.gz':raise BackupError('power loss before local commit')
            return original(storage, source, key, **kwargs)
        with patch.object(Storage, 'put', fail_alias):self.assertEqual(self.execute(now=2000), 1)
        self.assertTrue((self.config.state_dir / 'test' / 'publication.json').exists())
        self.assertEqual(self.execute(now=2001), 0, self.output.getvalue())
        self.assertEqual(indexed_files(self.state(), 'batch')['a']['size'], 7)
        self.assertFalse((self.config.state_dir / 'test' / 'publication.json').exists())

    def test_generation_order_survives_system_clock_moving_backwards(self):
        self.file('a', b'old')
        self.assertEqual(self.execute(), 0)
        before = self.state()['generation']
        (self.source / 'a').write_bytes(b'new')
        with patch('sambinha.index_generations.time.time_ns', return_value=1):
            self.assertEqual(self.execute(now=2000), 0, self.output.getvalue())
        self.assertGreater(self.state()['generation'], before)

    def test_generation_pruning_and_rebalance_of_simple_job_rejected(self):
        self.file('a', b'one')
        self.config = replace(self.config, recovery_index_versions=2)
        for i in range(4):
            (self.source / 'a').write_bytes(str(i).encode())
            self.assertEqual(self.execute(now=1000+i), 0, self.output.getvalue())
        directory = self.destination / '.sambinha/indexes'
        self.assertEqual(len(list(directory.glob('*.commit.json'))), 2)
        self.assertEqual(len(list(directory.glob('*.tar.gz'))), 2)
        config = replace(self.config, jobs=(replace(self.config.jobs[0], logic='simple'),))
        self.assertEqual(self.execute(config, action='rebalance'), 1)
