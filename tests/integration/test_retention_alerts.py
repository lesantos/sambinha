import json
import unittest
from dataclasses import replace

from sambinha.config import with_job_overrides
from sambinha.retention import DAY
from tests.helpers import REAL_TOOLS, Sandbox


@unittest.skipUnless(REAL_TOOLS, 'rclone and 7z required')
class RetentionAlertTests(Sandbox):
    def prepare(self, days=10, logic='batch'):
        for name in ('a', 'b', 'c'):
            self.file(name, name.encode())
        self.config = with_job_overrides(replace(self.config, jobs=(replace(self.config.jobs[0], logic=logic, max_megabytes=1 / 1024 ** 2),)), deleted_days=days, deletion_alert_threshold=2)
        self.assertEqual(self.execute(now=1000), 0)
        for name in ('a', 'b', 'c'):
            (self.source / name).unlink()
        self.assertEqual(self.execute(now=2000), 0)

    def test_warning_two_days_before_does_not_fail_or_stop_expiration(self):
        self.prepare()
        self.assertEqual(self.execute(now=2000 + 8 * DAY - 1), 0)
        self.assertEqual(self.notifications[-1][1], 'complete')
        self.assertEqual(self.execute(now=2000 + 8 * DAY), 0)
        self.assertEqual(self.notifications[-1][1], 'fail')
        self.assertIn('3 arquivos/versões', self.notifications[-1][3])
        self.assertEqual(len(self.state()['retired']), 3)
        names = {e['path'] for e in self.events if e['event'] == 'deletion_candidate'}
        self.assertEqual(names, {'a', 'b', 'c'})
        self.assertTrue(self.events[-1]['success'])
        self.assertTrue(self.events[-1]['deletion_alert'])
        self.assertEqual(self.execute(now=2000 + 10 * DAY), 0)
        self.assertEqual(self.state()['retired'], [])
        self.assertEqual(self.notifications[-1][1], 'fail')
        self.assertEqual(self.execute(now=2000 + 10 * DAY + 1), 0)
        self.assertEqual(self.notifications[-1][1], 'complete')

    def test_one_day_retention_warns_at_detection_and_keeps_one_day(self):
        self.prepare(days=1)
        self.assertEqual(self.notifications[-1][1], 'fail')
        self.assertEqual(len(self.state()['retired']), 3)
        self.assertEqual(self.execute(now=2000 + DAY - 1), 0)
        self.assertEqual(len(self.state()['retired']), 3)
        self.assertEqual(self.execute(now=2000 + DAY), 0)
        self.assertEqual(self.state()['retired'], [])

    def test_zero_day_warns_before_actual_purge_without_delaying_it(self):
        for name in ('a', 'b', 'c'):
            self.file(name)
        self.config = with_job_overrides(self.config, deleted_days=0, deletion_alert_threshold=2)
        self.config = replace(self.config, jobs=(replace(self.config.jobs[0], logic='batch', max_megabytes=1 / 1024 ** 2),))
        self.assertEqual(self.execute(), 0)
        for name in ('a', 'b', 'c'):
            (self.source / name).unlink()
        from unittest.mock import patch

        from sambinha.storage import Storage
        original = Storage.delete
        seen = []
        def capture_delete(storage, key):
            seen.append(self.notifications[-1][1] if self.notifications else 'none')
            return original(storage, key)
        with patch.object(Storage, 'delete', capture_delete):
            self.assertEqual(self.execute(), 0)
        self.assertEqual(seen, ['fail'] * 6)  # Three archives and their control records.
        self.assertEqual(self.state()['retired'], [])

    def test_batch_counts_members_and_records_their_original_paths(self):
        self.prepare(logic='batch')
        self.assertEqual(self.execute(now=2000 + 8 * DAY), 0)
        self.assertEqual(self.notifications[-1][1], 'fail')
        self.assertEqual({e['path'] for e in self.events if e['event'] == 'deletion_candidate'}, {'a', 'b', 'c'})
        self.assertEqual(self.execute(now=2000 + 10 * DAY), 0)
        self.assertEqual(self.state()['retired'], [])

    def test_excluded_history_does_not_trigger_warning_or_get_deleted(self):
        self.prepare()
        rules = self.root / 'exclude.txt'
        rules.write_text('a\nb\nc\n')
        self.config = replace(self.config, jobs=(replace(self.config.jobs[0], exclude_files=rules),))
        self.assertEqual(self.execute(now=2000 + 10 * DAY), 0)
        self.assertEqual(self.notifications[-1][1], 'complete')
        self.assertEqual(len(self.state()['retired']), 3)

    def test_threshold_is_configurable_and_simulation_never_notifies(self):
        self.prepare()
        self.assertEqual(self.execute(with_job_overrides(self.config, deletion_alert_threshold=3), now=2000 + 8 * DAY), 0)
        self.assertEqual(self.notifications[-1][1], 'complete')
        self.notifications.clear()
        self.assertEqual(self.execute(with_job_overrides(self.config, dry_run=True), now=2000 + 8 * DAY), 0)
        self.assertEqual(self.notifications, [])
        self.assertEqual(len(self.state()['retired']), 3)

    def test_warning_threshold_is_per_job_like_separate_invocations(self):
        for name in ('a', 'b', 'c'):
            self.file(name)
        first = replace(self.config.jobs[0], logic='batch', max_megabytes=1 / 1024 ** 2)
        second = replace(first, name='second', destination=str(self.root / 'second'))
        self.config = with_job_overrides(replace(self.config, jobs=(first, second)), deletion_alert_threshold=5)
        self.assertEqual(self.execute(now=1000), 0)
        for name in ('a', 'b', 'c'):
            (self.source / name).unlink()
        self.assertEqual(self.execute(now=2000), 0)
        self.assertEqual(self.execute(now=2000 + 8 * DAY), 0)
        self.assertEqual(self.notifications[-1][1], 'complete')
        self.assertFalse(any(e['event'] == 'deletion_candidate' for e in self.events))
        self.config = with_job_overrides(self.config, deletion_alert_threshold=2)
        self.assertEqual(self.execute(now=2000 + 8 * DAY), 0)
        self.assertIn('3 arquivos/versões', self.notifications[-1][3])
        self.assertEqual({e['job'] for e in self.events if e['event'] == 'deletion_candidate'}, {'test', 'second'})
        self.assertEqual(self.execute(now=2000 + 10 * DAY), 0)
        self.assertEqual(self.state()['retired'], [])
        state = json.loads((self.config.state_dir / 'second/state.json').read_text())
        self.assertEqual(state['retired'], [])
