from dataclasses import replace
import importlib.util
import io
import json
import os
from pathlib import Path
import tarfile
import unittest
from unittest.mock import patch

from sambinha.engine import DAY
from sambinha.config import validate
from sambinha.errors import BackupError, ConfigError
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
                      max_bytes=100 * 1024)
        self.config = replace(self.config, jobs=(job,))

    def test_overlapping_job_destinations_are_rejected(self):
        self.file('first', b'first')
        source = self.root / 'second-source'
        source.mkdir()
        (source / 'second').write_bytes(b'second')
        first = self.config.jobs[0]
        rules = self.root / 'second-rules.txt'
        rules.write_text('first\n')
        for suffix in ('', '/nested'):
            with self.subTest(suffix=suffix):
                second = replace(first, name='second', source=source, destination=first.destination + suffix,
                                 exclude_files=rules)
                config = replace(self.config, jobs=(first, second), verify_interval_days=1,
                                 state_dir=self.root / ('nested-state' if suffix else 'shared-state'))
                with self.assertRaisesRegex(ConfigError, 'se sobrepõem'):
                    validate(config)

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

    def test_s3_simple_restore_selected_directory_and_file(self):
        self.fixture()
        self.assertEqual(self.execute(), 0, self.output.getvalue())
        from sambinha.recovery import recover
        output = self.root / 'selected-restore'
        self.assertEqual(recover(self.config, 'restore', output=output,
                                 selectors=('tree', 'root.txt'), runner=self.runner,
                                 notifier=lambda *args: None), 0)
        self.assertEqual(contents(output), {
            'tree/a.txt': b'original a', 'tree/b.txt': bytes(range(256)) * 100,
            'tree/sub/ação.txt': 'Texto com acentos'.encode(),
            'tree/sub/empty': b'', 'root.txt': b'root',
        })

    def test_metadata_mode_roundtrip_reuse_and_timestamp_change(self):
        self.fixture()
        self.config = replace(self.config, verification_mode='simple')
        with patch('sambinha.inventory.fingerprint', side_effect=AssertionError('source hash read')):
            self.assertEqual(self.execute(), 0, self.output.getvalue())
            self.assertEqual(self.execute(now=1001), 0, self.output.getvalue())
            path = self.source / 'root.txt'
            stamp = path.stat().st_mtime_ns
            path.write_bytes(b'edit')
            os.utime(path, ns=(stamp + 1, stamp + 1))
            self.assertEqual(self.execute(now=1002), 0, self.output.getvalue())
        self.assertFalse(any(e['event'] == 'transferred' for e in self.events))
        self.assert_complete()
        from sambinha.recovery import verify_local_metadata_job
        from unittest.mock import Mock
        with patch('sambinha.storage.Storage.get', side_effect=AssertionError('download')):
            result = verify_local_metadata_job(self.config, self.config.jobs[0], self.runner, Mock())
        self.assertEqual(result['mode'], 'rclone_check')

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
        histories = list(self.client.get_paginator('list_objects_v2').paginate(
            Bucket='sambinha-tests', Prefix='backup/.sambinha/history/'))
        keys = {item['Key'] for page in histories for item in page.get('Contents', [])}
        old_key = next(key for key in keys if key.endswith('/tree/a.txt'))
        self.assertEqual(self.client.get_object(Bucket='sambinha-tests', Key=old_key)['Body'].read(), old)
        self.assertEqual(self.execute(now=2000 + 2 * DAY), 0)
        self.assertTrue(self.client.get_object(Bucket='sambinha-tests', Key=old_key))
        self.assert_complete()
        self.assertEqual(self.execute(now=2000 + 10 * DAY), 0)
        expired = self.client.list_objects_v2(Bucket='sambinha-tests', Prefix='backup/.sambinha/history/')
        self.assertEqual(expired['KeyCount'], 0)
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

    def test_batch_json_replicas_stay_standard_and_update_together(self):
        from unittest.mock import Mock
        from sambinha.metadata import control_directory
        from sambinha.storage import Storage
        from sambinha.verification import verify_local_metadata_job, verify_destination_metadata_job
        self.file('a.txt', b'alpha')
        self.file('b.txt', b'beta')
        self.use_batch('DEEP_ARCHIVE')
        self.config = replace(self.config, metadata_dir=self.root / 'metadata')
        for now in (1000, 2000):
            if now == 2000:
                (self.source / 'a.txt').write_bytes(b'new content')
            self.assertEqual(self.execute(now=now), 0, self.output.getvalue())
            root = control_directory(self.config, self.config.jobs[0])
            for key in self.state()['active']:
                head = self.client.head_object(Bucket='sambinha-tests', Key='backup/' + key)
                self.assertEqual(head['StorageClass'], 'DEEP_ARCHIVE')
                control_key = 'backup/' + key + '.json'
                head = self.client.head_object(Bucket='sambinha-tests', Key=control_key)
                self.assertEqual(head.get('StorageClass', 'STANDARD'), 'STANDARD')
                remote = self.client.get_object(Bucket='sambinha-tests', Key=control_key)['Body'].read()
                self.assertEqual((root / (key + '.json')).read_bytes(), remote)
            index = self.client.head_object(Bucket='sambinha-tests', Key='backup/.sambinha/control.tar.gz')
            self.assertEqual(index.get('StorageClass', 'STANDARD'), 'STANDARD')
        with patch.object(Storage, 'get', side_effect=AssertionError('daily download')), \
             patch.object(Storage, 'stat', side_effect=AssertionError('daily stat')):
            self.assertFalse(verify_local_metadata_job(self.config, self.config.jobs[0], self.runner, Mock())['needs_update'])
        get = Storage.get
        def metadata_only(storage, key, target):
            self.assertTrue(key.endswith('.json') or key == '.sambinha/control.tar.gz')
            return get(storage, key, target)
        with patch.object(Storage, 'get', metadata_only):
            self.assertGreater(verify_destination_metadata_job(self.config, self.config.jobs[0], self.runner, Mock())['batches'], 0)

    def test_s3_same_size_edit_with_preserved_timestamp(self):
        target = self.file('file', b'abc')
        self.assertEqual(self.execute(), 0)
        mtime = target.stat().st_mtime_ns
        target.write_bytes(b'xyz')
        os.utime(target, ns=(mtime, mtime))
        self.assertEqual(self.execute(), 0)
        self.assert_complete()

    def test_failed_s3_json_publication_keeps_old_generation_and_retry_updates_both_copies(self):
        from sambinha.metadata import control_directory
        from sambinha.storage import Storage
        self.file('file.txt', b'old')
        self.use_batch('DEEP_ARCHIVE')
        self.config = replace(self.config, metadata_dir=self.root / 'metadata')
        self.assertEqual(self.execute(), 0, self.output.getvalue())
        before = self.state()
        root = control_directory(self.config, self.config.jobs[0])
        manifest = (root / '.index.json').read_bytes()
        (self.source / 'file.txt').write_bytes(b'new content')
        original = Storage.put
        def fail(storage, source, key, **kwargs):
            if key.endswith('.json'):
                raise BackupError('remote JSON failed')
            return original(storage, source, key, **kwargs)
        with patch.object(Storage, 'put', fail):
            self.assertEqual(self.execute(now=2000), 1)
        self.assertEqual(self.state(), before)
        self.assertEqual((root / '.index.json').read_bytes(), manifest)
        self.assertEqual(self.execute(now=2001), 0, self.output.getvalue())
        for key in self.state()['active']:
            remote = self.client.get_object(Bucket='sambinha-tests', Key='backup/' + key + '.json')['Body'].read()
            self.assertEqual((root / (key + '.json')).read_bytes(), remote)

    def test_s3_identical_simple_and_batch_skip_transfers(self):
        for logic in ('simple', 'batch'):
            with self.subTest(logic=logic):
                self.file('a')
                self.file('b')
                self.config = replace(self.config, jobs=(replace(self.config.jobs[0], logic=logic,
                    destination='aws:sambinha-tests/' + logic),), state_dir=self.root / ('state-' + logic))
                self.assertEqual(self.execute(), 0)
                sync_count = sum(command[1] == 'sync' for command in self.runner.commands)
                self.assertEqual(self.execute(), 0)
                if logic == 'simple':
                    self.assertEqual(sum(command[1] == 'sync' for command in self.runner.commands), sync_count + 1)
                    self.assertFalse(any(e['event'] == 'identical' for e in self.events))
                else:
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

    def test_s3_excluded_files_are_not_uploaded_or_removed(self):
        self.file('keep.txt', b'keep')
        excluded = self.file('skip.tmp', b'old')
        self.assertEqual(self.execute(), 0)
        rules = self.root / 'exclude.txt'
        rules.write_text('*.tmp\n')
        self.config = replace(self.config, jobs=(replace(self.config.jobs[0], exclude_files=rules),))
        excluded.unlink()
        self.file('new.tmp', b'not uploaded')
        self.assertEqual(self.execute(), 0, self.output.getvalue())
        self.assertEqual(self.client.get_object(Bucket='sambinha-tests', Key='backup/skip.tmp')['Body'].read(), b'old')
        objects = self.client.list_objects_v2(Bucket='sambinha-tests', Prefix='backup/new.tmp')
        self.assertEqual(objects['KeyCount'], 0)

    def test_s3_batch_exclusions_protect_mixed_lot(self):
        self.file('keep.txt', b'keep')
        self.file('skip.tmp', b'old')
        self.use_batch()
        # Previous releases could mix text and other files in the same lot.
        with self.subTest(compression='uniform'):
            self.assertEqual(self.execute(), 0)
        old_keys = list(self.state()['active'])
        rules = self.root / 'exclude.txt'
        rules.write_text('*.tmp\n')
        self.config = replace(self.config, jobs=(replace(self.config.jobs[0], exclude_files=rules),))
        self.assertEqual(self.execute(), 0, self.output.getvalue())
        self.assertEqual(set(self.state()['excluded']), set(old_keys))
        download = self.download()
        actual = restore_batches(self.state(), download, download / '.sambinha/selected')
        self.assertEqual(actual, {'keep.txt': b'keep'})
        self.assertTrue(all((download / key).exists() for key in old_keys))

    def test_s3_simple_and_batch_verification_only_reads_metadata(self):
        from sambinha.recovery import verify_local_metadata_job, verify_destination_metadata_job
        from unittest.mock import Mock
        from sambinha.storage import Storage
        self.file('data.txt', bytes(range(256)))
        self.file('more.txt', b'original')
        original = self.runner.run
        for logic in ('simple', 'batch'):
            with self.subTest(logic=logic):
                if logic == 'batch':
                    # Keep the two strategies' independent indexes/prefixes.
                    self.config = replace(self.config, jobs=(replace(self.config.jobs[0], name='batches',
                        logic='batch', destination='aws:sambinha-tests/batches'),))
                self.assertEqual(self.execute(), 0, self.output.getvalue())
                commands = []
                original_get = Storage.get
                def metadata_download(storage, key, target):
                    self.assertTrue(key.endswith('.json') or key == '.sambinha/control.tar.gz')
                    return original_get(storage, key, target)
                def metadata(args, **kwargs):
                    commands.append(args)
                    return original(args, **kwargs)
                with patch.object(self.runner, 'run', metadata), \
                     patch.object(Storage, 'get', metadata_download):
                    if logic == 'simple':
                        result = verify_local_metadata_job(self.config, self.config.jobs[0], self.runner, Mock())
                    else:
                        state = json.loads((self.config.state_dir / self.config.jobs[0].name / 'state.json').read_text())
                        result = verify_destination_metadata_job(self.config, self.config.jobs[0], self.runner, Mock(), state)
                if logic == 'simple':
                    self.assertEqual(result['mode'], 'rclone_check')
                    self.assertTrue(any(command[1] == 'check' for command in commands))
                else:
                    self.assertEqual(result['batches'], len(state['active']))
                    self.assertEqual(result['mode'], 'metadata_sync')
                    self.assertTrue(all((command[1] == 'lsjson' and '--stat' in command and '--hash' not in command)
                                        or (command[1] == 'copyto' and str(command[2]).endswith('.json'))
                                        for command in commands))
                self.assertTrue(commands)

    def test_s3_deletion_warning_continues_backup_and_expiration(self):
        self.use_batch()
        self.config = replace(self.config, jobs=(replace(self.config.jobs[0], max_bytes=1),))
        for name in ('a', 'b', 'c'):
            self.file(name)
        self.config = replace(self.config, deletion_alert_threshold=2)
        self.assertEqual(self.execute(now=1000), 0)
        for name in ('a', 'b', 'c'):
            (self.source / name).unlink()
        self.assertEqual(self.execute(now=2000), 0)
        self.assertEqual(self.execute(now=2000 + 8 * DAY), 0)
        self.assertEqual(self.notifications[-1][1], 'fail')
        self.assertEqual(len(self.state()['retired']), 3)
        self.assertEqual(self.execute(now=2000 + 10 * DAY), 0)
        self.assertEqual(self.state()['retired'], [])
        self.assertEqual(self.notifications[-1][1], 'fail')

    def test_s3_batch_warning_counts_archived_members_and_keeps_deadline(self):
        for name in ('a', 'b', 'c'):
            self.file(name)
        self.use_batch()
        self.config = replace(self.config, deletion_alert_threshold=2)
        self.assertEqual(self.execute(now=1000), 0)
        for name in ('a', 'b', 'c'):
            (self.source / name).unlink()
        self.assertEqual(self.execute(now=2000), 0)
        self.assertEqual(self.execute(now=2000 + 8 * DAY), 0)
        self.assertEqual(self.notifications[-1][1], 'fail')
        self.assertIn('3 arquivos/versões', self.notifications[-1][3])
        self.assertEqual(self.execute(now=2000 + 10 * DAY), 0)
        self.assertEqual(self.state()['retired'], [])

    def test_s3_source_mount_failure_does_not_upload_and_notifies(self):
        self.file('file')
        self.config = replace(self.config, jobs=(replace(self.config.jobs[0], source_mount=self.source),))
        self.assertEqual(self.execute(), 1)
        self.assertEqual(self.client.list_objects_v2(Bucket='sambinha-tests')['KeyCount'], 0)
        self.assertEqual(self.notifications[-1][1], 'fail')
        self.assertIn('Montagem de origem', self.notifications[-1][3])

    def test_stable_growth_and_manual_rebalance_without_source_over_s3(self):
        import shutil
        job = replace(self.config.jobs[0], logic='batch', max_bytes=10)
        self.config = replace(self.config, jobs=(job,))
        for name in ('a','b','c','d'):
            self.file(name, name.encode()*4)
        self.file('large', b'x'*30)
        self.assertEqual(self.execute(), 0, self.output.getvalue())
        (self.source/'a').write_bytes(b'a'*7)
        (self.source/'b').write_bytes(b'b'*7)
        self.assertEqual(self.execute(now=2000), 0, self.output.getvalue())
        before = self.state()
        expected = contents(self.source)
        stable = {k:c for k,c in before['active'].items() if len(c['files'])==1 or sum(f['size'] for f in c['files'])<=10}
        shutil.rmtree(self.source)
        self.assertEqual(self.execute(now=3000, action='rebalance'), 0, self.output.getvalue())
        after=self.state()
        self.assertEqual({k:after['active'][k] for k in stable}, stable)
        self.assertTrue(all(len(c['files'])==1 or sum(f['size'] for f in c['files'])<=10 for c in after['active'].values()))
        directory=self.download()
        self.assertEqual(restore_batches(after,directory,self.root/'rebalanced'),expected)

    def test_s3_state_recovery_and_restore_work_without_original_source(self):
        import shutil
        from contextlib import redirect_stdout
        from sambinha.recovery import recover
        self.config=replace(self.config,jobs=(replace(self.config.jobs[0],logic='batch'),))
        self.file('a',b'original');self.file('b',b'other')
        self.assertEqual(self.execute(),0,self.output.getvalue())
        before=self.state()['active'];expected=contents(self.source)
        state_path=self.config.state_dir/'test/state.json'
        state_path.write_text('corrupt')
        self.assertEqual(self.execute(now=2000),0,self.output.getvalue())
        self.assertEqual(self.state()['active'],before)
        shutil.rmtree(self.source);shutil.rmtree(self.config.state_dir)
        self.config=replace(self.config,jobs=(replace(self.config.jobs[0],source_mount=self.source),))
        with redirect_stdout(io.StringIO()):
            result=recover(self.config,'restore',runner=self.runner,output=self.root/'recovered')
        self.assertEqual(result,0)
        self.assertEqual(contents(self.root/'recovered'),expected)

    def test_lost_s3_destination_contents_are_recreated_from_source(self):
        self.config=replace(self.config,jobs=(replace(self.config.jobs[0],logic='batch'),))
        self.file('a',b'original');self.file('b',b'other')
        self.assertEqual(self.execute(),0,self.output.getvalue())
        before=self.state()['active']
        objects=self.client.list_objects_v2(Bucket='sambinha-tests',Prefix='backup/').get('Contents',[])
        for item in objects:self.client.delete_object(Bucket='sambinha-tests',Key=item['Key'])
        self.assertEqual(self.execute(now=2000),0,self.output.getvalue())
        self.assertEqual(self.state()['active'],before)
        self.assert_complete()
