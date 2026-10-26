"""Ctrl+C and SIGTERM stop children promptly and unwind every cleanup block."""
import io
import json
import os
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import skipUnless
from unittest.mock import Mock, patch

from sambinha.config import with_job_overrides
from sambinha.config_validation import validate
from sambinha.engine import run
from sambinha.errors import BackupError
from sambinha.process import Interrupted, Runner, Terminated, handle_termination
from sambinha.recovery import recover
from tests.helpers import REAL_TOOLS, Sandbox

ROOT = Path(__file__).resolve().parent.parent


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def wait_until(condition, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.02)
    return condition()


class RunnerCancellationTests(unittest.TestCase):
    def test_cancel_kills_a_running_child_promptly_without_retrying(self):
        runner = Runner(sleep=Mock())
        outcome = {}

        def execute():
            try:
                runner.run(['sleep', '30'], retry=True)
            except BaseException as exc:
                outcome['error'] = exc

        thread = threading.Thread(target=execute)
        thread.start()
        self.assertTrue(wait_until(lambda: runner.cancellation.processes))
        child = next(iter(runner.cancellation.processes))
        started = time.monotonic()
        runner.cancel()
        thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertLess(time.monotonic() - started, 5)
        self.assertIsInstance(outcome['error'], Interrupted)
        self.assertIsInstance(outcome['error'], KeyboardInterrupt)
        self.assertIsNotNone(child.poll())
        runner.sleep.assert_not_called()
        self.assertEqual(runner.cancellation.processes, set())

    def test_cancel_reaches_grandchildren_through_the_process_group(self):
        runner = Runner()
        pid_file = Path(self.enterContext(tempfile.TemporaryDirectory())) / 'pid'
        thread = threading.Thread(target=lambda: self.assertRaises(
            Interrupted, runner.run, ['sh', '-c', f'sleep 30 & echo $! > "{pid_file}"; wait']))
        thread.start()
        self.assertTrue(wait_until(lambda: pid_file.is_file() and pid_file.read_text().strip()))
        grandchild = int(pid_file.read_text())
        self.assertTrue(alive(grandchild))
        runner.cancel()
        thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertTrue(wait_until(lambda: not alive(grandchild)))

    def test_cancelled_runner_refuses_to_spawn_new_children(self):
        runner = Runner(sleep=Mock())
        runner.cancel()
        with patch('sambinha.process.subprocess.Popen') as popen, self.assertRaises(Interrupted):
            runner.run(['sleep', '30'], retry=True)
        popen.assert_not_called()
        runner.sleep.assert_not_called()

    def test_copied_worker_runners_share_the_cancellation_token(self):
        import copy
        runner = Runner()
        worker = copy.copy(runner)
        runner.cancel()
        self.assertTrue(worker.cancelled)
        with self.assertRaises(Interrupted):
            worker.check_cancelled()

    def test_failures_without_cancellation_still_retry_and_redact(self):
        runner = Runner(sleep=Mock(), env={'AWS_SECRET_ACCESS_KEY': 'PRIVATE'})
        with self.assertRaises(BackupError) as error:
            runner.run(['sh', '-c', 'echo PRIVATE >&2; exit 3'], retry=True)
        self.assertNotIn('PRIVATE', str(error.exception))
        self.assertIn('[REDACTED]', str(error.exception))
        self.assertEqual(runner.sleep.call_count, 3)
        self.assertEqual(runner.cancellation.processes, set())

    def test_streaming_runs_are_tracked_and_released(self):
        runner = Runner()
        seen = []
        runner.run(['sh', '-c', 'echo \'{"msg":"hello"}\''], on_output=lambda record: seen.append(
            (record['msg'], len(runner.cancellation.processes))))
        self.assertEqual(seen, [('hello', 1)])
        self.assertEqual(runner.cancellation.processes, set())

    def test_interruption_during_the_grace_period_still_kills_survivors(self):
        runner = Runner()
        # A child ignoring SIGTERM only dies through the SIGKILL fallback.
        thread = threading.Thread(target=lambda: self.assertRaises(
            Interrupted, runner.run, ['sh', '-c', 'trap "" TERM; sleep 30']))
        thread.start()
        self.assertTrue(wait_until(lambda: runner.cancellation.processes))
        child = next(iter(runner.cancellation.processes))
        real_wait = child.wait

        def interrupted_wait(*args, **kwargs):
            # Only the cancelling (main) thread is interrupted; the worker's
            # communicate() must still reap the child normally.
            if threading.current_thread() is threading.main_thread():
                raise KeyboardInterrupt()
            return real_wait(*args, **kwargs)

        with patch.object(child, 'wait', side_effect=interrupted_wait), self.assertRaises(KeyboardInterrupt):
            runner.cancellation.cancel(grace=5)
        thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertTrue(wait_until(lambda: not alive(child.pid)), 'child survived the interrupted cancel')


