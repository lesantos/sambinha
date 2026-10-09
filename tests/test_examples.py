"""Keep public examples complete and parseable as configuration evolves."""
from dataclasses import fields
from pathlib import Path
import re
import unittest
from unittest.mock import patch

from sambinha.config import Config, Job, load
from sambinha.secrets import read_environment

ROOT = Path(__file__).resolve().parents[1]


class ExampleTests(unittest.TestCase):
    def test_all_public_options_present_even_when_commented(self):
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
        expected = {field.name for field in fields(Config)} - {'jobs', 'environment'}
        self.assertEqual(global_keys, expected)
        expected_jobs = {field.name for field in fields(Job)}
        self.assertGreaterEqual(len(job_keys), 2)
        for index, keys in enumerate(job_keys):
            with self.subTest(job=index):
                self.assertEqual(keys, expected_jobs)

    def test_active_toml_example_remains_valid(self):
        with patch.dict('os.environ', {'CRONITOR_URL': ''}):
            config = load(ROOT / 'sambinha.example.toml')
        self.assertEqual(len(config.jobs), 2)
        self.assertIsNone(config.secrets_file)

    def test_secrets_example_is_valid_without_real_credentials(self):
        environment = read_environment(ROOT / 'secrets.example.env', {})
        self.assertEqual(environment, {'AWS_PROFILE': 'sambinha'})
