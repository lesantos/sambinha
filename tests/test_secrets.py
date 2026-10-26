"""Safe dotenv parsing, isolated configuration and subprocess credentials."""
import io
import json
import os
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from sambinha.cli import main, parser
from sambinha.config import load
from sambinha.errors import BackupError, ConfigError
from sambinha.process import Runner
from sambinha.secrets import expand, read_environment
from tests.helpers import Sandbox


class SecretParserTests(Sandbox):
    def secrets(self, text):
        path = self.root / "secrets.env"
        path.write_text(text, encoding="utf-8")
        return path

    def test_export_quotes_comments_empty_and_unicode(self):
        path = self.secrets("# private configuration\n\nexport AWS_PROFILE = 'sambinha'\n"
                            'TOKEN="a b#c" # comment\nEMPTY=\nUTF8=ação\nHASH=a#b\n')
        self.assertEqual(read_environment(path, {}), {
            "AWS_PROFILE": "sambinha", "TOKEN": "a b#c", "EMPTY": "",
            "UTF8": "ação", "HASH": "a#b"})

    def test_bom_crlf_and_multiline(self):
        path = self.root / "secrets.env"
        path.write_bytes(b'\xef\xbb\xbfTOKEN="first\r\nsecond"\r\nOTHER=ok\r\n')
        self.assertEqual(read_environment(path, {}), {"TOKEN": "first\nsecond", "OTHER": "ok"})

    def test_inherited_values_including_empty_take_precedence(self):
        path = self.secrets("TOKEN=file\nEMPTY=file\nNEW=loaded\n")
        inherited = {"TOKEN": "terminal", "EMPTY": "", "KEEP": "untouched"}
        before = dict(os.environ)
        result = read_environment(path, inherited)
        self.assertEqual(result, {"TOKEN": "terminal", "EMPTY": "", "NEW": "loaded", "KEEP": "untouched"})
        self.assertEqual(inherited, {"TOKEN": "terminal", "EMPTY": "", "KEEP": "untouched"})
        self.assertEqual(dict(os.environ), before)

    def test_default_inherits_environment_without_mutating_it(self):
        path = self.secrets("SAMBINHA_NEW_TOKEN=file\n")
        with patch.dict(os.environ, {"SAMBINHA_EXISTING": "terminal"}, clear=True):
            result = read_environment(path)
            self.assertEqual(result, {"SAMBINHA_EXISTING": "terminal", "SAMBINHA_NEW_TOKEN": "file"})
            self.assertNotIn("SAMBINHA_NEW_TOKEN", os.environ)

    def test_shell_substitution_is_literal_and_never_executed(self):
        marker = self.root / "must-not-exist"
        payload = f"$(touch {marker}); `touch {marker}`"
        path = self.secrets(f"TOKEN='{payload}'\nREFERENCE='${{OTHER}}'\n")
        result = read_environment(path, {})
        self.assertEqual(result, {"TOKEN": payload, "REFERENCE": "${OTHER}"})
        self.assertFalse(marker.exists())

    def test_invalid_assignments_redact_values(self):
        for text in ("TOKEN='PRIVATE-VALUE\n", "echo PRIVATE-VALUE\n",
                     "export PRIVATE_VALUE\n", "INVALID-NAME=PRIVATE-VALUE\n",
                     "'INVALID NAME'=PRIVATE-VALUE\n", "TOKEN=PRIVATE-VALUE\x00\n",
                     "TOKEN=PRIVATE-VALUE\nTOKEN=again\n"):
            with self.subTest(text=text):
                path = self.secrets(text)
                with self.assertRaises(ConfigError) as raised:
                    read_environment(path, {})
                self.assertIn("linha", str(raised.exception))
                self.assertNotIn("PRIVATE-VALUE", str(raised.exception))

    def test_bad_file_does_not_partially_publish_credentials(self):
        path = self.secrets("SAMBINHA_NEW_TOKEN=PRIVATE-VALUE\ninvalid command\n")
        before = dict(os.environ)
        with self.assertRaises(ConfigError):
            read_environment(path)
        self.assertEqual(dict(os.environ), before)

    def test_missing_unreadable_directory_and_invalid_utf8(self):
        for path in (self.root / "missing", self.root):
            with self.subTest(path=path), self.assertRaises(ConfigError):
                read_environment(path, {})
        path = self.root / "secrets.env"
        path.write_bytes(b"TOKEN=PRIVATE-VALUE\xff")
        with self.assertRaises(ConfigError) as raised:
            read_environment(path, {})
        self.assertNotIn("PRIVATE-VALUE", str(raised.exception))
        with patch.object(Path, "open", side_effect=PermissionError("PRIVATE-VALUE")):
            with self.assertRaises(ConfigError) as raised:
                read_environment(path, {})
        self.assertNotIn("PRIVATE-VALUE", str(raised.exception))

    def test_blank_file_and_unknown_variable_expansion(self):
        self.assertEqual(read_environment(self.secrets("\n# comment\n"), {}), {})
        self.assertEqual(expand("${ROOT}/$NAME/$UNKNOWN", {"ROOT": "/tmp", "NAME": "a"}),
                         "/tmp/a/$UNKNOWN")
        self.assertEqual(expand("${TOKEN}", {"TOKEN": "literal$OTHER"}), "literal$OTHER")


