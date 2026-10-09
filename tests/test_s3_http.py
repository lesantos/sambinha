"""Full rclone/S3 HTTP integration; opt in where loopback sockets are allowed."""
from dataclasses import replace
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time
import unittest
from unittest.mock import patch
from urllib.request import urlopen

from sambinha.process import Runner
from sambinha.config import load
from tests import test_s3_integration as suite
from tests.helpers import Sandbox


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

    def test_verification_makes_head_requests_and_never_gets_object_bodies(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import hashlib
        import threading
        from unittest.mock import Mock
        from sambinha.inventory import scan
        from sambinha.recovery import verify_job
        self.file('file.txt', b'original')
        item = scan(self.source)[0].metadata()
        requests = []
        class MetadataServer(BaseHTTPRequestHandler):
            def do_HEAD(handler):
                requests.append(('HEAD', handler.path))
                handler.send_response(200)
                handler.send_header('Content-Length', str(item['size']))
                handler.send_header('ETag', '"' + item['md5'] + '"')
                handler.send_header('Last-Modified', 'Fri, 09 Oct 2026 00:00:00 GMT')
                handler.end_headers()

            def do_GET(handler):
                requests.append(('GET', handler.path))
                handler.send_response(403)
                handler.end_headers()

            def log_message(handler, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), MetadataServer)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        config_file = self.root / 'head-only.conf'
        config_file.write_text('[aws]\ntype=s3\nprovider=Other\nregion=us-east-1\n'
                              'access_key_id=testing\nsecret_access_key=testing\n'
                              f'endpoint=http://127.0.0.1:{server.server_port}\nforce_path_style=true\n')
        config = replace(self.config, rclone_config=str(config_file))
        result = verify_job(config, config.jobs[0], Runner(), Mock(), {'active': {'file.txt': item}})
        self.assertEqual(result['mode'], 's3_metadata')
        self.assertTrue(requests)
        self.assertTrue(all(method == 'HEAD' for method, path in requests), requests)
