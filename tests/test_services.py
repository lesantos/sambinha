import subprocess
import unittest
from dataclasses import replace
from unittest.mock import Mock, patch
from urllib.error import URLError

from sambinha.errors import BackupError
from sambinha.inventory import safe_relative, scan, verify
from sambinha.notify import notify
from sambinha.process import Runner
from sambinha.state import atomic_json, load_state, lock
from sambinha.storage import Storage
from tests.helpers import Sandbox


class ProcessTests(unittest.TestCase):
    def test_success_no_retry(self):
        runner = Runner(sleep=Mock())
        with patch.object(Runner, '_run_captured', return_value=subprocess.CompletedProcess([], 0, 'ok', '')) as mock:
            self.assertEqual(runner.run(['program', 'arg'], retry=True), 'ok')
        self.assertEqual(mock.call_count, 1)
        runner.sleep.assert_not_called()

    def test_three_retries_two_seconds(self):
        sleeper = Mock()
        runner = Runner(sleep=sleeper)
        with patch.object(Runner, '_run_captured', return_value=subprocess.CompletedProcess([], 1, '', 'fail')) as mock:
            with self.assertRaises(BackupError):
                runner.run(['rclone'], retry=True)
        self.assertEqual(mock.call_count, 4)
        self.assertEqual([c.args for c in sleeper.call_args_list], [(2,), (2,), (2,)])

    def test_transient_retry_then_success(self):
        runner = Runner(sleep=Mock())
        with patch.object(Runner, '_run_captured', side_effect=[OSError('network'), subprocess.CompletedProcess([], 0, 'ok', '')]):
            self.assertEqual(runner.run(['rclone'], retry=True), 'ok')
        runner.sleep.assert_called_once_with(2)

    def test_timeout_bounded_and_nontransfer_not_retried(self):
        sleeper = Mock()
        with patch.object(Runner, '_run_captured', side_effect=subprocess.TimeoutExpired('7z', 1)) as mock:
            with self.assertRaises(BackupError):
                Runner(sleep=sleeper).run(['7z'])
        self.assertEqual(mock.call_count, 1)
        sleeper.assert_not_called()

    def test_dependency_check_lists_all_missing(self):
        with patch('shutil.which', return_value=None), self.assertRaisesRegex(BackupError, '7z, rclone'):
            Runner().require(['rclone', '7z', 'rclone'])


class InventoryStateTests(Sandbox):
    def test_fingerprint_and_changed_same_size_same_mtime(self):
        target = self.file('ação/a.txt', b'abc')
        before = scan(self.source)
        target.write_bytes(b'xyz')
        import os
        os.utime(target, ns=(before[0].mtime_ns, before[0].mtime_ns))
        self.assertNotEqual(scan(self.source)[0].sha256, before[0].sha256)
        with self.assertRaises(BackupError):
            verify(self.source, before)

    def test_symlink_file_and_directory_ignored(self):
        target = self.file('a')
        for directory in (False, True):
            with self.subTest(directory=directory):
                link = self.source / 'link'
                link.symlink_to(self.source if directory else target, target_is_directory=directory)
                self.assertEqual([f.path for f in scan(self.source)], ['a'])
                link.unlink()

    def test_reserved_and_invalid_paths(self):
        for value in ('../x', '/x', 'a/../x', 'a\nb', '', 'a//b'):
            with self.subTest(value=value), self.assertRaises(BackupError):
                safe_relative(value)
        self.file('.sambinha/private')
        with self.assertRaises(BackupError):
            scan(self.source)

    def test_atomic_json_and_identity(self):
        from dataclasses import replace

        from sambinha.state import identity
        job = self.config.jobs[0]
        path = self.root / 'state.json'
        state = load_state(path, identity(job))
        atomic_json(path, state)
        self.assertEqual(load_state(path, identity(job)), state)
        self.assertEqual(list(self.root.glob('.state-*')), [])
        with self.assertRaises(BackupError):
            load_state(path, identity(replace(job, name='other')))

    def test_corrupt_state_never_interpreted_as_empty(self):
        path = self.root / 'state.json'
        for text in ('broken', '{}', '{"schema":1,"identity":{},"active":{},"retired":[{"key":"../x","expires":0}]}'):
            with self.subTest(text=text), self.assertRaises(BackupError):
                path.write_text(text)
                load_state(path, {})

    def test_lock_contention_and_release(self):
        with lock(self.root / 'lock'), self.assertRaises(BackupError), lock(self.root / 'lock'):
            pass
        with lock(self.root / 'lock'):
            pass