class SecretsConfigurationTests(Sandbox):
    def setUp(self):
        super().setUp()
        # Credentials from the host are irrelevant to these deterministic tests.
        environment = patch.dict(os.environ, {}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)

    def toml(self, extra="", source="source", destination="destination"):
        path = self.root / "config.toml"
        path.write_text('[backup]\n' + extra + '\n[[jobs]]\nname="test"\n'
                        f'source="{source}"\ndestination="{destination}"\n')
        return path

    def secrets(self, text, name="secrets.env"):
        path = self.root / name
        path.write_text(text)
        return path

    def test_file_loaded_before_paths_and_cronitor_are_resolved(self):
        self.secrets('CRONITOR_URL=https://cronitor.link/p/test/monitor\n'
                     'ORIGIN=source\nDEST=destination\nSTATE=state\nLOGS=logs\n'
                     'RCLONE_FILE=rclone.conf\nAWS_PROFILE=sambinha\n')
        config = load(self.toml('secrets_file="secrets.env"\n'
            'cronitor_url="${CRONITOR_URL}"\nstate_dir="${STATE}"\n'
            'log_dir="$LOGS"\nrclone_config="${RCLONE_FILE}"', "${ORIGIN}", "$DEST"))
        self.assertEqual(config.jobs[0].source, self.source)
        self.assertEqual(config.jobs[0].destination, str(self.destination))
        self.assertEqual(config.state_dir, self.root / "state")
        self.assertEqual(config.log_dir, self.root / "logs")
        self.assertEqual(config.rclone_config, str(self.root / "rclone.conf"))
        self.assertEqual(config.cronitor_url, "https://cronitor.link/p/test/monitor")
        self.assertEqual(config.environment["AWS_PROFILE"], "sambinha")
        self.assertEqual(config.secrets_file, self.root / "secrets.env")
        self.assertNotIn("AWS_PROFILE", os.environ)

    def test_relative_file_is_resolved_from_toml_not_working_directory(self):
        self.secrets("AWS_PROFILE=sambinha\n")
        folder = self.root / "nested"
        folder.mkdir()
        config_file = folder / "config.toml"
        config_file.write_text('[backup]\nsecrets_file="../secrets.env"\n'
            '[[jobs]]\nname="test"\nsource="../source"\ndestination="../destination"\n')
        self.assertEqual(load(config_file).secrets_file, self.root / "secrets.env")

    def test_home_and_inherited_variable_paths(self):
        path = self.secrets("AWS_PROFILE=sambinha\n")
        with patch.dict(os.environ, {"PRIVATE_DIR": str(self.root)}):
            self.assertEqual(load(self.toml('secrets_file="${PRIVATE_DIR}/secrets.env"')).secrets_file, path)
        with patch("sambinha.config.read_environment", return_value={}) as reader:
            load(self.toml('secrets_file="~/.config/sambinha/secrets.env"'))
            self.assertEqual(reader.call_args.args[0], Path.home() / ".config/sambinha/secrets.env")

    def test_terminal_wins_and_explicit_toml_url_still_wins(self):
        self.secrets("AWS_PROFILE=file\nCRONITOR_URL=https://example.com/file\n")
        with patch.dict(os.environ, {"AWS_PROFILE": "terminal", "CRONITOR_URL": "https://example.com/terminal"}):
            config = load(self.toml('secrets_file="secrets.env"'))
            self.assertEqual(config.environment["AWS_PROFILE"], "terminal")
            self.assertEqual(config.cronitor_url, "https://example.com/terminal")
            explicit = load(self.toml('secrets_file="secrets.env"\ncronitor_url="https://example.com/config"'))
            self.assertEqual(explicit.cronitor_url, "https://example.com/config")

    def test_cli_override_selects_file_and_can_disable_loading(self):
        self.secrets("AWS_PROFILE=selected\n", "selected.env")
        path = self.toml('secrets_file="missing.env"')
        self.assertEqual(load(path, {"secrets_file": "selected.env"}).environment["AWS_PROFILE"], "selected")
        disabled = load(path, {"secrets_file": ""})
        self.assertIsNone(disabled.secrets_file)
        self.assertIsNone(disabled.environment)
        self.assertEqual(parser().parse_args(["--secrets-file", "selected.env"]).secrets_file, "selected.env")

    def test_omitted_and_empty_file_preserve_existing_configuration(self):
        for text in ("", 'secrets_file=""'):
            with self.subTest(text=text):
                config = load(self.toml(text))
                self.assertIsNone(config.secrets_file)
                self.assertIsNone(config.environment)

    def test_configurations_are_isolated_and_values_hidden_from_repr(self):
        self.secrets("PRIVATE_TOKEN=PRIVATE-VALUE-A\n", "a.env")
        self.secrets("PRIVATE_TOKEN=PRIVATE-VALUE-B\n", "b.env")
        a = load(self.toml('secrets_file="a.env"'))
        b = load(self.toml('secrets_file="b.env"'))
        self.assertEqual(a.environment["PRIVATE_TOKEN"], "PRIVATE-VALUE-A")
        self.assertEqual(b.environment["PRIVATE_TOKEN"], "PRIVATE-VALUE-B")
        self.assertNotIn("PRIVATE-VALUE", repr(a))
        self.assertNotIn("PRIVATE_TOKEN", os.environ)

    def test_invalid_config_after_secrets_does_not_leak_environment(self):
        self.secrets("PRIVATE_TOKEN=PRIVATE-VALUE\n")
        with self.assertRaises(ConfigError):
            load(self.toml('secrets_file="secrets.env"\ndeleted_days=-1'))
        self.assertNotIn("PRIVATE_TOKEN", os.environ)

    def test_invalid_file_stops_cli_before_backup_and_redacts_error(self):
        self.secrets("TOKEN=PRIVATE-VALUE\nbad command PRIVATE-VALUE\n")
        output = io.StringIO()
        with patch("sambinha.cli.run") as backup, redirect_stderr(output), redirect_stdout(output):
            self.assertEqual(main(["--config", str(self.toml('secrets_file="secrets.env"'))]), 2)
        backup.assert_not_called()
        self.assertIn("linha 2", output.getvalue())
        self.assertNotIn("PRIVATE-VALUE", output.getvalue())
        self.assertFalse(self.destination.exists())

    def test_missing_file_stops_simulation_too(self):
        with patch("sambinha.cli.run") as backup, redirect_stderr(io.StringIO()):
            self.assertEqual(main(["--config", str(self.toml('secrets_file="missing.env"')), "--dry-run"]), 2)
        backup.assert_not_called()

    def test_internal_environment_cannot_be_injected_from_toml(self):
        for extra in ('secrets_file=42', 'environment={PRIVATE_TOKEN="PRIVATE-VALUE"}'):
            with self.subTest(extra=extra), self.assertRaises(ConfigError):
                load(self.toml(extra))

    def test_credentials_reach_subprocess_without_source_or_value_logging(self):
        self.secrets("AWS_ACCESS_KEY_ID=test-key\nAWS_SECRET_ACCESS_KEY=test-secret\n")
        config = load(self.toml('secrets_file="secrets.env"'))
        runner = Runner(env=config.environment)
        result = runner.run([sys.executable, "-c", 'import os,json; print(json.dumps([os.environ["AWS_ACCESS_KEY_ID"],os.environ["AWS_SECRET_ACCESS_KEY"]]))'])
        self.assertEqual(json.loads(result), ["test-key", "test-secret"])
        self.assertNotIn("AWS_SECRET_ACCESS_KEY", os.environ)

    def test_dependency_lookup_uses_the_same_environment_as_child(self):
        runner = Runner(env={"PATH": "/private/bin"})
        with patch("shutil.which", return_value="/private/bin/rclone") as lookup:
            runner.require(["rclone"])
        lookup.assert_called_once_with("rclone", path="/private/bin")

    def test_command_environment_overrides_are_scoped_and_preserve_other_values(self):
        script = 'import os,json; print(json.dumps([os.environ["TZ"],os.environ["SAMBINHA_TEST_VALUE"]]))'
        for explicit in (False, True):
            for streaming in (False, True):
                with self.subTest(explicit=explicit, streaming=streaming), \
                     patch.dict(os.environ, {'TZ': 'America/Sao_Paulo', 'SAMBINHA_TEST_VALUE': 'inherited'}):
                    environment = {**os.environ, 'SAMBINHA_TEST_VALUE': 'configured'} if explicit else None
                    runner = Runner(env=environment)
                    before = dict(os.environ)
                    result = runner.run([sys.executable, '-c', script], env={'TZ': 'UTC'},
                                        on_output=(lambda record: None) if streaming else None)
                    output = runner.last_stderr if streaming else result
                    self.assertEqual(json.loads(output), ['UTC', 'configured' if explicit else 'inherited'])
                    self.assertEqual(dict(os.environ), before)
                    if explicit:
                        self.assertEqual(runner.env['TZ'], 'America/Sao_Paulo')

    def test_private_environment_without_path_uses_os_default_lookup(self):
        with patch.dict(os.environ, {'PATH': '/parent-only'}), patch('shutil.which', return_value='/bin/rclone') as lookup:
            Runner(env={}).require(['rclone'])
        lookup.assert_called_once_with('rclone', path=os.defpath)

    def test_backend_errors_redact_credentials_before_logging(self):
        self.secrets('AWS_ACCESS_KEY_ID=PRIVATE-ID\nAWS_SECRET_ACCESS_KEY=PRIVATE-SECRET\n'
                     'CRONITOR_URL=https://example.com/PRIVATE-TOKEN\n')
        config = load(self.toml('secrets_file="secrets.env"'))
        self.assertNotIn('PRIVATE-TOKEN', repr(config))
        runner = Runner(env=config.environment)
        failed = subprocess.CompletedProcess([], 1, '',
            'authentication: PRIVATE-ID PRIVATE-SECRET https://example.com/PRIVATE-TOKEN')
        with patch.object(Runner, '_run_captured', return_value=failed), self.assertRaises(BackupError) as raised:
            runner.run(['rclone'])
        self.assertNotIn('PRIVATE-', str(raised.exception))
        self.assertIn('[REDACTED]', str(raised.exception))

    def test_inherited_credentials_are_also_redacted_without_secrets_file(self):
        with patch.dict(os.environ, {'API_TOKEN': 'PRIVATE-TOKEN'}):
            self.assertEqual(Runner().safe_error('failure: PRIVATE-TOKEN'), 'failure: [REDACTED]')

    def test_short_credential_like_values_do_not_destroy_error_messages(self):
        environment = {'GPG_KEY_ID': '1', 'PASSWORD_STORE_DIR': '/', 'EMPTY_TOKEN': '',
                       'SHORT_KEY': 'abcde', 'API_TOKEN': 'PRIVATE-TOKEN'}
        message = Runner(env=environment).safe_error(
            'rclone: /home/user1/backup/part-1.7z: 401 abcde PRIVATE-TOKEN')
        self.assertEqual(message, 'rclone: /home/user1/backup/part-1.7z: 401 abcde [REDACTED]')
        with patch.dict(os.environ, {'GPG_KEY_ID': '1', 'API_TOKEN': 'SECRET-6'}):
            self.assertEqual(Runner().safe_error('path /tmp/1/x: SECRET-6'), 'path /tmp/1/x: [REDACTED]')
