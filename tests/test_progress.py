from contextlib import redirect_stdout
from dataclasses import replace
import io
import json
import threading
import re
from unittest.mock import Mock, patch

from sambinha.engine import run
from sambinha.inventory import scan
from sambinha.progress import Progress
from sambinha.process import Runner
from tests.helpers import Sandbox, REAL_TOOLS, contents
import unittest


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
        self.assertTrue(first.startswith('\r\033[2K 25.0% do backup (estimado) | Inventário simples | /dados/pasta'))
        progress.update('Inventário simples', '/dados/outra', 2, 4)
        self.assertIn('/dados/outra', terminal.getvalue())
        second = terminal.getvalue()
        progress.update('Inventário simples', '/dados/outra', 2, 4)
        self.assertEqual(terminal.getvalue(), second)
        clock.return_value = 0.3
        progress.update('Inventário simples', '/dados/outra', 3, 4)
        progress.update('Inventário simples', '/dados/outra', 4, 4)
        self.assertNotIn('100.0%', terminal.getvalue())
        progress.finish()
        self.assertIn('100.0% do backup', terminal.getvalue())
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
                progress.scope.side_effect = lambda *args: progress
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
            result = run(replace(self.config, dry_run=True, verification_mode='simple',
                                 jobs=(replace(self.config.jobs[0], logic='batch'),)), runner=runner,
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
        self.assertIn('99.9% do backup', terminal.getvalue())
        terminal.truncate(0)
        terminal.seek(0)
        with self.assertRaises(RuntimeError):
            with progress.task('Comprimindo', '/dados'):
                raise RuntimeError('failed')
        self.assertNotIn('100.0%', terminal.getvalue())
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
                if '95.0% do backup' in value:
                    updated.set()
                return result
        terminal = ReportingTerminal()
        clock = Mock(return_value=0)
        progress = Progress(terminal, clock=clock)
        with progress.task('Lendo destino', '/dados'):
            clock.return_value = 1000
            self.assertTrue(updated.wait(timeout=3))
            self.assertNotIn('100.0%', terminal.getvalue())
        self.assertIn('99.9% do backup', terminal.getvalue())

    def test_overall_progress_never_restarts_between_scopes_and_retries(self):
        terminal = Terminal()
        progress = Progress(terminal)
        first = progress.scope(0, 0.2)
        first.update('Inventário', '/dados/dir-a', 1, 1)
        copying = progress.scope(0.2, 0.9)
        copying.update('Copiando', '/dados/dir-b', 1, 2)
        copying.update('Copiando', '/dados/dir-c', 0, 2)
        with copying.scope(0, 0.5).task('Repetindo cópia', '/dados/dir-c'):
            pass
        copying.update('Copiando', '/dados/dir-c', 2, 2)
        self.assertNotIn('100.0%', terminal.getvalue())
        progress.finish()
        values = [float(p) for p in re.findall(r'(\d+\.\d+)% do backup', terminal.getvalue())]
        self.assertEqual(values, sorted(values))
        self.assertEqual(values[-1], 100)
        self.assertIn(55, values)

    def test_parallel_workers_contribute_by_bytes_even_when_last_worker_finishes_first(self):
        terminal = Terminal()
        progress = Progress(terminal)
        first, last = progress.scope(0.2, 0.8).parallel([9, 1])
        last.update('Lote', '/dados/last', 1, 1)
        self.assertAlmostEqual(progress.percent, 26)
        first.update('Lote', '/dados/first', 1, 2)
        self.assertAlmostEqual(progress.percent, 53)
        first.update('Lote', '/dados/first', 1, 1)
        self.assertAlmostEqual(progress.percent, 80)

    def test_terminal_hides_per_file_events_but_complete_audit_and_subdirectory_remain(self):
        self.file('nested/subdir/unique-file-name')
        terminal = Terminal()
        stdout = io.StringIO()
        with patch('sys.stderr', terminal), redirect_stdout(stdout):
            result = run(replace(self.config, dry_run=True, verification_mode='simple',
                                 jobs=(replace(self.config.jobs[0], logic='batch'),)),
                         runner=Mock(), clock=lambda: 1000)
        self.assertEqual(result, 0)
        self.assertNotIn('unique-file-name', stdout.getvalue())
        self.assertNotIn('unique-file-name', terminal.getvalue())
        self.assertIn(str(self.source / 'nested/subdir'), terminal.getvalue())
        records = [json.loads(line) for line in next(self.config.log_dir.glob('*.jsonl')).read_text().splitlines()]
        self.assertTrue(any('nested/subdir/unique-file-name' in r.get('files', []) for r in records))
        values = [float(p) for p in re.findall(r'(\d+\.\d+)% do backup', terminal.getvalue())]
        self.assertEqual(values, sorted(values))
        self.assertEqual(values[-1], 100)
        self.assertEqual(sum(p == 100 for p in values), 1)

    def test_failure_never_displays_complete_percentage(self):
        terminal = Terminal()
        with patch('sys.stderr', terminal), redirect_stdout(io.StringIO()), \
             patch('sambinha.engine.preflight', side_effect=RuntimeError('failure')), \
             self.assertRaises(RuntimeError):
            run(self.config, runner=Mock())
        self.assertNotIn('100.0%', terminal.getvalue())


@unittest.skipUnless(REAL_TOOLS, 'rclone and 7z required')
class OverallProgressIntegrationTests(Sandbox):
    def test_simple_sync_uses_rclone_progress_and_keeps_file_names_out_of_terminal(self):
        for name in ('nested/subdir/file-a', 'nested/subdir/file-b', 'other/file-c', 'other/file-d'):
            self.file(name)
        terminal, stdout = Terminal(), io.StringIO()
        directories = []
        original = Runner.run
        def record(runner, args, **kwargs):
            if len(args) > 1 and args[1] == 'sync':
                directories.append(kwargs.get('progress_directory'))
            return original(runner, args, **kwargs)
        with patch('sys.stderr', terminal), redirect_stdout(stdout), patch.object(Runner, 'run', record):
            result = run(replace(self.config, verification_mode='simple'), clock=lambda: 1000)
        self.assertEqual(result, 0, stdout.getvalue())
        self.assertEqual(directories, [str(self.source)])
        self.assertEqual(contents(self.source), contents(self.destination))
        self.assertIn('Sincronizando arquivos | ' + str(self.source), terminal.getvalue())
        for name in ('file-a', 'file-b', 'file-c', 'file-d'):
            self.assertNotIn(name, terminal.getvalue() + stdout.getvalue())
        records = [json.loads(line) for line in next(self.config.log_dir.glob('*.jsonl')).read_text().splitlines()]
        self.assertFalse(any(r['event'] == 'transferred' for r in records))
        values = [float(p) for p in re.findall(r'(\d+\.\d+)% do backup', terminal.getvalue())]
        self.assertEqual(values, sorted(values))
        self.assertEqual(sum(p == 100 for p in values), 1)

    def test_multiple_jobs_share_one_overall_percentage_until_all_are_done(self):
        self.file('subdir/file')
        first = self.config.jobs[0]
        second = replace(first, name='second', destination=str(self.root / 'second'))
        config = replace(self.config, jobs=(first, second), verification_mode='simple')
        terminal, stdout = Terminal(), io.StringIO()
        with patch('sys.stderr', terminal), redirect_stdout(stdout):
            result = run(config, clock=lambda: 1000)
        self.assertEqual(result, 0, stdout.getvalue())
        values = [float(p) for p in re.findall(r'(\d+\.\d+)% do backup', terminal.getvalue())]
        self.assertEqual(values, sorted(values))
        self.assertIn(47.5, values)
        self.assertEqual(sum(p == 100 for p in values), 1)
        self.assertEqual(contents(self.destination), contents(self.root / 'second'))
