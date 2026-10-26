import io
import json
import re
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from unittest.mock import patch

from sambinha.config import with_job_overrides
from sambinha.engine import run
from sambinha.process import Runner
from tests.helpers import REAL_TOOLS, Sandbox, Terminal, contents


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
            result = run(with_job_overrides(self.config, verification_mode='simple'), clock=lambda: 1000)
        self.assertEqual(result, 0, stdout.getvalue())
        self.assertEqual(directories, [str(self.source)])
        self.assertEqual(contents(self.source), contents(self.destination))
        self.assertIn('Sincronizando', terminal.getvalue())
        for name in ('file-a', 'file-b', 'file-c', 'file-d'):
            self.assertNotIn(name, terminal.getvalue() + stdout.getvalue())
        records = [json.loads(line) for line in next(self.config.log_dir.glob('*.jsonl')).read_text().splitlines()]
        self.assertFalse(any(r['event'] == 'transferred' for r in records))
        values = [float(p) for p in re.findall(r'(\d+\.\d+)% (?:do backup|est\.)', terminal.getvalue())]
        self.assertEqual(values, sorted(values))
        self.assertEqual(sum(p == 100 for p in values), 1)

    def test_multiple_jobs_share_one_overall_percentage_until_all_are_done(self):
        self.file('subdir/file')
        first = self.config.jobs[0]
        second = replace(first, name='second', destination=str(self.root / 'second'))
        config = with_job_overrides(replace(self.config, jobs=(first, second)), verification_mode='simple')
        terminal, stdout = Terminal(), io.StringIO()
        with patch('sys.stderr', terminal), redirect_stdout(stdout):
            result = run(config, clock=lambda: 1000)
        self.assertEqual(result, 0, stdout.getvalue())
        values = [float(p) for p in re.findall(r'(\d+\.\d+)% (?:do backup|est\.)', terminal.getvalue())]
        self.assertEqual(values, sorted(values))
        self.assertIn(47.5, values)
        self.assertEqual(sum(p == 100 for p in values), 1)
        self.assertEqual(contents(self.destination), contents(self.root / 'second'))
