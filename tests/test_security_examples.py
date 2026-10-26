import re
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class CredentialRuleTests(unittest.TestCase):
    def test_realistic_cronitor_url_is_blocked_without_storing_a_real_token(self):
        config = tomllib.loads((ROOT / '.gitleaks.toml').read_text())
        rule = next(rule for rule in config['rules'] if rule['id'] == 'cronitor-telemetry-url')
        token = '0' * 32
        for host in ('cronitor.link', 'eu.cronitor.link', 'cronitor.io'):
            with self.subTest(host=host):
                value = f'https://{host}/p/{token}/monitor'
                self.assertEqual(re.search(rule['regex'], value).group(rule['secretGroup']), value)
        self.assertTrue(config['extend']['useDefault'])

    def test_placeholders_and_environment_references_are_allowed(self):
        config = tomllib.loads((ROOT / '.gitleaks.toml').read_text())
        rule = next(rule for rule in config['rules'] if rule['id'] == 'cronitor-telemetry-url')
        for value in ('${CRONITOR_URL}', 'https://cronitor.link/p/SEU_TOKEN/SEU_MONITOR',
                      'https://cronitor.link/p/test/monitor'):
            with self.subTest(value=value):
                self.assertIsNone(re.search(rule['regex'], value))
