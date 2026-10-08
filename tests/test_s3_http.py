"""Full rclone/S3 HTTP integration; opt in where loopback sockets are allowed."""
from dataclasses import replace
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time
import unittest
from urllib.request import urlopen

from smabafiba.process import Runner
from tests import test_s3_integration as suite
from tests.helpers import Sandbox


@unittest.skipUnless(os.environ.get('SMABAFIBA_HTTP_S3_TESTS') == '1',
                     'HTTP integration requires SMABAFIBA_HTTP_S3_TESTS=1 and loopback networking')
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
        for _ in range(100):
            try:
                with urlopen(cls.endpoint, timeout=1):
                    return
            except OSError:
                if cls.server.poll() is not None:
                    raise RuntimeError('Moto server exited before readiness')
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
