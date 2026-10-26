from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from tests.helpers import REAL_TOOLS, Sandbox, Usage


class DiskTests(Sandbox):
    def test_disabled_checks_allow_backup_when_capacity_is_unavailable(self):
        if not REAL_TOOLS:
            self.skipTest('rclone and 7z required')
        self.file('file')
        for logic in ('simple', 'batch'):
            for role in ('source', 'destination'):
                with self.subTest(logic=logic, role=role):
                    destination = self.root / f'{logic}-{role}'
                    job = replace(self.config.jobs[0], logic=logic, destination=str(destination),
                                  **{f'check_{role}_disk': False})
                    config = replace(self.config, jobs=(job,), state_dir=self.root / f'state-{logic}-{role}')
                    measured = []
                    def usage(path):
                        measured.append(Path(path))
                        unavailable = (Path(path) == self.source if role == 'source' else
                                       Path(path) in (destination, self.root))
                        return Usage(0, 0, 0) if unavailable else Usage(100 * 1024**2, 10 * 1024**2, 90 * 1024**2)
                    # The nearest existing destination parent must also remain
                    # unmeasured; temporary storage always has a real capacity.
                    with patch('shutil.disk_usage', side_effect=usage):
                        self.assertEqual(self.execute(config), 0)
                    self.assertTrue(destination.exists())
                    if role == 'source':
                        self.assertNotIn(self.source, measured)
                    else:
                        self.assertNotIn(destination, measured)

    def test_full_source_stops_before_destination_write_and_notifies(self):
        if not REAL_TOOLS:
            self.skipTest('rclone required')
        self.file('file')
        with patch('shutil.disk_usage', return_value=Usage(100, 96, 4)):
            self.assertEqual(self.execute(), 1)
        self.assertFalse(self.destination.exists())
        self.assertEqual(self.notifications[-1][1], 'fail')
        self.assertIn('95%', self.notifications[-1][3])

    def test_disabling_source_check_does_not_disable_destination_protection(self):
        if not REAL_TOOLS:
            self.skipTest('rclone required')
        self.file('file')
        job = replace(self.config.jobs[0], check_source_disk=False)
        with patch('shutil.disk_usage', return_value=Usage(100, 96, 4)):
            self.assertEqual(self.execute(replace(self.config, jobs=(job,))), 1)
        self.assertFalse(self.destination.exists())
        self.assertIn('destino', self.notifications[-1][3])

    def test_disabling_both_checks_keeps_temporary_and_mount_protection(self):
        if not REAL_TOOLS:
            self.skipTest('rclone and 7z required')
        self.file('file')
        job = replace(self.config.jobs[0], logic='batch', check_source_disk=False,
                      check_destination_disk=False)
        with patch('shutil.disk_usage', return_value=Usage(100, 96, 4)):
            self.assertEqual(self.execute(replace(self.config, jobs=(job,))), 1)
        self.assertFalse(self.destination.exists())
        self.assertIn('temporários', self.notifications[-1][3])
        job = replace(job, source_mount=self.root / 'missing-mount')
        self.assertEqual(self.execute(replace(self.config, jobs=(job,))), 1)
        self.assertFalse(self.destination.exists())
        self.assertIn('Montagem de origem', self.notifications[-1][3])

    def test_destination_crossing_limit_on_final_transfer_is_reported_as_error(self):
        if not REAL_TOOLS:
            self.skipTest('rclone required')
        from sambinha.storage import Storage
        self.file('file')
        self.config = replace(self.config, jobs=(replace(self.config.jobs[0], logic='batch'),))
        original = Storage.put
        filled = False
        def put(storage, *args, **kwargs):
            nonlocal filled
            result = original(storage, *args, **kwargs)
            filled = True
            return result
        def usage(path):
            return Usage(100, 96, 4) if filled and Path(path) == self.destination else Usage(100 * 1024**2, 10 * 1024**2, 90 * 1024**2)
        with patch.object(Storage, 'put', put), patch('shutil.disk_usage', side_effect=usage):
            self.assertEqual(self.execute(), 1)
        self.assertIn('destino', self.notifications[-1][3])
        self.assertIn('95%', self.notifications[-1][3])
