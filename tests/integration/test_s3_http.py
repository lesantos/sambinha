"""Full rclone/S3 HTTP integration; opt in where loopback sockets are allowed."""
import os
import shutil
import socket
import subprocess
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from urllib.request import urlopen

from sambinha.config import load
from sambinha.process import Runner
from tests.helpers import Sandbox
from tests.integration import test_s3_integration as suite


@unittest.skipUnless(os.environ.get('SAMBINHA_HTTP_S3_TESTS') == '1',
                     'HTTP integration requires SAMBINHA_HTTP_S3_TESTS=1 and loopback networking')
class S3HTTPTests(suite.S3IntegrationTests):
    @classmethod
    def setUpClass(cls):
        import sys
        executable = shutil.which('moto_server') or str(Path(sys.executable).parent / 'moto_server')
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        cls.endpoint = f'http://127.0.0.1:{port}'
        cls.server = subprocess.Popen([executable, '-H', '127.0.0.1', '-p', str(port)],
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        def stop():
            cls.server.terminate()
            try:
                cls.server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                cls.server.kill()
                cls.server.wait()
        cls.addClassCleanup(stop)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                with urlopen(cls.endpoint, timeout=1):
                    return
            except OSError:
                if cls.server.poll() is not None:
                    raise RuntimeError('Moto server exited before readiness') from None
                time.sleep(0.1)
        raise RuntimeError('Moto server did not become ready')

    def setUp(self):
        Sandbox.setUp(self)
        import boto3
        self.client = boto3.client('s3', region_name='us-east-1', endpoint_url=self.endpoint,
                                  aws_access_key_id='testing', aws_secret_access_key='testing')
        # Each test receives a clean emulated bucket.
        for bucket in self.client.list_buckets()['Buckets']:
            name = bucket['Name']
            for page in self.client.get_paginator('list_objects_v2').paginate(Bucket=name):
                for obj in page.get('Contents', []):
                    self.client.delete_object(Bucket=name, Key=obj['Key'])
            self.client.delete_bucket(Bucket=name)
        self.prepare_s3()
        rclone_config = self.root / 'rclone.conf'
        rclone_config.write_text('[aws]\ntype=s3\nprovider=Other\nregion=us-east-1\n'
            'access_key_id=testing\nsecret_access_key=testing\n'
            f'endpoint={self.endpoint}\nforce_path_style=true\n')
        self.config = replace(self.config, rclone_config=str(rclone_config))
        self.runner = Runner()
        self.runner.commands = []
        run_command = self.runner.run
        def record_command(args, **kwargs):
            self.runner.commands.append(list(args))
            return run_command(args, **kwargs)
        self.runner.run = record_command

    def test_secrets_supply_remote_and_credentials_to_real_rclone(self):
        self.fixture()
        # A remote declared entirely in dotenv verifies both preflight and the
        # real rclone subprocess use the configuration's private environment.
        rclone_config = self.root / 'rclone.conf'
        rclone_config.write_text('')
        (self.root / 'secrets.env').write_text(
            'RCLONE_CONFIG_AWS_TYPE=s3\nRCLONE_CONFIG_AWS_PROVIDER=Other\n'
            'RCLONE_CONFIG_AWS_REGION=us-east-1\nRCLONE_CONFIG_AWS_ENV_AUTH=true\n'
            f'RCLONE_CONFIG_AWS_ENDPOINT={self.endpoint}\n'
            'RCLONE_CONFIG_AWS_FORCE_PATH_STYLE=true\n'
            'AWS_ACCESS_KEY_ID=testing\nAWS_SECRET_ACCESS_KEY=PRIVATE-S3-SECRET\n')
        path = self.root / 'config.toml'
        path.write_text('[backup]\nsecrets_file="secrets.env"\n'
            'rclone_config="rclone.conf"\ncronitor_url=""\n'
            '[[jobs]]\nname="test"\nsource="source"\n'
            'type="s3"\ndestination="aws:sambinha-tests/backup"\n')
        inherited = {k: v for k, v in os.environ.items()
                     if not k.startswith(('AWS_', 'RCLONE_'))}
        with patch.dict(os.environ, inherited, clear=True):
            self.config = load(path)
            self.assertEqual(Sandbox.execute(self), 0, self.output.getvalue())
            self.assertNotIn('AWS_SECRET_ACCESS_KEY', os.environ)
        self.assert_complete()
        self.assertNotIn('PRIVATE-S3-SECRET', self.output.getvalue())

    def test_new_zip_and_batch_take_md5_from_the_stored_object_without_hashing_files(self):
        import hashlib

        from sambinha.inventory import fingerprint
        payload = bytes(range(256)) * 400
        self.file('big.zip', payload)
        self.file('small.txt', b'small')
        job = replace(self.config.jobs[0], logic='batch', max_megabytes=50 / 1024 ** 2,
                      log_terminal_level='detailed')
        self.config = replace(self.config, jobs=(job,))
        hashed = []
        real = fingerprint

        def counting(root, relative, **kwargs):
            hashed.append(relative)
            return real(root, relative, **kwargs)
        with patch('sambinha.strategies.batch.fingerprint', counting):
            self.assertEqual(self.execute(), 0, self.output.getvalue())
        self.assertEqual(hashed, [])
        sources = {e['archive'] for e in self.events if e['event'] == 'md5_from_destination'}
        controls = self.state()['active']
        self.assertEqual(sources, set(controls))
        zipped = next(c for c in controls.values() if c.get('passthrough'))
        self.assertEqual(zipped['archive_md5'], hashlib.md5(payload).hexdigest())
        for key, control in controls.items():
            stored = self.client.get_object(Bucket='sambinha-tests', Key='backup/' + key)['Body'].read()
            self.assertEqual((control['archive_size'], control['archive_md5']),
                             (len(stored), hashlib.md5(stored).hexdigest()))

    def test_lost_state_adopts_the_stored_batches_after_listing_the_bucket(self):
        from sambinha.archives import compress
        from sambinha.state import atomic_json, identity
        self.file('big.zip', bytes(range(256)) * 400)
        for name in 'abcd':
            self.file(name, name.encode() * 4)
        job = replace(self.config.jobs[0], logic='batch', max_megabytes=10 / 1024 ** 2, batch_workers=1,
                      log_terminal_level='detailed')
        self.config = replace(self.config, jobs=(job,))
        self.assertEqual(self.execute(), 0, self.output.getvalue())
        confirmed = self.state()['active']
        directory = self.config.state_dir / 'test'
        for name in ('state.json', 'uploads.json'):
            (directory / name).unlink(missing_ok=True)
        for obj in self.client.list_objects_v2(Bucket='sambinha-tests', Prefix='backup/.sambinha/')\
                .get('Contents', []):
            self.client.delete_object(Bucket='sambinha-tests', Key=obj['Key'])
        atomic_json(directory / 'initialization.json', {'identity': identity(job)})
        built = []

        def counting(worker, item, temp):
            built.append(item.key)
            return compress(worker, item, temp)
        with patch('sambinha.strategies.batch.compress', counting):
            self.assertEqual(self.execute(now=2000), 0, self.output.getvalue())
        self.assertEqual(built, [])
        self.assertEqual([e['count'] for e in self.events if e['event'] == 'batches_adopted'], [len(confirmed)])
        self.assertEqual(set(self.state()['active']), set(confirmed))

    def test_lost_state_and_mirrors_adopt_batches_from_the_controls_stored_in_the_bucket(self):
        import shutil

        from sambinha.archives import compress
        from sambinha.state import atomic_json, identity
        self.file('big.zip', bytes(range(256)) * 400)
        for name in 'abcd':
            self.file(name, name.encode() * 4)
        job = replace(self.config.jobs[0], logic='batch', max_megabytes=10 / 1024 ** 2, batch_workers=1,
                      log_terminal_level='detailed')
        self.config = replace(self.config, jobs=(job,))
        self.assertEqual(self.execute(), 0, self.output.getvalue())
        confirmed = self.state()['active']
        directory = self.config.state_dir / 'test'
        for name in ('state.json', 'uploads.json'):
            (directory / name).unlink(missing_ok=True)
        shutil.rmtree(directory / 'controls')
        for obj in self.client.list_objects_v2(Bucket='sambinha-tests', Prefix='backup/.sambinha/')\
                .get('Contents', []):
            self.client.delete_object(Bucket='sambinha-tests', Key=obj['Key'])
        atomic_json(directory / 'initialization.json', {'identity': identity(job)})
        built, copies = [], []

        def counting(worker, item, temp):
            built.append(item.key)
            return compress(worker, item, temp)
        run = self.runner.run

        def record(args, **kwargs):
            copies.append(list(args))
            return run(args, **kwargs)
        self.runner.run = record
        with patch('sambinha.strategies.batch.compress', counting):
            self.assertEqual(self.execute(now=2000), 0, self.output.getvalue())
        self.assertEqual(built, [])
        event = next(e for e in self.events if e['event'] == 'batches_adopted')
        self.assertEqual((event['count'], event['from_destination'], event['rebuilt']),
                         (len(confirmed), len(confirmed), 0))
        self.assertEqual(sum(1 for c in copies if len(c) > 1 and c[1] == 'copy' and '--files-from-raw' in c), 1)
        self.assertEqual(set(self.state()['active']), set(confirmed))
        self.assertEqual({k: c['archive_md5'] for k, c in self.state()['active'].items()},
                         {k: c['archive_md5'] for k, c in confirmed.items()})

    def test_verification_makes_head_requests_and_never_gets_object_bodies(self):
        from unittest.mock import Mock, patch

        from sambinha.verification import verify_local_metadata_job
        self.file('file.txt', b'original')
        self.assertEqual(self.execute(), 0, self.output.getvalue())
        with patch('sambinha.storage.Storage.get', side_effect=AssertionError('download')):
            result = verify_local_metadata_job(self.config, self.config.jobs[0], self.runner, Mock())
        self.assertEqual(result['mode'], 'rclone_check')
        self.assertTrue(any(command[1] == 'check' for command in self.runner.commands))
