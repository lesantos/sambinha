from dataclasses import replace
import importlib.util
import io
import json
import os
from pathlib import Path
import tarfile
import unittest

from sambinha.engine import DAY
from tests.helpers import REAL_TOOLS, Sandbox, contents, restore_batches
from tests.s3_support import MotoRunner

MOTO_AVAILABLE = importlib.util.find_spec('moto') is not None


@unittest.skipUnless(MOTO_AVAILABLE and REAL_TOOLS, 'Install requirements-test.txt, rclone and 7z')
class S3IntegrationTests(Sandbox):
    def setUp(self):
        super().setUp()
        from moto import mock_aws
        import boto3
        emulator = mock_aws()
        emulator.start()
        self.addCleanup(emulator.stop)
        self.client = boto3.client('s3', region_name='us-east-1',
                                  aws_access_key_id='testing', aws_secret_access_key='testing')
        self.prepare_s3()
        self.runner = MotoRunner(self.client)

    def prepare_s3(self):
        self.client.create_bucket(Bucket='sambinha-tests')
        job = replace(self.config.jobs[0], type='s3', destination='aws:sambinha-tests/backup',
                      max_bytes=100 * 1024, max_files=3)
        self.config = replace(self.config, jobs=(job,))

    def execute(self, *args, **kwargs):
        kwargs['runner'] = self.runner
        return super().execute(*args, **kwargs)

    def fixture(self):
        self.file('root.txt', b'root')
        self.file('tree/a.txt', b'original a')
        self.file('tree/b.txt', bytes(range(256)) * 100)
        self.file('tree/sub/ação.txt', 'Texto com acentos'.encode())
        self.file('tree/sub/empty', b'')
        self.file('other/@ file.txt', b'file with @ and spaces')

    def download(self):
        import uuid
        directory = self.root / ('download-' + uuid.uuid4().hex)
        directory.mkdir()
        for page in self.client.get_paginator('list_objects_v2').paginate(Bucket='sambinha-tests', Prefix='backup/'):
            for item in page.get('Contents', []):
                key = item['Key']
                head = self.client.head_object(Bucket='sambinha-tests', Key=key)
                if head.get('StorageClass') in {'GLACIER', 'DEEP_ARCHIVE'} and 'Restore' not in head:
                    self.client.restore_object(Bucket='sambinha-tests', Key=key, RestoreRequest={'Days': 1})
                payload = self.client.get_object(Bucket='sambinha-tests', Key=key)['Body'].read()
                target = directory / key.removeprefix('backup/')
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(payload)
        return directory

    def assert_complete(self):
        download = self.download()
        if self.config.jobs[0].logic == 'simple':
            self.assertEqual(contents(download), contents(self.source))
        else:
            actual = restore_batches(self.state(), download, download / '.sambinha/restored')
            self.assertEqual(actual, contents(self.source))
            with tarfile.open(download / '.sambinha/control.tar.gz') as tar:
                recovered = json.load(tar.extractfile('state.json'))
                self.assertEqual(recovered, self.state())
                self.assertTrue(all('controls/' + key + '.json' in tar.getnames() for key in recovered['active']))

    def use_batch(self, storage_class='GLACIER_IR'):
        self.config = replace(self.config, jobs=(replace(self.config.jobs[0], logic='batch', storage_class=storage_class),))

    def test_simple_full_roundtrip_and_default_storage(self):
        self.fixture()
        self.assertEqual(self.execute(), 0, self.output.getvalue())
        self.assert_complete()
        for path in contents(self.source):
            head = self.client.head_object(Bucket='sambinha-tests', Key='backup/' + path)
            self.assertEqual(head.get('StorageClass'), 'GLACIER_IR')

    def test_batch_full_roundtrip_and_controls_in_standard(self):
        self.fixture()
        self.use_batch()
        self.assertEqual(self.execute(), 0, self.output.getvalue())
        self.assert_complete()
        for key in self.state()['active']:
            archive = self.client.head_object(Bucket='sambinha-tests', Key='backup/' + key)
            control = self.client.head_object(Bucket='sambinha-tests', Key='backup/' + key + '.json')
            self.assertEqual(archive.get('StorageClass'), 'GLACIER_IR')
            self.assertEqual(control.get('StorageClass', 'STANDARD'), 'STANDARD')
        index = self.client.head_object(Bucket='sambinha-tests', Key='backup/.sambinha/control.tar.gz')
        self.assertEqual(index.get('StorageClass', 'STANDARD'), 'STANDARD')

    def test_simple_edit_delete_add_and_retention_integrity(self):
        self.fixture()
        self.assertEqual(self.execute(), 0)
        old = (self.source / 'tree/a.txt').read_bytes()
        (self.source / 'tree/a.txt').write_bytes(b'new text a')
        (self.source / 'tree/sub/empty').unlink()
        self.file('new/new.dat', b'new')
        self.assertEqual(self.execute(now=2000), 0, self.output.getvalue())
        self.assert_complete()
        histories = self.state()['retired']
        self.assertEqual({r['reason'] for r in histories}, {'deleted', 'version'})
        old_version = next(r['key'] for r in histories if r['reason'] == 'version')
        self.assertEqual(self.client.get_object(Bucket='sambinha-tests', Key='backup/' + old_version)['Body'].read(), old)
        self.assertEqual(self.execute(now=2000 + 2 * DAY), 0)
        self.assertEqual([r['reason'] for r in self.state()['retired']], ['deleted'])
        self.assert_complete()
        self.assertEqual(self.execute(now=2000 + 10 * DAY), 0)
        self.assertEqual(self.state()['retired'], [])
        self.assert_complete()

    def test_batch_edit_delete_add_and_retention_integrity(self):
        self.fixture()
        self.use_batch()
        self.assertEqual(self.execute(), 0)
        old = self.state()
        (self.source / 'tree/a.txt').write_bytes(b'edited')
        (self.source / 'tree/sub/empty').unlink()
        self.file('new/new.dat', b'new')
        self.assertEqual(self.execute(now=2000), 0, self.output.getvalue())
        self.assert_complete()
        download = self.download()
        previous = restore_batches(old, download, download / '.sambinha/previous')
        self.assertEqual(previous['tree/a.txt'], b'original a')
        self.assertIn('tree/sub/empty', previous)
        self.assertEqual(self.execute(now=2000 + 10 * DAY), 0)
        self.assertEqual(self.state()['retired'], [])
        self.assert_complete()

    def test_all_archival_storage_classes_roundtrip(self):
        for storage_class in ('STANDARD', 'GLACIER_IR', 'GLACIER', 'DEEP_ARCHIVE'):
            with self.subTest(storage_class=storage_class):
                self.file('a', b'first')
                self.file('b', b'second')
                self.use_batch(storage_class)
                # Distinct jobs/prefixes make each storage-class test independent.
                self.config = replace(self.config, state_dir=self.root / ('state-' + storage_class))
                self.file('a', storage_class.encode())
                self.assertEqual(self.execute(), 0, self.output.getvalue())
                self.assert_complete()

    def test_s3_same_size_edit_with_preserved_timestamp(self):
        target = self.file('file', b'abc')
        self.assertEqual(self.execute(), 0)
        mtime = target.stat().st_mtime_ns
        target.write_bytes(b'xyz')
        os.utime(target, ns=(mtime, mtime))
        self.assertEqual(self.execute(), 0)
        self.assert_complete()

    def test_s3_identical_simple_and_batch_skip_transfers(self):
        for logic in ('simple', 'batch'):
            with self.subTest(logic=logic):
                self.file('a')
                self.file('b')
                self.config = replace(self.config, jobs=(replace(self.config.jobs[0], logic=logic,
                    destination='aws:sambinha-tests/' + logic),), state_dir=self.root / ('state-' + logic))
                self.assertEqual(self.execute(), 0)
                self.assertEqual(self.execute(), 0)
                self.assertFalse(any(e['event'] == 'transferred' for e in self.events))
                self.assertEqual(sum(e['event'] == 'identical' for e in self.events), 2)

    def test_s3_dry_run_leaves_no_objects_and_no_state(self):
        self.fixture()
        for logic in ('simple', 'batch'):
            with self.subTest(logic=logic):
                config = replace(self.config, dry_run=True, jobs=(replace(self.config.jobs[0], logic=logic),))
                self.assertEqual(self.execute(config), 0)
                self.assertEqual(self.client.list_objects_v2(Bucket='sambinha-tests').get('KeyCount'), 0)
                self.assertFalse(self.config.state_dir.exists())

    def test_s3_oversized_file_and_filename_collisions_integrity(self):
        self.file('folder/big.txt', bytes(range(256)) * 400)
        self.file('folder/big.csv', bytes(range(256)) * 400)
        self.use_batch()
        self.config = replace(self.config, jobs=(replace(self.config.jobs[0], max_bytes=32 * 1024),))
        self.assertEqual(self.execute(), 0, self.output.getvalue())
        self.assert_complete()
        self.assertEqual(len(self.state()['active']), 2)

    def test_s3_empty_source(self):
        for logic in ('simple', 'batch'):
            with self.subTest(logic=logic):
                self.config = replace(self.config, jobs=(replace(self.config.jobs[0], logic=logic),),
                                      state_dir=self.root / ("state-" + logic))
                self.assertEqual(self.execute(), 0)
                self.assert_complete()

    def test_s3_newer_destination_is_preserved_and_notified(self):
        source = self.file('file', b'old source')
        self.assertEqual(self.execute(), 0)
        ns = source.stat().st_mtime_ns + 1000000000
        self.client.put_object(Bucket='sambinha-tests', Key='backup/file', Body=b'new destination',
            Metadata={'mtime': f'{ns // 1000000000}.{ns % 1000000000:09d}'})
        self.assertEqual(self.execute(), 1, self.output.getvalue())
        result = self.client.get_object(Bucket='sambinha-tests', Key='backup/file')['Body'].read()
        self.assertEqual(result, b'new destination')
        self.assertEqual(self.notifications[-1][1], 'fail')
        self.assertIn('file', self.notifications[-1][3])

    def test_s3_batch_older_source_preserves_active_index(self):
        source = self.file('file', b'confirmed')
        self.file('other')
        self.use_batch()
        self.assertEqual(self.execute(), 0)
        before = self.state()
        ns = source.stat().st_mtime_ns - 1000000000
        source.write_bytes(b'old restored source')
        os.utime(source, ns=(ns, ns))
        self.assertEqual(self.execute(), 1)
        self.assertEqual(self.state(), before)
        self.assertEqual(self.notifications[-1][1], 'fail')
