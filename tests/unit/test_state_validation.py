"""Defensive validation of state.json (load_state, validate_file, validate_controls) with malformed shapes."""
import copy
import json
from dataclasses import replace

from sambinha.errors import BackupError, ForeignStateError, UnsupportedStateError
from sambinha.state import (STATE_SCHEMA, atomic_json, identity, identity_options, load_state, validate_controls,
                            validate_file, validate_origins)
from tests.helpers import Sandbox

HEX64, HEX32 = 'a' * 64, 'b' * 32


def file_item(path='a.txt', **values):
    return {'path': path, 'size': 1, 'sha256': HEX64, 'md5': HEX32, 'mtime_ns': 5, 'mode': 0o640, **values}


def control(key='a.7z', *paths, **values):
    return {'schema': 1, 'archive': key, 'files': [file_item(p) for p in paths or ('a.txt',)], **values}


class StateValidationTests(Sandbox):
    def setUp(self):
        super().setUp()
        self.batch = replace(self.config.jobs[0], logic='batch')
        self.path = self.root / 'state.json'

    def good(self):
        return {'schema': STATE_SCHEMA, 'identity': identity(self.batch), 'active': {'a.7z': control()},
                'retired': [{'key': 'old.7z', 'expires': 10}]}

    def load(self, data, job=None):
        job = job or self.batch
        self.path.write_text(data if isinstance(data, str) else json.dumps(data))
        return load_state(self.path, identity(job), **identity_options(job))

    def assertRejected(self, data, error=BackupError):
        with self.assertRaises(error):
            self.load(data)

    def test_valid_state_round_trips_and_missing_file_is_empty(self):
        data = self.good()
        self.assertEqual(self.load(data), data)
        self.path.unlink()
        self.assertEqual(load_state(self.path, identity(self.batch)),
                         {'schema': STATE_SCHEMA, 'identity': identity(self.batch), 'active': {}, 'retired': []})

    def test_top_level_shapes_are_rejected(self):
        for name, data in (('not json', '{'), ('list', '[]'), ('no schema', '{}'), ('null', 'null')):
            with self.subTest(name):
                self.assertRejected(data)

    def test_schema_and_identity_defects(self):
        for schema in ('3', 3.0, True, None):
            with self.subTest(schema=schema):
                state = self.good(); state['schema'] = schema
                self.assertRejected(state, UnsupportedStateError)
        state = self.good(); state['schema'] = 99
        self.assertRejected(state, UnsupportedStateError)
        for name, identity_value in (('list', []), ('non-str key', {1: 'x'}), ('bad name type', {'schema': 1, 'name': 5}),
                                     ('empty name', {**identity(self.batch), 'name': ''}),
                                     ('extra key', {**identity(self.batch), 'extra': 1}),
                                     ('wrong identity schema', {**identity(self.batch), 'schema': 99})):
            with self.subTest(name):
                state = self.good(); state['identity'] = identity_value
                with self.assertRaises(BackupError):
                    self.load(json.dumps(state, default=str))
        state = self.good(); state['identity'] = {k: v for k, v in identity(self.batch).items() if k != 'name'}
        self.assertRejected(state)  # a schema-3 state must carry a named identity

    def test_foreign_identity_is_distinguished_from_corruption(self):
        other = replace(self.batch, name='other')
        state = self.good(); state['identity'] = identity(other)
        self.assertRejected(state, ForeignStateError)

    def test_active_and_retired_container_types(self):
        for key, value in (('active', []), ('active', None), ('retired', {}), ('retired', 'x')):
            with self.subTest(key=key, value=value):
                state = self.good(); state[key] = value
                self.assertRejected(state)
        state = self.good(); del state['retired']
        self.assertRejected(state)

    def test_origins_generation_and_optional_timestamps(self):
        origin = {'host': 'h', 'platform': 'p', 'source': 's', 'destination': 'd', 'last_seen': 1.5}
        cases = {
            'origins not list': {'origins': {}},
            'origins too many': {'origins': [origin] * 100},
            'origin not dict': {'origins': ['x']},
            'origin host type': {'origins': [{**origin, 'host': 3}]},
            'origin last_seen bool': {'origins': [{**origin, 'last_seen': True}]},
            'origin last_seen inf': {'origins': [{**origin, 'last_seen': float('inf')}]},
            'generation type': {'generation': 5},
            'generation format': {'generation': 'abc'},
            'last_verified type': {'last_verified': 'x'},
            'last_verified nan': {'last_verified': float('nan')},
            'last_full_scan type': {'last_full_scan': None},
            'excluded not dict': {'excluded': []},
            'excluded bad control': {'excluded': {'x.7z': {'schema': 1}}},
            'fingerprints not dict': {'fingerprints': []},
            'fingerprint bad entry': {'fingerprints': {'a.txt': {'file': file_item('b.txt'), 'signature': [1] * 6}}},
            'fingerprint bad signature': {'fingerprints': {'a.txt': {'file': file_item(), 'signature': [1, 2]}}},
        }
        for name, extra in cases.items():
            with self.subTest(name):
                state = self.good(); state.update(copy.deepcopy(extra))
                with self.assertRaises(BackupError):
                    self.load(json.dumps(state, allow_nan=True))
        good = self.good(); good['origins'] = [origin]; good['generation'] = '1' * 20 + '-' + 'c' * 32
        good.update(last_verified=1, last_full_scan=2.5,
                    fingerprints={'a.txt': {'file': file_item(), 'signature': [1, 2, 3, 4, 5, 6]}})
        self.assertEqual(self.load(good), good)
        validate_origins([origin])

    def test_retired_entry_defects(self):
        cases = {
            'missing key': {'expires': 1},
            'unsafe key': {'key': '../x', 'expires': 1},
            'duplicate of active': {'key': 'a.7z', 'expires': 1},
            'missing expires': {'key': 'o.7z'},
            'expires type': {'key': 'o.7z', 'expires': 'soon'},
            'expires nan': {'key': 'o.7z', 'expires': float('nan')},
            'alert type': {'key': 'o.7z', 'expires': 1, 'alert_at': 'x'},
            'alert inf': {'key': 'o.7z', 'expires': 1, 'alert_at': float('inf')},
            'files not list': {'key': 'o.7z', 'expires': 1, 'files': 'a'},
            'files unsafe': {'key': 'o.7z', 'expires': 1, 'files': ['/abs']},
            'control unsafe': {'key': 'o.7z', 'expires': 1, 'control': '../c.json'},
            'control mismatch': {'key': 'o.7z', 'expires': 1, 'control': 'other.json'},
            'not a dict': 'x',
        }
        for name, entry in cases.items():
            with self.subTest(name):
                state = self.good(); state['retired'] = [entry]
                with self.assertRaises(BackupError):
                    self.load(json.dumps(state, allow_nan=True))
        state = self.good(); state['retired'] = [{'key': 'o.7z', 'expires': 1}] * 2
        self.assertRejected(state)
        state['retired'] = [{'key': 'o.7z', 'expires': 1.5, 'alert_at': 2, 'files': ['x'], 'control': 'o.7z.json'}]
        self.assertEqual(self.load(state)['retired'], state['retired'])

    def test_batch_control_defects(self):
        cases = {
            'not dict': 'x', 'schema': control(schema=2), 'archive mismatch': control(archive='b.7z'),
            'files not list': control(files='x'), 'no files': control(files=[]),
            'individual type': control(individual=1), 'individual with two files': control('a.7z', 'a', 'b', individual=True),
            'passthrough type': control(passthrough='yes'),
            'passthrough two files': control('a.7z', 'a', 'b', passthrough=True),
            'passthrough not individual': control(passthrough=True, individual=False),
            'batch_id type': control(batch_id=7), 'batch_id format': control(batch_id='xyz'),
            'archive_size without md5': control(archive_size=1),
            'archive_md5 without size': control(archive_md5=HEX32),
            'archive_size negative': control(archive_size=-1, archive_md5=HEX32),
            'archive_md5 bad': control(archive_size=1, archive_md5='nothex'),
            'duplicate path in batch': control('a.7z', 'x', 'x'),
            'file overlaps directory': control('a.7z', 'x', 'x/y'),
        }
        for name, value in cases.items():
            with self.subTest(name):
                with self.assertRaises(ValueError):
                    validate_controls({'a.7z': value}, True)
        with self.assertRaises(ValueError):  # the same path in two batches
            validate_controls({'a.7z': control(), 'b.7z': control('b.7z')}, True)
        validate_controls({'a.7z': control(individual=True, passthrough=True, batch_id='f' * 24,
                                           archive_size=3, archive_md5=HEX32)}, True)

    def test_simple_controls_are_file_items_keyed_by_their_path(self):
        validate_controls({'a.txt': file_item(), 'b/c.txt': file_item('b/c.txt')}, False)
        for name, controls in (('key differs', {'x.txt': file_item()}), ('unsafe key', {'../a': file_item('../a')}),
                               ('overlap', {'a': file_item('a'), 'a/b': file_item('a/b')}),
                               ('not a dict', {'a.txt': 5})):
            with self.subTest(name), self.assertRaises((ValueError, BackupError)):
                validate_controls(controls, False)

    def test_simple_state_loads_through_the_same_checks(self):
        job = replace(self.batch, logic='simple')
        state = {'schema': STATE_SCHEMA, 'identity': identity(job), 'active': {'a.txt': file_item()}, 'retired': []}
        self.assertEqual(self.load(state, job), state)
        state['active']['a.txt']['path'] = 'b.txt'
        with self.assertRaises(BackupError):
            self.load(state, job)

    def test_file_item_defects(self):
        validate_file(file_item())
        validate_file(file_item(sha256='', md5=''))  # metadata-only inventories carry no hashes
        extra = dict(file_item(), extra=1)
        missing = {k: v for k, v in file_item().items() if k != 'mode'}
        cases = {
            'not dict': 'x', 'extra key': extra, 'missing key': missing,
            'size bool': file_item(size=True), 'size negative': file_item(size=-1), 'size float': file_item(size=1.0),
            'mtime type': file_item(mtime_ns='1'), 'mode type': file_item(mode='640'),
            'mode negative': file_item(mode=-1), 'mode too large': file_item(mode=0o10000),
            'sha256 upper': file_item(sha256='A' * 64), 'sha256 short': file_item(sha256='a' * 63),
            'md5 type': file_item(md5=5), 'md5 only blank': file_item(md5=''),
            'sha256 only blank': file_item(sha256=''),
        }
        for name, item in cases.items():
            with self.subTest(name), self.assertRaises((ValueError, BackupError)):
                validate_file(item)
        for path in ('', '/abs', '../x', 'a/../b', 'a//b'):
            with self.subTest(path=path), self.assertRaises((ValueError, BackupError)):
                validate_file(file_item(path))

    def test_atomic_json_is_a_valid_state_file(self):
        data = self.good()
        atomic_json(self.path, data)
        self.assertEqual(load_state(self.path, identity(self.batch), **identity_options(self.batch)), data)
