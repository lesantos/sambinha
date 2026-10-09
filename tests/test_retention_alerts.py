from dataclasses import replace
import json
from pathlib import Path
import unittest
from unittest.mock import Mock

from sambinha.retention import DAY, DeletionForecast, retirement, alert_time
from tests.helpers import REAL_TOOLS, Sandbox


class ForecastUnitTests(unittest.TestCase):
    def test_strict_threshold_global_count_and_original_names(self):
        log, notify = Mock(), Mock()
        forecast = DeletionForecast(2, log, notify)
        forecast.add('one', [(retirement('h/a', ['a', 'b'], 'deleted', 0, 10), ['a', 'b'])])
        notify.assert_not_called()
        forecast.add('two', [(retirement('h/c', ['c'], 'deleted', 0, 10), ['c'])])
        notify.assert_called_once()
        self.assertTrue(forecast.sent)
        self.assertIn('3 arquivos/versões', forecast.message)
        names = {call.kwargs['path'] for call in log.event.call_args_list if call.args[0] == 'deletion_candidate'}
        self.assertEqual(names, {'a', 'b', 'c'})
        forecast.add('two', [(retirement('h/c', ['c'], 'deleted', 0, 10), ['c'])])
        self.assertEqual(notify.call_count, 1)

    def test_dates_two_days_one_day_and_zero_day_retention(self):
        for days, lead in ((10, 2), (2, 2), (1, 1), (0, 0)):
            with self.subTest(days=days):
                entry = retirement('h/file', ['file'], 'deleted', 1000, days)
                self.assertEqual(entry['alert_at'], 1000 + (days - lead) * DAY)
                self.assertEqual(entry['expires'], 1000 + days * DAY)
        self.assertEqual(alert_time({'expires': 10 * DAY, 'reason': 'deleted'}, 10, 2), 8 * DAY)

    def test_thousands_are_metadata_only_and_count_files_inside_lot(self):
        forecast = DeletionForecast(1000, Mock(), Mock())
        files = [f'dir/file-{i}' for i in range(1001)]
        forecast.add('batch', [(retirement('batch.7z', files, 'deleted', 0, 10), files)])
        self.assertTrue(forecast.sent)
        self.assertEqual(forecast.count_reported, 1001)


@unittest.skipUnless(REAL_TOOLS, 'rclone and 7z required')
class RetentionAlertTests(Sandbox):
    def prepare(self, days=10, logic='simple'):
        for name in ('a', 'b', 'c'):
            self.file(name, name.encode())
        self.config = replace(self.config, deleted_days=days, deletion_alert_threshold=2,
                              jobs=(replace(self.config.jobs[0], logic=logic),))
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
        self.config = replace(self.config, deleted_days=0, deletion_alert_threshold=2)
        self.assertEqual(self.execute(), 0)
        for name in ('a', 'b', 'c'):
            (self.source / name).unlink()
        from unittest.mock import patch
        from sambinha.storage import Storage
        original = Storage.delete
        seen = []
        def capture_delete(storage, key):
            if key.startswith('.sambinha/history'):
                seen.append(self.notifications[-1][1])
            return original(storage, key)
        with patch.object(Storage, 'delete', capture_delete):
            self.assertEqual(self.execute(), 0)
        self.assertEqual(seen, ['fail'] * 3)
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
        self.assertEqual(self.execute(replace(self.config, deletion_alert_threshold=3), now=2000 + 8 * DAY), 0)
        self.assertEqual(self.notifications[-1][1], 'complete')
        self.notifications.clear()
        self.assertEqual(self.execute(replace(self.config, dry_run=True), now=2000 + 8 * DAY), 0)
        self.assertEqual(self.notifications, [])
        self.assertEqual(len(self.state()['retired']), 3)

    def test_global_warning_counts_multiple_jobs_before_cleanup(self):
        for name in ('a', 'b', 'c'):
            self.file(name)
        second = replace(self.config.jobs[0], name='second', destination=str(self.root / 'second'))
        self.config = replace(self.config, deletion_alert_threshold=5, jobs=self.config.jobs + (second,))
        self.assertEqual(self.execute(now=1000), 0)
        for name in ('a', 'b', 'c'):
            (self.source / name).unlink()
        self.assertEqual(self.execute(now=2000), 0)
        self.assertEqual(self.execute(now=2000 + 8 * DAY), 0)
        self.assertIn('6 arquivos/versões', self.notifications[-1][3])
        self.assertEqual({e['job'] for e in self.events if e['event'] == 'deletion_candidate'}, {'test', 'second'})
        self.assertEqual(self.execute(now=2000 + 10 * DAY), 0)
        self.assertEqual(self.state()['retired'], [])
        state = json.loads((self.config.state_dir / 'second/state.json').read_text())
        self.assertEqual(state['retired'], [])
