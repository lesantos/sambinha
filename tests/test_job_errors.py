from contextlib import redirect_stdout
from dataclasses import replace
import io
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from sambinha.errors import BackupError, JobError, job_errors
from sambinha.inventory import scan
from sambinha.recovery import recover, verify_job
from sambinha.storage import Storage
from tests.helpers import REAL_TOOLS, Sandbox, contents
from tests.test_disks import Usage


class JobErrorTests(unittest.TestCase):
    def test_error_preserves_job_and_cause_without_duplicate_prefix(self):
        cause = PermissionError('sem acesso')
        with self.assertRaises(JobError) as raised:
            with job_errors('first'):
                with job_errors('first'):
                    raise cause
        self.assertEqual(raised.exception.job, 'first')
        self.assertEqual(str(raised.exception), 'Job first: sem acesso')
        self.assertIs(raised.exception.__cause__, cause)

    def test_error_callback_can_continue_but_does_not_swallow_interrupt(self):
        callback = Mock()
        with job_errors('first', callback):
            raise ValueError('inválido')
        self.assertEqual(str(callback.call_args.args[0]), 'Job first: inválido')
        with self.assertRaises(KeyboardInterrupt):
            with job_errors('first', callback):
                raise KeyboardInterrupt
        callback.assert_called_once()


@unittest.skipUnless(REAL_TOOLS, 'rclone and 7z required')
class JobFailurePolicyTests(Sandbox):
    def two_jobs(self, policy, suffix):
        self.file('file', b'first')
        second_source = self.root / 'second-source'
        second_source.mkdir(exist_ok=True)
        (second_source / 'file').write_bytes(b'second')
        first = replace(self.config.jobs[0], destination=str(self.root / ('first-' + suffix)),
                        continue_on_job_error=policy)
        second = replace(first, name='second', source=second_source, continue_on_job_error=not policy,
                         destination=str(self.root / ('second-' + suffix)))
        return replace(self.config, jobs=(first, second),
                       state_dir=self.root / ('state-' + suffix))

    def assert_failure_policy(self, config):
        self.assertEqual(self.execute(config), 1)
        errors = [e for e in self.events if e['event'] == 'error']
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]['job'], 'test')
        self.assertTrue(errors[0]['error'].startswith('Job test: '))
        self.assertIn('Job test: ', self.notifications[-1][3])
        self.assertFalse(self.events[-1]['success'])
        second = config.jobs[1]
        if config.jobs[0].continue_on_job_error:
            self.assertEqual(contents(Path(second.destination)), contents(second.source))
        else:
            self.assertFalse(Path(second.destination).exists())
            self.assertFalse((config.state_dir / 'second' / 'state.json').exists())

    def test_capacity_failure_reports_path_and_obeys_policy(self):
        for policy in (True, False):
            with self.subTest(policy=policy):
                config = self.two_jobs(policy, f'disk-{policy}')
                def usage(path):
                    return Usage(0, 0, 0) if Path(path) == self.source else Usage(100, 10, 90)
                with patch('shutil.disk_usage', side_effect=usage):
                    self.assert_failure_policy(config)
                error = next(e for e in self.events if e['event'] == 'error')
                self.assertIn(str(self.source), error['error'])
                self.assertIn('0 bytes', error['error'])
                self.assertFalse(Path(config.jobs[0].destination).exists())

    def test_inventory_failure_obeys_policy(self):
        for policy in (True, False):
            with self.subTest(policy=policy):
                config = self.two_jobs(policy, f'inventory-{policy}')
                def inventory(root, *args, **kwargs):
                    if root == self.source:
                        raise PermissionError('inventário ilegível')
                    return scan(root, *args, **kwargs)
                with patch('sambinha.engine.scan', side_effect=inventory):
                    self.assert_failure_policy(config)

    def test_missing_exclusion_file_obeys_policy(self):
        for policy in (True, False):
            with self.subTest(policy=policy):
                config = self.two_jobs(policy, f'rules-{policy}')
                first = replace(config.jobs[0], exclude_files=self.root / 'missing.txt')
                self.assert_failure_policy(replace(config, jobs=(first, config.jobs[1])))

    def test_invalid_state_obeys_policy(self):
        for policy in (True, False):
            with self.subTest(policy=policy):
                config = self.two_jobs(policy, f'state-{policy}')
                directory = config.state_dir / 'test'
                directory.mkdir(parents=True)
                (directory / 'state.json').write_text('corrupt')
                self.assert_failure_policy(config)

    def test_transfer_failure_obeys_policy(self):
        original = Storage.put
        for policy in (True, False):
            with self.subTest(policy=policy):
                config = self.two_jobs(policy, f'transfer-{policy}')
                def put(storage, *args, **kwargs):
                    if storage.job.name == 'test':
                        raise BackupError('transferência falhou')
                    return original(storage, *args, **kwargs)
                with patch.object(Storage, 'put', put):
                    self.assert_failure_policy(config)

    def test_scheduled_verification_failure_obeys_policy(self):
        for policy in (True, False):
            with self.subTest(policy=policy):
                config = replace(self.two_jobs(policy, f'scheduled-{policy}'), verify_interval_days=7)
                def verify(config, job, *args, **kwargs):
                    if job.name == 'test':
                        raise BackupError('verificação falhou')
                    return verify_job(config, job, *args, **kwargs)
                with patch('sambinha.recovery.verify_job', side_effect=verify):
                    self.assert_failure_policy(config)

    def test_manual_verification_failure_obeys_policy(self):
        for policy in (True, False):
            with self.subTest(policy=policy):
                config = self.two_jobs(policy, f'verify-{policy}')
                self.assertEqual(self.execute(config), 0)
                (config.state_dir / 'test' / 'state.json').unlink()
                output, notifications = io.StringIO(), []
                with redirect_stdout(output):
                    result = recover(config, 'verify', notifier=lambda *args: notifications.append(args))
                self.assertEqual(result, 1)
                events = [json.loads(line) for line in output.getvalue().splitlines()]
                error = next(e for e in events if e['event'] == 'error')
                self.assertEqual(error['job'], 'test')
                self.assertTrue(error['error'].startswith('Job test: '))
                self.assertIn('Job test: ', notifications[-1][3])
                self.assertEqual(any(e['event'] == 'verification_complete' and e['job'] == 'second'
                                     for e in events), policy)

    def test_all_failed_preflight_jobs_return_failure_without_creating_destinations(self):
        config = self.two_jobs(True, 'all-failed')
        with patch('shutil.disk_usage', return_value=Usage(0, 0, 0)):
            self.assertEqual(self.execute(config), 1)
        self.assertEqual([e['job'] for e in self.events if e['event'] == 'error'], ['test', 'second'])
        self.assertIn('Job test: ', self.notifications[-1][3])
        self.assertIn('Job second: ', self.notifications[-1][3])
        for job in config.jobs:
            self.assertFalse(Path(job.destination).exists())
