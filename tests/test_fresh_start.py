import json
import unittest
from dataclasses import replace
from unittest.mock import patch

from sambinha import preflight
from sambinha.cli import main, parser
from sambinha.config import load, validate
from sambinha.errors import ConfigError
from tests.helpers import REAL_TOOLS, Sandbox, tick


class FreshStartOptionTests(Sandbox):
    def test_resume_is_validated_and_defaults_to_auto(self):
        job = self.config.jobs[0]
        self.assertEqual(job.resume, 'auto')
        validate(replace(self.config, jobs=(replace(job, resume='fresh'),)))
        for value in ('Fresh', 'never', True, None):
            with self.subTest(value=value), self.assertRaises(ConfigError):
                validate(replace(self.config, jobs=(replace(job, resume=value),)))

    def test_resume_loads_from_toml_but_fresh_start_is_run_scoped(self):
        toml = self.root / 'sambinha.toml'
        toml.write_text(f'[[jobs]]\nname = "x"\nsource = "{self.source}"\ndestination = "{self.destination}"\n'
                        'logic = "batch"\nresume = "fresh"\n')
        self.assertEqual(load(toml).jobs[0].resume, 'fresh')
        toml.write_text(toml.read_text() + 'fresh_start = true\n')
        with self.assertRaisesRegex(ConfigError, 'fresh_start'):
            load(toml)

    def test_flags_parse_and_only_apply_to_backup(self):
        args = parser().parse_args(['--fresh-start', '--prune-orphans'])
        self.assertTrue(args.fresh_start and args.prune_orphans)
        for flag in ('--fresh-start', '--prune-orphans'):
            with self.subTest(flag=flag), patch('sys.stderr'), self.assertRaises(SystemExit) as caught:
                main(['verify-local-metadata', flag, '--config', str(self.root / 'none.toml')])
            self.assertEqual(caught.exception.code, 2)


@unittest.skipUnless(REAL_TOOLS, 'rclone and 7z required')
class FreshStartTests(Sandbox):
    def setUp(self):
        super().setUp()
        job = replace(self.config.jobs[0], logic='batch', max_megabytes=10 / 1024 ** 2, batch_workers=1)
        self.config = replace(self.config, jobs=(job,))
        for name in 'abcd':
            self.file(name, name.encode() * 4)
        self.assertEqual(self.execute(now=1000), 0, self.output.getvalue())
        self.directory = self.config.state_dir / 'test'
        self.before = self.state()
        # Hints a previous interrupted run could have left behind.
        (self.directory / 'uploads.json').write_text('{}')
        (self.directory / 'publication.json').write_text('{}')

    def fresh_run(self, config=None, **kwargs):
        caches = []
        real_scan = preflight.scan

        def spy(*args, **options):
            caches.append(options.get('cache'))
            return real_scan(*args, **options)
        with patch('sambinha.preflight.scan', spy):
            code = self.execute(config=config, now=2000, **kwargs)
        return code, caches

    def check_fresh(self, config=None, **kwargs):
        self.assertTrue(json.loads((self.directory / 'fingerprints.json').read_text()))
        code, caches = self.fresh_run(config, **kwargs)
        self.assertEqual(code, 0, self.output.getvalue())
        self.assertEqual(caches, [None])  # cache discarded: complete rescan and rehash
        (event,) = [e for e in self.events if e['event'] == 'fresh_start']
        self.assertEqual(sorted(event['discarded']),
                         ['fingerprints', 'last_full_scan', 'publication.json', 'uploads.json'])
        self.assertFalse((self.directory / 'uploads.json').exists())
        self.assertFalse((self.directory / 'publication.json').exists())
        after = self.state()
        self.assertEqual(after['active'], self.before['active'])  # nothing re-uploaded
        self.assertEqual(after['last_full_scan'], 2000)
        self.assertNotIn('fingerprints', after)
        self.assertTrue(json.loads((self.directory / 'fingerprints.json').read_text()))  # rebuilt

    def test_flag_discards_caches_and_journals_but_keeps_active(self):
        self.check_fresh(fresh_start=True)

    def test_toml_resume_fresh_is_equivalent(self):
        self.check_fresh(replace(self.config, jobs=(replace(self.config.jobs[0], resume='fresh'),)))

    def test_auto_keeps_using_the_cache(self):
        code, caches = self.fresh_run()
        self.assertEqual(code, 0)
        self.assertIsNotNone(caches[0])
        self.assertFalse([e for e in self.events if e['event'] == 'fresh_start'])

    def test_changed_file_is_still_detected_after_a_fresh_start(self):
        tick()
        self.file('a', b'changed!')
        code, _ = self.fresh_run(fresh_start=True)
        self.assertEqual(code, 0, self.output.getvalue())
        files = {f['path']: f for c in self.state()['active'].values() for f in c['files']}
        self.assertEqual(files['a']['size'], 8)
        self.assertNotEqual(self.state()['active'].keys(), self.before['active'].keys())
        self.assertTrue(json.loads((self.directory / 'state.json').read_text())['retired'])

    def test_dry_run_reports_but_keeps_journals(self):
        code, _ = self.fresh_run(replace(self.config, dry_run=True), fresh_start=True)
        self.assertEqual(code, 0)
        (event,) = [e for e in self.events if e['event'] == 'fresh_start']
        self.assertTrue(event['dry_run'])
        self.assertTrue((self.directory / 'uploads.json').exists())