class NotificationTests(unittest.TestCase):
    def test_cronitor_states_series_and_existing_query(self):
        from urllib.parse import parse_qs, urlsplit
        for state in ('run', 'complete', 'fail'):
            with self.subTest(state=state):
                response = Mock(status=200)
                response.__enter__ = Mock(return_value=response)
                response.__exit__ = Mock(return_value=False)
                opener = Mock(return_value=response)
                notify('https://cronitor.link/p/secret/key?env=test&state=old', state, 'series', opener=opener)
                query = parse_qs(urlsplit(opener.call_args.args[0]).query)
                self.assertEqual(query, {'env': ['test'], 'state': [state], 'series': ['series']})

    def test_cronitor_failure_message_is_encoded_and_bounded(self):
        from urllib.parse import parse_qs, urlsplit
        response = Mock(status=200)
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        opener = Mock(return_value=response)
        message = "arquivo ação & outro: " + "x" * 2100
        notify('https://cronitor.link/p/secret/key?message=old', 'fail', 'series',
               message, opener=opener)
        query = parse_qs(urlsplit(opener.call_args.args[0]).query)
        self.assertEqual(query['message'], [message[:2000]])
        self.assertEqual(query['state'], ['fail'])
        self.assertEqual(query['series'], ['series'])

    def test_notification_retries_and_redacts_secret(self):
        opener, sleeper = Mock(side_effect=URLError('secret-url')), Mock()
        with self.assertRaises(BackupError) as context:
            notify('https://cronitor.link/p/secret/key', 'fail', 'series', opener=opener, sleep=sleeper)
        self.assertEqual(opener.call_count, 4)
        self.assertEqual(sleeper.call_count, 3)
        self.assertNotIn('secret', str(context.exception))

    def test_disabled_notification(self):
        opener = Mock()
        notify('', 'run', 'series', opener=opener)
        opener.assert_not_called()


class StorageTests(Sandbox):
    def test_s3_classes_and_explicit_retry_policy(self):
        job = replace(self.config.jobs[0], type='s3', destination='aws:bucket/prefix', logic='batch', storage_class='DEEP_ARCHIVE')
        runner = Mock()
        storage = Storage(job, self.config, runner)
        storage.put(self.source / 'file', 'batch.7z')
        args = runner.run.call_args.args[0]
        self.assertIn('DEEP_ARCHIVE', args)
        self.assertEqual(args[args.index('--retries') + 1], '1')
        self.assertTrue(runner.run.call_args.kwargs['retry'])
        storage.put(self.source / 'file', 'batch.7z.json', standard=True)
        self.assertIn('STANDARD', runner.run.call_args.args[0])
        self.assertNotIn('DEEP_ARCHIVE', runner.run.call_args.args[0])

    def test_unknown_inventory_failure_is_fatal(self):
        runner = Mock()
        runner.run.side_effect = BackupError('network')
        storage = Storage(replace(self.config.jobs[0], type='s3', destination='aws:bucket'), self.config, runner)
        with self.assertRaises(BackupError):
            storage.inventory()

    def test_bad_inventory_and_path_traversal(self):
        self.destination.mkdir()
        runner = Mock()
        storage = Storage(self.config.jobs[0], self.config, runner)
        for text in ('not json', '[{}]', '[{"Path":"../x"}]'):
            with self.subTest(text=text), self.assertRaises(BackupError):
                runner.run.return_value = text
                storage.inventory()
        with self.assertRaises(BackupError):
            storage.path('../outside')

    def test_idempotent_delete_uses_literal_paths_without_shell(self):
        runner = Mock()
        storage = Storage(self.config.jobs[0], self.config, runner)
        storage.delete('.sambinha/history/a/[wild]*?.txt')
        args = runner.run.call_args.args[0]
        self.assertEqual(args[1], 'delete')
        self.assertIn('--files-from-raw', args)
        self.assertEqual(runner.run.call_args.kwargs['input'], '.sambinha/history/a/[wild]*?.txt\n')

    def test_copy_reports_actual_transfer_or_identical_skip(self):
        runner = Mock()
        storage = Storage(self.config.jobs[0], self.config, runner)
        for report, transferred in (('{"msg":"Copied (new)"}', True),
                                    ('{"msg":"Copied (replaced existing)"}', True),
                                    ('{"msg":"Unchanged skipping"}', False),
                                    ('', False)):
            with self.subTest(report=report):
                runner.last_stderr = report
                self.assertEqual(storage.put(self.source / 'file', 'file'), transferred)
