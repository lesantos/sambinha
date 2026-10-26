from dataclasses import replace
from pathlib import Path

from sambinha.cli import main, parser
from sambinha.config import load
from sambinha.config_validation import validate
from sambinha.errors import ConfigError
from tests.helpers import Sandbox


class ConfigurationTests(Sandbox):
    def toml(self, extra="", jobs=None):
        path = self.root / "config.toml"
        path.write_text('[backup]\n' + extra + '\n' + (jobs or
            '[[jobs]]\nname="test"\nsource="source"\ndestination="destination"\n'))
        return path

    def test_defaults_and_relative_paths(self):
        config = load(self.toml())
        self.assertEqual(config.jobs[0].deleted_days, 10)
        self.assertEqual(config.jobs[0].version_days, 2)
        self.assertEqual(config.jobs[0].max_megabytes, 1024)
        self.assertEqual(config.jobs[0].max_bytes, 1024**3)
        self.assertEqual(config.jobs[0].compression_level, 3)
        self.assertEqual(config.jobs[0].source, self.source)
        self.assertEqual(config.log_dir, self.root / "logs")
        self.assertIsNone(config.temp_dir)
        self.assertEqual(config.jobs[0].log_retention_days, 14)
        self.assertTrue(config.jobs[0].continue_on_job_error)
        self.assertTrue(config.jobs[0].check_source_disk)
        self.assertTrue(config.jobs[0].check_destination_disk)

    def test_temp_dir_is_global_and_relative_to_config(self):
        config = load(self.toml('temp_dir="/mnt/fast/tmp"'))
        self.assertEqual(config.temp_dir, Path('/mnt/fast/tmp'))
        config = load(self.toml('temp_dir="./fast-tmp"'))
        self.assertEqual(config.temp_dir, self.root / 'fast-tmp')
        self.assertIsNone(load(self.toml('temp_dir=""')).temp_dir)

    def test_temp_dir_must_be_separate_from_other_configured_directories(self):
        with self.assertRaisesRegex(ConfigError, 'Diretório temporário'):
            validate(replace(self.config, temp_dir=self.config.log_dir))

    def test_temp_dir_rejects_job_scope_and_non_path_values(self):
        with self.assertRaisesRegex(ConfigError, "Parâmetros desconhecidos em \\[\\[jobs\\]\\]"):
            load(self.toml(jobs=('[[jobs]]\nname="test"\nsource="source"\n'
                                 'destination="destination"\ntemp_dir="/mnt/fast/tmp"\n')))
        for value in (False, 0, []):
            with self.subTest(value=value), self.assertRaisesRegex(ConfigError, 'temp_dir'):
                validate(replace(self.config, temp_dir=value))

    def test_disk_checks_and_failure_policy_load_from_toml(self):
        jobs = ('[[jobs]]\nname="test"\nsource="source"\ndestination="destination"\n'
                'check_source_disk=false\ncheck_destination_disk=false\ncontinue_on_job_error=false\n'
                '[[jobs]]\nname="second"\nsource="source"\ndestination="second"\n')
        config = load(self.toml(jobs=jobs))
        self.assertFalse(config.jobs[0].continue_on_job_error)
        self.assertTrue(config.jobs[1].continue_on_job_error)
        self.assertFalse(config.jobs[0].check_source_disk)
        self.assertFalse(config.jobs[0].check_destination_disk)
        self.assertTrue(config.jobs[1].check_source_disk)
        self.assertTrue(config.jobs[1].check_destination_disk)

    def test_disk_checks_and_failure_policy_require_booleans(self):
        for value in ('false', 0, 1, None):
            for key in ('check_source_disk', 'check_destination_disk', 'continue_on_job_error'):
                with self.subTest(key=key, value=value), self.assertRaisesRegex(ConfigError, key):
                    validate(replace(self.config, jobs=(replace(self.config.jobs[0], **{key: value}),)))

    def test_failure_policy_is_not_a_global_backup_option(self):
        with self.assertRaisesRegex(ConfigError, r'Parâmetros desconhecidos em \[backup\]'):
            load(self.toml('continue_on_job_error=true'))

    def test_override_only_supplied_values(self):
        jobs = ('[[jobs]]\nname="test"\nsource="source"\ndestination="destination"\n'
                'deleted_days=17\ndry_run=true\n')
        config = load(self.toml(jobs=jobs), {"version_days": 0, "dry_run": False, "max_megabytes": 100})
        self.assertEqual(config.jobs[0].deleted_days, 17)
        self.assertEqual(config.jobs[0].version_days, 0)
        self.assertFalse(config.jobs[0].dry_run)
        self.assertEqual(config.jobs[0].max_megabytes, 100)
        self.assertEqual(config.jobs[0].max_bytes, 100 * 1024 ** 2)

    def test_max_megabytes_loads_toml_and_cli_in_megabytes(self):
        prefix = '[[jobs]]\nname="test"\nsource="source"\ndestination="destination"\n'
        for value, expected_bytes in ((64, 64 * 1024 ** 2), (0.5, 512 * 1024),
                                      (0.1, 104857)):
            with self.subTest(value=value):
                config = load(self.toml(jobs=prefix + f'max_megabytes={value}\n'))
                self.assertEqual(config.jobs[0].max_megabytes, value)
                self.assertEqual(config.jobs[0].max_bytes, expected_bytes)
        path = self.toml(jobs=prefix + 'max_megabytes=64\n')
        overrides = vars(parser().parse_args(['--max-megabytes', '0.5', '--job', 'test']))
        overrides = {key: overrides[key] for key in ('max_megabytes', 'job')}
        config = load(path, overrides)
        self.assertEqual(config.jobs[0].max_bytes, 512 * 1024)

    def test_max_megabytes_rejects_invalid_values(self):
        for value in (0, -1, True, False, '64', None, float('nan'), float('inf'),
                      float('-inf'), 1e308, 0.5 / 1024 ** 2):
            with self.subTest(value=value), self.assertRaisesRegex(ConfigError, 'max_megabytes'):
                validate(replace(self.config, jobs=(replace(self.config.jobs[0], max_megabytes=value),)))
        config = load(self.toml(), {'max_megabytes': 1 / 1024 ** 2})
        self.assertEqual(config.jobs[0].max_bytes, 1)

    def test_max_bytes_is_no_longer_a_configuration_option(self):
        jobs = '[[jobs]]\nname="test"\nsource="source"\ndestination="destination"\nmax_bytes=1048576\n'
        with self.assertRaisesRegex(ConfigError, "'max_bytes' "):
            load(self.toml(jobs=jobs))
        with self.assertRaisesRegex(ConfigError, 'Override desconhecido'):
            load(self.toml(), {'max_bytes': 1048576})
        import io
        from contextlib import redirect_stderr
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser().parse_args(['--max-bytes', '1048576'])

    def test_metadata_directory_loads_relative_to_configuration_and_rejects_overlap(self):
        self.assertEqual(load(self.toml('metadata_dir="metadata"')).metadata_dir, self.root / 'metadata')
        self.assertIsNone(load(self.toml('metadata_dir=""')).metadata_dir)
        self.assertEqual(parser().parse_args(['verify-destination-metadata']).action, 'verify-destination-metadata')
        self.assertEqual(parser().parse_args(['verify-destination-integrity']).action, 'verify-destination-integrity')
        for value in ('source', 'destination', 'logs'):
            with self.subTest(value=value), self.assertRaises(ConfigError):
                load(self.toml(f'metadata_dir="{value}"'))
        for value in (False, 0, []):
            with self.subTest(value=value), self.assertRaises(ConfigError):
                load(self.toml(), {'metadata_dir': value})

    def test_max_delete_is_per_simple_job_and_cli_override_requires_selection(self):
        jobs = ('[[jobs]]\nname="test"\nsource="source"\ndestination="destination"\nmax_delete=7\n'
                '[[jobs]]\nname="second"\nsource="source"\ndestination="second"\nmax_delete=9\n')
        config_path = self.toml(jobs=jobs)
        config = load(config_path)
        self.assertEqual([job.max_delete for job in config.jobs], [7, 9])
        with self.assertRaisesRegex(ConfigError, '--job'):
            load(config_path, {'max_delete': 3})
        selected = load(config_path, {'job': 'second', 'max_delete': 3})
        self.assertEqual(selected.jobs[0].max_delete, 3)
        self.assertEqual(parser().parse_args(['--max-delete', '3']).max_delete, 3)

    def test_select_job(self):
        jobs = '[[jobs]]\nname="a"\nsource="source"\ndestination="a"\n[[jobs]]\nname="b"\nsource="source"\ndestination="b"\n'
        config = load(self.toml(jobs=jobs), {"job": "b", "logic": "batch"})
        self.assertEqual(config.jobs[0].name, "b")
        self.assertEqual(config.jobs[0].logic, "batch")
        with self.assertRaises(ConfigError):
            load(self.toml(jobs=jobs), {"destination": "other"})
        with self.assertRaises(ConfigError):
            load(self.toml(jobs=jobs), {"job": "missing"})

    def test_invalid_global_values(self):
        for values in ({"deleted_days": -1}, {"version_days": True}, {"timeout": 0},
                       {"log_retention_days": -1}, {"log_retention_days": True},
                       {"log_retention_days": 1.5}, {"log_retention_days": "14"},
                       {"dry_run": "yes"}, {"cronitor_url": "http://example.com"},
                       {"rclone": ""}, {"rclone_config": 123}, {"secrets_file": "wrong-type"}, {"deletion_alert_threshold": -1}, {"deletion_alert_threshold": True}):
            with self.subTest(values=values), self.assertRaises(ConfigError):
                validate(replace(self.config, **values))

    def test_log_retention_toml_cli_and_zero_disables_cleanup(self):
        jobs = '[[jobs]]\nname="test"\nsource="source"\ndestination="destination"\nlog_retention_days=30\n'
        config = load(self.toml(jobs=jobs))
        self.assertEqual(config.jobs[0].log_retention_days, 30)
        arguments = vars(parser().parse_args(['--log-retention-days', '0']))
        self.assertEqual(load(self.toml(jobs=jobs),
                              {'log_retention_days': arguments['log_retention_days']}).jobs[0].log_retention_days, 0)

    def test_invalid_job_values(self):
        for values in ({"name": "../bad"}, {"type": "ftp"}, {"logic": "unknown"},
                       {"storage_class": "wrong"}, {"max_megabytes": 0}, {"individual_batch_percent": 0},
                       {"compressor": ""}, {"archive_format": "rar"}, {"destination": ""},
                       {"max_delete": -1}, {"max_delete": True},
                       {"compression_level": 10},
                       {"compression_threads": 65}):
            with self.subTest(values=values), self.assertRaises(ConfigError):
                validate(replace(self.config, jobs=(replace(self.config.jobs[0], **values),)))
        batch = replace(self.config.jobs[0], logic='batch')
        for values in ({"max_delete_percent": 101}, {"max_delete_percent": float('nan')},
                       {"deletion_min_files": 0}):
            with self.subTest(values=values), self.assertRaises(ConfigError):
                validate(replace(self.config, jobs=(replace(batch, **values),)))

    def test_invalid_new_global_values_and_cli_actions(self):
        for values in ({'verify_interval_days': -1}, {'fingerprint_cache': 'yes'},
                       {'verify_source_changes': 'yes'},
                       {'fingerprint_full_scan_days': 0},
                       {'transfer_workers': True}, {'batch_workers': 0}):
            with self.subTest(values=values), self.assertRaises(ConfigError):
                validate(replace(self.config, **values))
        self.assertEqual(parser().parse_args(['restore', '--job', 'test', '--output', '/tmp/restored']).action, 'restore')
        self.assertEqual(parser().parse_args(['verify-local-metadata']).action, 'verify-local-metadata')
        self.assertFalse(parser().parse_args(['--no-fingerprint-cache']).fingerprint_cache)

    def test_destination_and_internal_overlap(self):
        for dest in (self.source, self.source / "backup", self.root, self.config.state_dir):
            with self.subTest(dest=dest), self.assertRaises(ConfigError):
                validate(replace(self.config, jobs=(replace(self.config.jobs[0], destination=str(dest)),)))
        with self.assertRaises(ConfigError):
            validate(replace(self.config, state_dir=self.source / "state"))
        with self.assertRaises(ConfigError):
            validate(replace(self.config, log_dir=self.config.state_dir / "logs"))

    def test_separate_jobs_same_source(self):
        first = self.config.jobs[0]
        second = replace(first, name="batch", logic="batch", destination=str(self.root / "second"))
        self.assertEqual(len(validate(replace(self.config, jobs=(first, second))).jobs), 2)
        for destination in (first.destination, first.destination + "/nested"):
            with self.subTest(destination=destination), self.assertRaisesRegex(ConfigError, "se sobrepõem"):
                validate(replace(self.config, jobs=(first, replace(second, destination=destination))))
        with self.assertRaises(ConfigError):
            validate(replace(self.config, jobs=(first, replace(second, name=first.name))))

    def test_s3_validation(self):
        job = replace(self.config.jobs[0], type="s3", destination="aws:bucket/prefix")
        self.assertEqual(validate(replace(self.config, jobs=(job,))).jobs[0].storage_class, "GLACIER_IR")
        for changes in ({"destination": "/local"}, {"storage_class": "DEEP_ARCHIVE"}):
            with self.subTest(changes=changes), self.assertRaises(ConfigError):
                validate(replace(self.config, jobs=(replace(job, **changes),)))
        validate(replace(self.config, jobs=(replace(job, logic="batch", storage_class="DEEP_ARCHIVE"),)))

    def test_jobs_can_write_into_other_sources_and_local_or_s3_destinations(self):
        first = self.config.jobs[0]
        second = replace(first, name='second', source=self.root / 'other-source', destination=str(first.source))
        validate(replace(self.config, jobs=(first, second)))
        first = replace(first, type='s3', destination='aws:bucket/prefix')
        for destination in (first.destination, first.destination + '/nested'):
            with self.subTest(destination=destination), self.assertRaisesRegex(ConfigError, "se sobrepõem"):
                validate(replace(self.config, jobs=(first, replace(first, name='second', destination=destination))))
        other = replace(first, name='other', destination='aws:bucket/other')
        validate(replace(self.config, jobs=(first, other)))

    def test_unknown_options_and_malformed_toml(self):
        for text in ('[backup]\nunknown=1', '[backup', 'jobs="bad"',
                     '[[jobs]]\nname="x"', '[other]\nx=1'):
            with self.subTest(text=text), self.assertRaises(ConfigError):
                path = self.root / "invalid.toml"
                path.write_text(text)
                load(path)

    def test_unknown_job_parameters_include_job_key_and_source_line(self):
        path = self.toml(jobs=(
            '[[jobs]]\nname="first"\nsource="source"\ndestination="first"\n'
            '# max_file=123\n'
            '[[jobs]]\nname="second"\nsource="source"\ndestination="second"\n'
            'max_file=123\nunknown=true\n'))
        with self.assertRaises(ConfigError) as raised:
            load(path, {"job": "first"})
        message = str(raised.exception)
        self.assertIn("job 'second'", message)
        self.assertIn("'max_file' (linha 12)", message)
        self.assertIn("'unknown' (linha 13)", message)
        self.assertIn(str(path), message)

    def test_unknown_global_parameters_include_key_and_source_line(self):
        path = self.toml('timeot=30\n"other option"=false')
        with self.assertRaises(ConfigError) as raised:
            load(path)
        message = str(raised.exception)
        self.assertIn("[backup]", message)
        self.assertIn("'timeot' (linha 2)", message)
        self.assertIn("'other option' (linha 3)", message)
        self.assertIn(str(path), message)

    def test_unknown_parameter_without_job_name(self):
        with self.assertRaises(ConfigError) as raised:
            load(self.toml(jobs='[[jobs]]\nunknown=1\n'))
        self.assertIn("job '#1 (sem nome)'", str(raised.exception))
        self.assertIn("'unknown' (linha 4)", str(raised.exception))

    def test_unknown_parameter_lines_with_toml_strings_and_tables(self):
        prefix = ('[[jobs]]\nname="test"\nsource="source"\n'
                  'destination="""destination\n[[jobs]]\nunknown = 99\n"""\n')
        for declaration, parameter in (
                ('"unk\\u006eown" = [\n 1, 2,\n]', 'unknown'),
                ("'unknown' = { value = 1 }", 'unknown'),
                ('unknown.child = 1', 'unknown'),
                ('[jobs.unknown]\nchild=1', 'unknown')):
            with self.subTest(declaration=declaration), self.assertRaises(ConfigError) as raised:
                load(self.toml(jobs=prefix + declaration + '\n'))
            self.assertIn("job 'test'", str(raised.exception))
            self.assertIn(f"{parameter!r} (linha 10)", str(raised.exception))

    def test_unknown_parameter_lines_with_inline_jobs(self):
        path = self.root / "config.toml"
        path.write_text('jobs = [\n'
                        '{ name="first", source="source", destination="first" },\n'
                        '{ name="second", source="source", destination="second", unknown=1 },\n'
                        ']\n')
        with self.assertRaises(ConfigError) as raised:
            load(path)
        self.assertIn("job 'second'", str(raised.exception))
        self.assertIn("'unknown' (linha 3)", str(raised.exception))

    def test_cli_missing_values_inherit_config(self):
        args = parser().parse_args([])
        self.assertEqual(args.config, Path("sambinha.toml"))
        self.assertEqual(parser().prog, "sambinha")
        self.assertIsNone(args.dry_run)
        self.assertIsNone(args.max_megabytes)
        self.assertFalse(parser().parse_args(["--no-dry-run"]).dry_run)

    def test_cli_invalid_config_exit_code(self):
        import io
        from contextlib import redirect_stderr
        with redirect_stderr(io.StringIO()):
            self.assertEqual(main(["--config", str(self.root / "missing")]), 2)
