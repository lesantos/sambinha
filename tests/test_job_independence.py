"""Real transfers prove that each job observes the previous job's completed work."""
from contextlib import redirect_stdout
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import tarfile
import unittest
from unittest.mock import patch

from sambinha.engine import DAY, Context
from sambinha.config import validate
from sambinha.errors import ConfigError
from sambinha.inventory import scan
from sambinha.recovery import recover
from tests.helpers import REAL_TOOLS, Sandbox, contents


@unittest.skipUnless(REAL_TOOLS, 'rclone and 7z required')
class IndependentJobTests(Sandbox):
    def verify(self, config):
        with redirect_stdout(io.StringIO()) as output:
            result = recover(config, 'verify')
        self.assertEqual(result, 0, output.getvalue())

    def chain(self, base, mode='hash', logic='simple'):
        source = base / 'source'
        source.mkdir(parents=True)
        for name, payload in (('changed', b'old'), ('deleted', b'delete')):
            path = source / name
            path.write_bytes(payload)
            os.utime(path, ns=(1000000000, 1000000000))
        first = replace(self.config.jobs[0], source=source, destination=str(base / 'middle' / 'backup'))
        second = replace(first, name='second', source=base / 'middle',
                         destination=str(base / 'final'), logic=logic)
        return replace(self.config, jobs=(first, second), state_dir=base / 'state', log_dir=base / 'logs',
                       verification_mode=mode, verify_interval_days=1)

    def execute_separately(self, config, now):
        for job in config.jobs:
            self.assertEqual(self.execute(replace(config, jobs=(job,)), now=now), 0, self.output.getvalue())

    def test_chain_matches_separate_commands_after_add_change_delete_and_verification(self):
        for mode in ('simple', 'hash'):
            for logic in ('simple', 'batch'):
                with self.subTest(mode=mode, logic=logic):
                    combined = self.chain(self.root / f'combined-{mode}-{logic}', mode, logic)
                    separate = self.chain(self.root / f'separate-{mode}-{logic}', mode, logic)
                    self.assertEqual(self.execute(combined), 0, self.output.getvalue())
                    self.execute_separately(separate, 1000)
                    for config in (combined, separate):
                        source = config.jobs[0].source
                        (source / 'changed').write_bytes(b'new contents')
                        (source / 'deleted').unlink()
                        (source / 'added').write_bytes(b'new file')
                        for name in ('changed', 'added'):
                            os.utime(source / name, ns=(2000000000, 2000000000))
                    self.assertEqual(self.execute(combined, now=1000 + DAY), 0, self.output.getvalue())
                    self.execute_separately(separate, 1000 + DAY)
                    for config in (combined, separate):
                        self.verify(config)
                        first, second = config.jobs
                        self.assertEqual(contents(Path(first.destination)), contents(first.source))
                        expected = {p.relative_to(second.source).as_posix(): p.read_bytes()
                                    for p in second.source.rglob('*') if p.is_file()
                                    and (logic == 'batch' or '.sambinha' not in p.relative_to(second.source).parts)}
                        if logic == 'batch':
                            state = json.loads((config.state_dir / second.name / 'state.json').read_text())
                            restored = config.state_dir.parent / 'restored'
                            with redirect_stdout(io.StringIO()) as output:
                                result = recover(replace(config, jobs=(second,)), 'restore', output=restored)
                            self.assertEqual(result, 0, output.getvalue())
                            actual = {p.relative_to(restored).as_posix(): p.read_bytes()
                                      for p in restored.rglob('*') if p.is_file()}
                            self.assertEqual(state['last_verified'], 1000 + DAY)
                        else:
                            actual = contents(Path(second.destination))
                        self.assertEqual(actual, expected)
                    if logic == 'simple':
                        self.assertEqual(contents(Path(combined.jobs[1].destination)),
                                         contents(Path(separate.jobs[1].destination)))

    def test_next_inventory_waits_for_verification_state_commit_and_summary(self):
        config = self.chain(self.root / 'ordered')
        config = replace(config, jobs=tuple(replace(job, logic='batch') for job in config.jobs))
        stages = []
        original_summary = Context.summary
        def inventory(root, *args, **kwargs):
            stages.append(('scan', root))
            if root == config.jobs[1].source:
                self.assertEqual(stages[-2], ('summary', 'test'))
                state = json.loads((config.state_dir / 'test' / 'state.json').read_text())
                self.assertEqual(state['last_verified'], 1000)
                self.assertTrue(Path(config.jobs[0].destination).exists())
            return scan(root, *args, **kwargs)
        def summary(ctx, success):
            self.assertTrue(success)
            stages.append(('summary', ctx.job.name))
            return original_summary(ctx, success)
        with patch('sambinha.engine.scan', side_effect=inventory), patch.object(Context, 'summary', summary):
            self.assertEqual(self.execute(config), 0, self.output.getvalue())
        self.assertEqual([stage[0] for stage in stages], ['scan', 'summary', 'scan', 'summary'])

    def test_later_preflight_failure_never_prevents_or_rolls_back_an_earlier_job(self):
        self.file('file')
        first = self.config.jobs[0]
        second = replace(first, name='second', source=self.root / 'missing',
                         destination=str(self.root / 'second'), continue_on_job_error=False)
        third = replace(first, name='third', destination=str(self.root / 'third'))
        self.assertEqual(self.execute(replace(self.config, jobs=(first, second, third))), 1)
        self.assertEqual(contents(self.destination), contents(self.source))
        self.assertFalse((self.config.state_dir / 'test' / 'state.json').exists())
        self.assertFalse(Path(third.destination).exists())
        self.assertEqual([e['job'] for e in self.events if e['event'] == 'error'], ['second'])

    def test_previous_job_can_create_the_next_jobs_exclusion_file(self):
        self.file('exclude.txt', b'*.tmp\n')
        next_source = self.root / 'next-source'
        next_source.mkdir()
        (next_source / 'ignored.tmp').write_bytes(b'ignore')
        (next_source / 'keep').write_bytes(b'keep')
        first = replace(self.config.jobs[0], destination=str(next_source / 'rules'))
        second = replace(first, name='second', source=next_source, destination=str(self.destination),
                         exclude_files=next_source / 'rules' / 'exclude.txt')
        config = replace(self.config, jobs=(first, second), verify_interval_days=1)
        self.assertEqual(self.execute(config), 0, self.output.getvalue())
        self.assertEqual(contents(self.destination), {'rules/exclude.txt': b'*.tmp\n', 'keep': b'keep'})
        self.verify(config)

    def test_writing_to_a_previous_origin_invalidates_later_jobs_own_fingerprint_cache(self):
        source = self.file('data/file', b'old')
        ns = source.stat().st_mtime_ns
        updater = self.root / 'updater'
        updater.mkdir()
        (updater / 'file').write_bytes(b'new')
        os.utime(updater / 'file', ns=(ns, ns))
        first = self.config.jobs[0]
        middle = replace(first, name='middle', source=updater, destination=str(self.source / 'data'))
        last = replace(first, name='last', destination=str(self.root / 'last'), logic='batch')
        config = replace(self.config, jobs=(first, middle, last), verify_interval_days=1)
        self.execute_separately(replace(config, jobs=(first, last)), 1000)
        self.assertEqual(self.execute(config, now=2000), 0, self.output.getvalue())
        self.assertEqual((self.destination / 'data/file').read_bytes(), b'old')
        restored = self.root / 'last-restored'
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(recover(replace(config, jobs=(last,)), 'restore', output=restored), 0,
                             output.getvalue())
        self.assertEqual((restored / 'data/file').read_bytes(), b'new')
        state = json.loads((config.state_dir / 'last' / 'state.json').read_text())
        self.assertEqual(state['last_full_scan'], 1000)
        self.verify(replace(config, jobs=(last,)))

    def test_exact_and_nested_shared_destinations_are_rejected(self):
        self.file('first', b'first')
        source = self.root / 'second-source'
        source.mkdir()
        (source / 'second').write_bytes(b'second')
        first = self.config.jobs[0]
        first_rules = self.root / 'first-rules.txt'
        first_rules.write_text('second\nnested/**\n')
        second_rules = self.root / 'second-rules.txt'
        second_rules.write_text('first\n')
        first = replace(first, exclude_files=first_rules)
        for suffix in ('', '/nested'):
            with self.subTest(suffix=suffix):
                second = replace(first, name='second', source=source, destination=first.destination + suffix,
                                 exclude_files=second_rules)
                config = replace(self.config, jobs=(first, second), verify_interval_days=1,
                                 state_dir=self.root / ('nested-state' if suffix else 'shared-state'))
                with self.assertRaisesRegex(ConfigError, 'se sobrepõem'):
                    validate(config)

    def test_shared_file_verifies_before_overwrite_and_later_reports_stale_index(self):
        path = self.file('file', b'old')
        os.utime(path, ns=(1000000000, 1000000000))
        source = self.root / 'second-source'
        source.mkdir()
        (source / 'file').write_bytes(b'new')
        os.utime(source / 'file', ns=(2000000000, 2000000000))
        first = self.config.jobs[0]
        second = replace(first, name='second', source=source)
        config = replace(self.config, jobs=(first, second), verify_interval_days=1)
        with self.assertRaisesRegex(ConfigError, 'se sobrepõem'):
            validate(config)

    def test_shared_batch_destination_keeps_local_indexes_but_remote_control_is_last_writer(self):
        self.file('first', b'first')
        source = self.root / 'second-source'
        source.mkdir()
        (source / 'second').write_bytes(b'second')
        first = replace(self.config.jobs[0], logic='batch')
        second = replace(first, name='second', source=source)
        config = replace(self.config, jobs=(first, second), verify_interval_days=1)
        with self.assertRaisesRegex(ConfigError, 'se sobrepõem'):
            validate(config)

    def test_shared_simple_destination_can_retire_the_previous_jobs_files(self):
        self.file('first', b'first')
        source = self.root / 'second-source'
        source.mkdir()
        (source / 'second').write_bytes(b'second')
        first = self.config.jobs[0]
        second = replace(first, name='second', source=source)
        config = replace(self.config, jobs=(first, second), verify_interval_days=1)
        with self.assertRaisesRegex(ConfigError, 'se sobrepõem'):
            validate(config)

    def test_source_root_metadata_conflict_fails_identically_in_combined_and_separate_runs(self):
        for separate in (False, True):
            with self.subTest(separate=separate):
                config = self.chain(self.root / f'root-metadata-{separate}')
                config = replace(config, jobs=tuple(replace(job, logic='batch') for job in config.jobs))
                first, second = config.jobs
                destination = Path(first.destination)
                destination.mkdir(parents=True)
                (destination / 'unmanaged').write_bytes(b'old')
                second = replace(second, source=destination)
                config = replace(config, jobs=(first, second))
                if separate:
                    self.assertEqual(self.execute(replace(config, jobs=(first,))), 0)
                    snapshot = contents(destination)
                    result = self.execute(replace(config, jobs=(second,)))
                else:
                    result = self.execute(config)
                    snapshot = contents(destination)
                self.assertEqual(result, 1)
                error = next(e for e in self.events if e['event'] == 'error')
                self.assertEqual(error['job'], 'second')
                self.assertEqual(error['error'].count('Job second:'), 1)
                self.assertIn('.sambinha é reservado', error['error'])
                self.assertEqual(contents(destination), snapshot)

    def test_dry_run_matches_separate_dry_runs_without_materializing_the_chain(self):
        config = replace(self.chain(self.root / 'dry'), dry_run=True)
        self.assertEqual(self.execute(config), 1)
        self.assertFalse(config.jobs[1].source.exists())
        self.assertFalse(config.state_dir.exists())
        self.assertEqual([e['job'] for e in self.events if e['event'] == 'error'], ['second'])
        self.assertEqual(self.execute(replace(config, jobs=(config.jobs[0],))), 0)
        self.assertEqual(self.execute(replace(config, jobs=(config.jobs[1],))), 1)
