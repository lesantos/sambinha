from dataclasses import replace
from unittest.mock import patch

from sambinha.cli import main, parser
from sambinha.config import load
from sambinha.config_validation import validate
from sambinha.errors import ConfigError
from tests.helpers import Sandbox


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
