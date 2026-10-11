from contextlib import redirect_stdout
from dataclasses import replace
import io
import json
import threading
from unittest.mock import Mock, patch

from sambinha.engine import run
from sambinha.inventory import scan
from sambinha.progress import Progress
from tests.helpers import Sandbox


class Terminal(io.StringIO):
    def isatty(self):
        return True


class ProgressTests(Sandbox):
    def test_progress_has_stage_directory_and_percentage_and_is_throttled(self):
        terminal = Terminal()
        clock = Mock(return_value=0)
        progress = Progress(terminal, clock=clock)
        progress.update('Inventário simples', '/dados/pasta', 1, 4)
        first = terminal.getvalue()
        self.assertIn('Inventário simples | /dados/pasta |  25.0% estimado', first)
        progress.update('Inventário simples', '/dados/outra', 2, 4)
        self.assertEqual(terminal.getvalue(), first)
        clock.return_value = 0.3
        progress.update('Inventário simples', '/dados/outra', 3, 4)
        progress.update('Inventário simples', '/dados/outra', 4, 4)
        self.assertIn('100.0% estimado', terminal.getvalue())
        progress.close()
        self.assertFalse(progress.visible)

    def test_progress_is_disabled_outside_terminal_and_sanitizes_paths(self):
        output = io.StringIO()
        progress = Progress(output)
        with progress.task('Copiando', '/dados'):
            progress.update('Lendo', '/dados', 1, 1)
        self.assertEqual(output.getvalue(), '')
        terminal = Terminal()
        Progress(terminal, redact=lambda s: s.replace('secret', '[REDACTED]')).update('Lendo', '/secret/\n\x1bname')
        self.assertNotIn('secret', terminal.getvalue())
        self.assertIn('/[REDACTED]/??name', terminal.getvalue())

    def test_discovery_and_each_mode_report_directories_and_completion(self):
        self.file('dir/a', b'a' * 200000)
        self.file('other/b')
        for mode, stage in (('simple', 'Inventário simples'), ('hash', 'Calculando hashes')):
            with self.subTest(mode=mode):
                progress = Mock()
                scan(self.source, mode=mode, progress=progress)
                calls = progress.update.call_args_list
                self.assertTrue(any(c.args[0] == 'Listando origem' for c in calls))
                self.assertTrue(any(c.args[0] == stage and str(c.args[1]).endswith('/dir') for c in calls))
                self.assertEqual(calls[-1].args[-2:], (1, 1))
                if mode == 'hash':
                    self.assertTrue(any(c.args[0] == stage and 0 < c.args[2] < c.args[3] for c in calls))

    def test_terminal_progress_never_enters_stdout_or_json_logs(self):
        self.file('dir/a')
        terminal = Terminal()
        runner = Mock()
        stdout = io.StringIO()
        with patch('sys.stderr', terminal), redirect_stdout(stdout):
            result = run(replace(self.config, dry_run=True, verification_mode='simple'), runner=runner,
                         clock=lambda: 1000)
        self.assertEqual(result, 0)
        self.assertIn('Inventário simples', terminal.getvalue())
        for text in (stdout.getvalue(), next(self.config.log_dir.glob('*.jsonl')).read_text()):
            records = [json.loads(line) for line in text.splitlines()]
            self.assertTrue(records)
            self.assertNotIn('estimado', text)
            self.assertNotIn('\x1b', text)

    def test_opaque_task_reaches_completion_and_cleans_up_on_failure(self):
        terminal = Terminal()
        progress = Progress(terminal)
        with progress.task('Copiando', '/dados'):
            pass
        self.assertIn('100.0% estimado', terminal.getvalue())
        terminal.truncate(0)
        terminal.seek(0)
        with self.assertRaises(RuntimeError):
            with progress.task('Comprimindo', '/dados'):
                raise RuntimeError('failed')
        self.assertNotIn('100.0% estimado', terminal.getvalue())
        self.assertFalse(progress.visible)

    def test_interrupted_backup_does_not_log_a_successful_end(self):
        with patch('sambinha.engine.preflight', side_effect=KeyboardInterrupt), \
             redirect_stdout(io.StringIO()), self.assertRaises(KeyboardInterrupt):
            run(self.config, runner=Mock())
        records = [json.loads(line) for line in next(self.config.log_dir.glob('*.jsonl')).read_text().splitlines()]
        self.assertEqual(records[-1]['event'], 'end')
        self.assertFalse(records[-1]['success'])

    def test_long_subprocess_estimate_stays_below_completion_until_it_finishes(self):
        updated = threading.Event()
        class ReportingTerminal(Terminal):
            def write(self, value):
                result = super().write(value)
                if '95.0% estimado' in value:
                    updated.set()
                return result
        terminal = ReportingTerminal()
        clock = Mock(return_value=0)
        progress = Progress(terminal, clock=clock)
        with progress.task('Lendo destino', '/dados'):
            clock.return_value = 1000
            self.assertTrue(updated.wait(timeout=3))
            self.assertNotIn('100.0% estimado', terminal.getvalue())
        self.assertIn('100.0% estimado', terminal.getvalue())
