"""Keep public examples complete and parseable as configuration evolves."""
import re
import unittest
from dataclasses import fields
from pathlib import Path
from unittest.mock import patch

from sambinha.config import JOB_SCOPED_OPTIONS, Config, Job, load
from sambinha.secrets import read_environment

ROOT = Path(__file__).resolve().parents[1]


class ExampleTests(unittest.TestCase):
    def test_english_and_brazilian_portuguese_examples_have_identical_settings(self):
        import tomllib
        from configparser import ConfigParser
        self.assertEqual(tomllib.loads((ROOT / 'sambinha.example.toml').read_text()),
                         tomllib.loads((ROOT / 'sambinha.example.pt-BR.toml').read_text()))
        configs = []
        for name in ('rclone.example.conf', 'rclone.example.pt-BR.conf'):
            config = ConfigParser()
            config.read(ROOT / name)
            configs.append({section: dict(config[section]) for section in config.sections()})
        self.assertEqual(*configs)
        self.assertEqual(read_environment(ROOT / 'secrets.example.env', {}),
                         read_environment(ROOT / 'secrets.example.pt-BR.env', {}))

    def test_every_maintained_markdown_guide_has_reciprocal_language_links(self):
        paths = [ROOT / n for n in ('README.md', 'CHANGELOG.md', 'SECURITY.md')]
        if (ROOT / 'backuper.md').exists():
            paths.append(ROOT / 'backuper.md')
        paths += list((ROOT / 'docs').glob('*.md'))
        for path in paths:
            if '.pt-BR.' in path.name:
                continue
            counterpart = path.with_name(path.stem + '.pt-BR.md')
            with self.subTest(path=path):
                self.assertTrue(counterpart.is_file())
                self.assertIn('(' + counterpart.name + ')', path.read_text())
                self.assertIn('(' + path.name + ')', counterpart.read_text())
        self.assertIn('backs up files', (ROOT / 'README.md').read_text())

    def test_all_public_options_present_even_when_commented(self):
        import tomllib
        section, global_keys, job_keys = None, set(), []
        for line in (ROOT / 'sambinha.example.toml').read_text().splitlines():
            if line.strip() == '[backup]':
                section = global_keys
            elif line.strip() == '[[jobs]]':
                section = set()
                job_keys.append(section)
            else:
                match = re.match(r'^\s*(?:#\s*)?([a-z_]+)\s*=', line)
                if match and section is not None:
                    section.add(match[1])
        expected = {field.name for field in fields(Config)} - {'jobs', 'environment'} - JOB_SCOPED_OPTIONS
        self.assertEqual(global_keys, expected)
        expected_jobs = {field.name for field in fields(Job)} - {
            'max_delete', 'max_delete_percent', 'deletion_min_files'}
        examples = tomllib.loads((ROOT / 'sambinha.example.toml').read_text())['jobs']
        self.assertGreaterEqual(len(job_keys), 2)
        for index, (keys, example) in enumerate(zip(job_keys, examples, strict=False)):
            expected = expected_jobs | ({'max_delete'} if example['logic'] == 'simple'
                                        else {'max_delete_percent', 'deletion_min_files'})
            with self.subTest(job=index):
                self.assertEqual(keys, expected)

    def test_active_toml_example_remains_valid(self):
        with patch.dict('os.environ', {'CRONITOR_URL': ''}):
            config = load(ROOT / 'sambinha.example.toml')
        self.assertEqual(len(config.jobs), 2)
        self.assertIsNone(config.secrets_file)

    def test_secrets_example_is_valid_without_real_credentials(self):
        environment = read_environment(ROOT / 'secrets.example.env', {})
        self.assertEqual(environment, {'AWS_PROFILE': 'sambinha'})
