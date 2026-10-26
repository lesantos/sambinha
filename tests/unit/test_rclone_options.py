"""Optional S3 tuning flags: fast_list, s3_chunk_size and s3_upload_concurrency."""
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

from sambinha import rclone
from sambinha.config import Config, Job, load
from sambinha.config_validation import validate
from sambinha.errors import ConfigError

TUNING = {'fast_list': True, 's3_chunk_size': '64M', 's3_upload_concurrency': 8}


def arguments(job, operation):
    runner = Mock()
    rclone.command(Config(jobs=(job,)), job, runner, operation, 'a', 'b')
    return runner.run.call_args.args[0]


class RcloneOptionFlagTests(unittest.TestCase):
    def job(self, **kwargs):
        return Job('j', Path('/src'), kwargs.pop('destination', 's3:bucket/path'),
                   type=kwargs.pop('type', 's3'), **kwargs)

    def test_unset_options_add_no_flags(self):
        for logic in ('simple', 'batch'):
            for operation in ('sync', 'check', 'lsjson', 'copyto'):
                with self.subTest(logic=logic, operation=operation):
                    args = arguments(self.job(logic=logic), operation)
                    self.assertFalse({'--fast-list', '--s3-chunk-size', '--s3-upload-concurrency'} & set(args))

    def test_fast_list_only_for_simple_s3_listing_comparison_and_sync(self):
        job = self.job(logic='simple', fast_list=True)
        for operation in ('sync', 'check', 'lsjson'):
            self.assertIn('--fast-list', arguments(job, operation))
        for operation in ('copyto', 'delete', 'purge'):
            self.assertNotIn('--fast-list', arguments(job, operation))
        self.assertNotIn('--fast-list', arguments(self.job(logic='batch', fast_list=True), 'lsjson'))

    def test_chunk_size_and_concurrency_follow_uploads_only(self):
        job = self.job(logic='batch', s3_chunk_size='64M', s3_upload_concurrency=8)
        for operation in ('copyto', 'copy', 'sync'):
            args = arguments(job, operation)
            self.assertEqual(args[args.index('--s3-chunk-size') + 1], '64M')
            self.assertEqual(args[args.index('--s3-upload-concurrency') + 1], '8')
        for operation in ('lsjson', 'check', 'delete'):
            self.assertNotIn('--s3-chunk-size', arguments(job, operation))
        simple = arguments(replace(job, logic='simple'), 'sync')
        self.assertIn('--s3-chunk-size', simple)

    def test_each_option_is_independent(self):
        args = arguments(self.job(logic='batch', s3_upload_concurrency=4), 'copyto')
        self.assertIn('--s3-upload-concurrency', args)
        self.assertNotIn('--s3-chunk-size', args)

    def test_local_destinations_never_receive_the_flags(self):
        for logic in ('simple', 'batch'):
            job = self.job(type='local', destination='/dst', logic=logic, **TUNING)
            for operation in ('sync', 'check', 'lsjson', 'copyto', 'copy'):
                with self.subTest(logic=logic, operation=operation):
                    args = arguments(job, operation)
                    self.assertFalse({'--fast-list', '--s3-chunk-size', '--s3-upload-concurrency'} & set(args))


class RcloneOptionValidationTests(unittest.TestCase):
    def check(self, **kwargs):
        return validate(Config(jobs=(Job('j', Path('/src'), 's3:bucket/p', type='s3', **kwargs),)))

    def test_valid_values_are_accepted(self):
        for size in ('5M', '64M', '16Mi', '1G', '512K'):
            self.check(s3_chunk_size=size)
        for count in (1, 8, 64):
            self.check(s3_upload_concurrency=count)
        self.check(fast_list=True, logic='simple')

    def test_invalid_values_are_rejected_in_portuguese(self):
        cases = [({'fast_list': 'yes'}, 'fast_list'), ({'fast_list': 1}, 'fast_list'),
                 ({'s3_chunk_size': '64'}, 's3_chunk_size'), ({'s3_chunk_size': '0M'}, 's3_chunk_size'),
                 ({'s3_chunk_size': '-1M'}, 's3_chunk_size'), ({'s3_chunk_size': '64 M'}, 's3_chunk_size'),
                 ({'s3_chunk_size': 64}, 's3_chunk_size'), ({'s3_chunk_size': '64M --x'}, 's3_chunk_size'),
                 ({'s3_upload_concurrency': 0}, 's3_upload_concurrency'),
                 ({'s3_upload_concurrency': 65}, 's3_upload_concurrency'),
                 ({'s3_upload_concurrency': True}, 's3_upload_concurrency'),
                 ({'s3_upload_concurrency': '4'}, 's3_upload_concurrency')]
        for kwargs, name in cases:
            with self.subTest(kwargs=kwargs), self.assertRaisesRegex(ConfigError, name):
                self.check(**kwargs)

    def test_toml_job_keys_are_read(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'src').mkdir()
            path = root / 's.toml'
            path.write_text(f'''[backup]
[[jobs]]
name = "j"
source = "{root / 'src'}"
destination = "s3:bucket/p"
type = "s3"
logic = "simple"
fast_list = true
s3_chunk_size = "64M"
s3_upload_concurrency = 8
''')
            job = load(path).jobs[0]
        self.assertEqual((job.fast_list, job.s3_chunk_size, job.s3_upload_concurrency), (True, '64M', 8))


if __name__ == '__main__':
    unittest.main()
