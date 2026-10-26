import unittest
from unittest.mock import Mock

from sambinha.retention import DAY, DeletionForecast, alert_time, retirement


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
