"""Keep public examples complete and parseable as configuration evolves."""
import re
import unittest
from dataclasses import fields
from pathlib import Path
from unittest.mock import patch

from sambinha.config import JOB_SCOPED_OPTIONS, RUN_SCOPED_FIELDS, Config, Job, load
from sambinha.secrets import read_environment

ROOT = Path(__file__).resolve().parents[2]


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
                match = re.match(r'^\s*(?:#\s*)?([a-z0-9_]+)\s*=', line)
                if match and section is not None:
                    section.add(match[1])
        expected = {field.name for field in fields(Config)} - {'jobs', 'environment'} - JOB_SCOPED_OPTIONS
        self.assertEqual(global_keys, expected)
        # Each example job lists only the options that apply to its kind (active or commented). The simple
        # local job also shows the S3-only options as comments, for when its destination becomes S3.
        every = {field.name for field in fields(Job)} - RUN_SCOPED_FIELDS
        batch_only = {'recovery_index_versions', 'batch_workers', 'resume', 'fingerprint_cache',
                      'fingerprint_full_scan_days', 'verify_source_changes', 'inventory_from_listing', 'file_mode',
                      'compression_text_level', 'compression_compressed_level', 'compression_other_level',
                      'compression_text_extensions', 'compression_compressed_extensions', 'compression_threads',
                      'individual_batch_percent', 'batch_size_alert_percent', 'max_megabytes', 'compressor',
                      'archive_format', 'max_delete_percent', 'deletion_min_files', 'control_storage_class'}
        simple_only = {'transfer_workers', 'max_delete', 'fast_list'}
        local_only = {'destination_mount'}
        examples = tomllib.loads((ROOT / 'sambinha.example.toml').read_text())['jobs']
        self.assertGreaterEqual(len(job_keys), 2)
        for index, (keys, example) in enumerate(zip(job_keys, examples, strict=False)):
            expected = every - batch_only if example['logic'] == 'simple' else every - simple_only
            if example['type'] == 'local':
                expected -= {'control_storage_class'}
            else:
                expected -= local_only
            with self.subTest(job=index):
                self.assertEqual(keys, expected)
        self.assertEqual(set().union(*job_keys), every)

    def test_active_toml_example_remains_valid(self):
        with patch.dict('os.environ', {'CRONITOR_URL': ''}):
            config = load(ROOT / 'sambinha.example.toml')
        self.assertEqual(len(config.jobs), 2)
        self.assertIsNone(config.secrets_file)

    def test_secrets_example_is_valid_without_real_credentials(self):
        environment = read_environment(ROOT / 'secrets.example.env', {})
        self.assertEqual(environment, {'AWS_PROFILE': 'sambinha'})