class TerminationHandlerTests(unittest.TestCase):
    def test_sigterm_in_the_main_thread_raises_terminated_and_restores_the_handler(self):
        previous = signal.getsignal(signal.SIGTERM)
        with self.assertRaises(Terminated), handle_termination():
            self.assertNotEqual(signal.getsignal(signal.SIGTERM), previous)
            os.kill(os.getpid(), signal.SIGTERM)
            time.sleep(1)
            self.fail('SIGTERM did not interrupt the main thread')
        self.assertEqual(signal.getsignal(signal.SIGTERM), previous)

    def test_handler_installed_outside_python_is_left_alone_on_exit(self):
        with patch('sambinha.process.signal.signal', return_value=None) as install, handle_termination():
            pass
        self.assertEqual(install.call_count, 1)
        self.assertIs(install.call_args.args[0], signal.SIGTERM)

    def test_handler_is_not_installed_from_worker_threads(self):
        previous = signal.getsignal(signal.SIGTERM)
        observed = []

        def worker():
            with handle_termination():
                observed.append(signal.getsignal(signal.SIGTERM))

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join(5)
        self.assertEqual(observed, [previous])
        self.assertEqual(signal.getsignal(signal.SIGTERM), previous)


class EngineTerminationTests(Sandbox):
    def events(self, output):
        return [json.loads(line) for line in output.getvalue().splitlines()]

    def terminating_runner(self):
        runner = Mock()
        runner.run.side_effect = lambda *args, **kwargs: os.kill(os.getpid(), signal.SIGTERM)
        return runner

    def test_backup_logs_an_unsuccessful_end_on_sigterm(self):
        self.file('a')
        previous = signal.getsignal(signal.SIGTERM)
        output = io.StringIO()
        config = validate(with_job_overrides(self.config, log_terminal_level='detailed'))
        with redirect_stdout(output), self.assertRaises(Terminated):
            run(config, runner=self.terminating_runner(), notifier=lambda *args: None)
        events = self.events(output)
        self.assertEqual([e['success'] for e in events if e['event'] == 'end'], [False])
        self.assertEqual(signal.getsignal(signal.SIGTERM), previous)

    def test_recovery_logs_an_unsuccessful_end_on_sigterm(self):
        previous = signal.getsignal(signal.SIGTERM)
        output = io.StringIO()
        config = validate(with_job_overrides(self.config, log_terminal_level='detailed'))
        with redirect_stdout(output), self.assertRaises(Terminated):
            recover(config, 'verify-local-metadata', runner=self.terminating_runner())
        events = self.events(output)
        self.assertEqual([e['success'] for e in events if e['event'] == 'end'], [False])
        self.assertEqual(signal.getsignal(signal.SIGTERM), previous)

    def test_cli_exit_codes_distinguish_sigterm_from_ctrl_c(self):
        from sambinha.cli import main
        with patch('sambinha.cli.load'), patch('sambinha.cli.run', side_effect=Terminated), \
                redirect_stdout(io.StringIO()):
            self.assertEqual(main(['backup']), 143)
        with patch('sambinha.cli.load'), patch('sambinha.cli.run', side_effect=KeyboardInterrupt), \
                redirect_stdout(io.StringIO()):
            self.assertEqual(main(['backup']), 130)


@skipUnless(REAL_TOOLS, 'requires rclone')
class CommandLineTerminationTests(Sandbox):
    def fake_compressor(self, pid_file):
        path = self.root / 'bin' / 'fake7z'
        path.parent.mkdir(exist_ok=True)
        path.write_text('#!/bin/sh\n'
                        'case "$1" in\n'
                        '  -h) echo "7-Zip (z) 25.01 (x64)" ;;\n'
                        f'  a) echo $$ > "{pid_file}"; exec sleep 60 ;;\n'
                        'esac\n')
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        return path

    def test_sigterm_ends_the_run_cleanly(self):
        self.file('a', b'contents')
        pid_file = self.root / 'compressor.pid'
        temp_dir = self.root / 'tmp'
        temp_dir.mkdir()
        self.destination.mkdir()
        config = self.root / 'sambinha.toml'
        config.write_text(f'[backup]\nstate_dir = "state"\nlog_dir = "logs"\ntemp_dir = "tmp"\n'
                          f'[[jobs]]\nname = "test"\nsource = "source"\ndestination = "destination"\n'
                          f'logic = "batch"\ncompressor = "{self.fake_compressor(pid_file)}"\n'
                          f'log_file_level = "summary"\n')
        process = subprocess.Popen([sys.executable, '-m', 'sambinha', 'backup', '--config', str(config)],
                                   cwd=ROOT, env={**os.environ, 'PYTHONPATH': str(ROOT)},
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertTrue(wait_until(lambda: pid_file.is_file() and pid_file.read_text().strip(), timeout=30),
                            'compression never started')
            compressor = int(pid_file.read_text())
            self.assertTrue(any(p.name.startswith('sambinha-') for p in temp_dir.iterdir()))
            process.send_signal(signal.SIGTERM)
            _stdout, stderr = process.communicate(timeout=20)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
        self.assertEqual(process.returncode, 143, stderr)
        self.assertIn('SIGTERM', stderr)
        logs = list((self.root / 'logs').glob('*.jsonl'))
        self.assertEqual(len(logs), 1)
        events = [json.loads(line) for line in logs[0].read_text().splitlines()]
        self.assertEqual([e['success'] for e in events if e['event'] == 'end'], [False])
        self.assertEqual([e['success'] for e in events if e['event'] == 'summary'], [False])
        self.assertEqual(list(temp_dir.iterdir()), [], 'temporary staging left behind')
        self.assertTrue(wait_until(lambda: not alive(compressor)), 'fake compressor survived')
        self.assertFalse((self.root / 'state' / 'test' / 'state.json').exists())


if __name__ == '__main__':
    unittest.main()
