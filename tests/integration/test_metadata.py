import json
import unittest
from dataclasses import replace
from unittest.mock import Mock, patch

from sambinha.errors import BackupError
from sambinha.metadata import control_directory
from sambinha.recovery import recover
from sambinha.storage import Storage
from sambinha.verification import verify_local_metadata_job
from tests.helpers import REAL_TOOLS, Sandbox


@unittest.skipUnless(REAL_TOOLS, 'rclone and 7z required')
class MetadataPublicationTests(Sandbox):
    def setUp(self):
        super().setUp()
        self.config = replace(self.config, metadata_dir=self.root / 'metadata',
                              jobs=(replace(self.config.jobs[0], logic='batch', max_megabytes=20 / 1024 ** 2),))
        self.file('a.txt', b'alpha')
        self.file('b.txt', b'beta')

    def assert_replicas(self):
        state = self.state()
        root = control_directory(self.config, self.config.jobs[0])
        for key in state['active']:
            self.assertEqual((root / (key + '.json')).read_bytes(), (self.destination / (key + '.json')).read_bytes())
        self.assertEqual(json.loads((root / '.index.json').read_text())['active'], sorted(state['active']))

    def test_updated_batches_publish_identical_local_and_remote_jsons(self):
        self.assertEqual(self.execute(), 0)
        self.assert_replicas()
        (self.source / 'a.txt').write_bytes(b'new content')
        self.assertEqual(self.execute(now=2000), 0)
        self.assert_replicas()
        original = Storage.put
        def forbid_control_update(storage, source, key, **kwargs):
            self.assertFalse(key.endswith('.json'))
            return original(storage, source, key, **kwargs)
        with patch.object(Storage, 'put', forbid_control_update):
            self.assertEqual(self.execute(now=2001), 0)

    def test_remote_json_failure_does_not_advance_local_state_or_manifest_and_retry_repairs(self):
        self.assertEqual(self.execute(), 0)
        before = self.state()
        root = control_directory(self.config, self.config.jobs[0])
        manifest = (root / '.index.json').read_bytes()
        (self.source / 'a.txt').write_bytes(b'new content')
        original = Storage.put
        def fail(storage, source, key, **kwargs):
            if key.endswith('.json'):
                raise BackupError('JSON remoto falhou')
            return original(storage, source, key, **kwargs)
        with patch.object(Storage, 'put', fail):
            self.assertEqual(self.execute(now=2000), 1)
        self.assertEqual(self.state(), before)
        self.assertEqual((root / '.index.json').read_bytes(), manifest)
        self.assertEqual(self.execute(now=2001), 0)
        self.assert_replicas()

    def test_local_json_failure_does_not_advance_state_and_retry_repairs(self):
        self.assertEqual(self.execute(), 0)
        before = self.state()
        root = control_directory(self.config, self.config.jobs[0])
        manifest = (root / '.index.json').read_bytes()
        (self.source / 'a.txt').write_bytes(b'new content')
        from sambinha.metadata import atomic_json as save
        def fail(path, control, **kwargs):
            if path.name != '.index.json' and path.is_relative_to(root):
                raise OSError('JSON local falhou')
            return save(path, control, **kwargs)
        with patch('sambinha.metadata.atomic_json', fail):
            self.assertEqual(self.execute(now=2000), 1)
        self.assertEqual(self.state(), before)
        self.assertEqual((root / '.index.json').read_bytes(), manifest)
        self.assertEqual(self.execute(now=2001), 0)
        self.assert_replicas()

    def test_manual_integrity_is_separate_from_daily_verification(self):
        self.assertEqual(self.execute(), 0)
        key = next(iter(self.state()['active']))
        (self.destination / key).write_bytes(b'corrupt')
        with patch.object(Storage, 'get', side_effect=AssertionError('daily archive read')):
            self.assertFalse(verify_local_metadata_job(self.config, self.config.jobs[0], Mock(), Mock())['needs_update'])
        self.assertEqual(recover(self.config, 'verify-destination-integrity'), 1)

    def test_recovery_index_failure_keeps_previous_active_catalog_until_retry(self):
        self.assertEqual(self.execute(), 0)
        before = self.state()
        root = control_directory(self.config, self.config.jobs[0])
        manifest = (root / '.index.json').read_bytes()
        (self.source / 'a.txt').write_bytes(b'new content')
        original = Storage.put
        def fail(storage, source, key, **kwargs):
            if key == '.sambinha/control.tar.gz':
                raise BackupError('índice remoto falhou')
            return original(storage, source, key, **kwargs)
        with patch.object(Storage, 'put', fail):
            self.assertEqual(self.execute(now=2000), 1)
        self.assertEqual(self.state(), before)
        self.assertEqual((root / '.index.json').read_bytes(), manifest)
        self.assertEqual(self.execute(now=2001), 0)
        self.assert_replicas()

    def test_archive_failure_does_not_publish_new_jsons_or_advance_state(self):
        self.assertEqual(self.execute(), 0)
        before = self.state()
        root = control_directory(self.config, self.config.jobs[0])
        controls = {p.relative_to(root): p.read_bytes() for p in root.rglob('*.json')}
        (self.source / 'a.txt').write_bytes(b'new content')
        called = []
        def fail(storage, source, key, **kwargs):
            called.append(key)
            raise BackupError('lote falhou')
        with patch.object(Storage, 'put', fail):
            self.assertEqual(self.execute(now=2000), 1)
        self.assertTrue(all(key.endswith('.7z') for key in called))
        self.assertEqual(self.state(), before)
        self.assertEqual({p.relative_to(root): p.read_bytes() for p in root.rglob('*.json')}, controls)
