from dataclasses import replace
from pathlib import Path

from sambinha.cli import parser, main
from sambinha.config import Job, load, validate
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
        self.assertEqual(config.deleted_days, 10)
        self.assertEqual(config.version_days, 2)
        self.assertEqual(config.jobs[0].max_bytes, 1024**3)
        self.assertEqual(config.jobs[0].max_files, 1000)
        self.assertEqual(config.jobs[0].source, self.source)
        self.assertEqual(config.log_dir, self.root / "logs")

    def test_override_only_supplied_values(self):
        config = load(self.toml('deleted_days=17\ndry_run=true'), {"version_days": 0, "dry_run": False, "max_bytes": 100000})
        self.assertEqual(config.deleted_days, 17)
        self.assertEqual(config.version_days, 0)
        self.assertFalse(config.dry_run)
        self.assertEqual(config.jobs[0].max_bytes, 100000)

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
                       {"dry_run": "yes"}, {"cronitor_url": "http://example.com"},
                       {"rclone": ""}, {"rclone_config": 123}, {"secrets_file": "wrong-type"}):
            with self.subTest(values=values), self.assertRaises(ConfigError):
                validate(replace(self.config, **values))

    def test_invalid_job_values(self):
        for values in ({"name": "../bad"}, {"type": "ftp"}, {"logic": "unknown"},
                       {"storage_class": "wrong"}, {"max_bytes": 0}, {"max_files": 1},
                       {"compressor": ""}, {"archive_format": "rar"}, {"destination": ""}):
            with self.subTest(values=values), self.assertRaises(ConfigError):
                validate(replace(self.config, jobs=(replace(self.config.jobs[0], **values),)))

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
        with self.assertRaises(ConfigError):
            validate(replace(self.config, jobs=(first, replace(second, destination=first.destination + "/nested"))))
        with self.assertRaises(ConfigError):
            validate(replace(self.config, jobs=(first, replace(second, name=first.name))))

    def test_s3_validation(self):
        job = replace(self.config.jobs[0], type="s3", destination="aws:bucket/prefix")
        self.assertEqual(validate(replace(self.config, jobs=(job,))).jobs[0].storage_class, "GLACIER_IR")
        for changes in ({"destination": "/local"}, {"storage_class": "DEEP_ARCHIVE"}):
            with self.subTest(changes=changes), self.assertRaises(ConfigError):
                validate(replace(self.config, jobs=(replace(job, **changes),)))
        validate(replace(self.config, jobs=(replace(job, logic="batch", storage_class="DEEP_ARCHIVE"),)))

    def test_unknown_options_and_malformed_toml(self):
        for text in ('[backup]\nunknown=1', '[backup', 'jobs="bad"',
                     '[[jobs]]\nname="x"', '[other]\nx=1'):
            with self.subTest(text=text), self.assertRaises(ConfigError):
                path = self.root / "invalid.toml"
                path.write_text(text)
                load(path)

    def test_cli_missing_values_inherit_config(self):
        args = parser().parse_args([])
        self.assertEqual(args.config, Path("sambinha.toml"))
        self.assertEqual(parser().prog, "sambinha")
        self.assertIsNone(args.dry_run)
        self.assertIsNone(args.max_bytes)
        self.assertFalse(parser().parse_args(["--no-dry-run"]).dry_run)

    def test_cli_invalid_config_exit_code(self):
        from contextlib import redirect_stderr
        import io
        with redirect_stderr(io.StringIO()):
            self.assertEqual(main(["--config", str(self.root / "missing")]), 2)
