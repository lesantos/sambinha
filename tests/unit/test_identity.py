"""Name-based backup identity: one job, several machines, different mount points."""


from dataclasses import replace
from unittest.mock import patch

from sambinha.errors import BackupError, ForeignStateError, UnsupportedStateError
from sambinha.state import (
    IDENTITY_SCHEMA,
    MAX_ORIGINS,
    STATE_SCHEMA,
    atomic_json,
    identity,
    identity_matches,
    legacy_identity,
    load_state,
    record_origin,
)
from tests.helpers import Sandbox


class IdentityUnitTests(Sandbox):
    def test_schema_2_identity_ignores_paths_except_s3_destination(self):
        job = self.config.jobs[0]
        moved = replace(job, source=self.root / 'elsewhere', destination=str(self.root / 'other-dest'))
        self.assertEqual(identity(job), identity(moved))
        self.assertEqual(identity(job), {'schema': IDENTITY_SCHEMA, 'name': 'test', 'type': 'local',
                                         'logic': 'simple', 'namespace': '.sambinha'})
        s3 = replace(job, type='s3', destination='aws:bucket/a')
        self.assertEqual(identity(s3)['destination'], 'aws:bucket/a')
        self.assertNotEqual(identity(s3), identity(replace(s3, destination='aws:bucket/b')))
        self.assertNotEqual(identity(job), identity(replace(job, name='other')))

    def test_identity_matches_legacy_renamed_and_adoption_rules(self):
        job = self.config.jobs[0]
        expected, legacy = identity(job), legacy_identity(job)
        self.assertTrue(identity_matches(expected, expected))
        self.assertTrue(identity_matches(legacy, expected, legacy=legacy))
        other_paths = dict(legacy, source='/other/path')
        self.assertFalse(identity_matches(other_paths, expected, legacy=legacy))
        self.assertTrue(identity_matches(other_paths, expected, legacy=legacy, adopt=True))
        self.assertFalse(identity_matches(dict(other_paths, logic='batch'), expected, legacy=legacy, adopt=True))
        renamed = identity(replace(job, name='renamed'))
        self.assertFalse(identity_matches(renamed, expected))
        self.assertTrue(identity_matches(renamed, expected, adopt=True))
        self.assertFalse(identity_matches(dict(renamed, logic='batch'), expected, adopt=True))
        self.assertFalse(identity_matches(None, expected, adopt=True))

    def test_state_file_schema_rules(self):
        job = self.config.jobs[0]
        path = self.root / 'state.json'
        self.assertEqual(load_state(path, identity(job))['schema'], STATE_SCHEMA)
        legacy = {'schema': 2, 'identity': legacy_identity(job), 'active': {}, 'retired': []}
        atomic_json(path, legacy)
        with self.assertRaises(ForeignStateError):
            load_state(path, identity(job))
        self.assertEqual(load_state(path, identity(job), legacy=legacy_identity(job))['identity'], legacy_identity(job))
        atomic_json(path, dict(legacy, schema=STATE_SCHEMA))
        with self.assertRaisesRegex(BackupError, 'inválido'):
            load_state(path, identity(job), legacy=legacy_identity(job))
        atomic_json(path, dict(legacy, schema=STATE_SCHEMA + 1, identity=identity(job)))
        with self.assertRaises(UnsupportedStateError):
            load_state(path, identity(job))

    def test_origins_are_upserted_per_host_and_bounded(self):
        job = self.config.jobs[0]
        state = {'origins': []}
        for number in range(MAX_ORIGINS + 3):
            with patch('sambinha.state.socket.gethostname', return_value=f'host-{number}'):
                record_origin(state, job, now=number)
        self.assertEqual(len(state['origins']), MAX_ORIGINS)
        self.assertEqual(state['origins'][-1]['host'], f'host-{MAX_ORIGINS + 2}')
        with patch('sambinha.state.socket.gethostname', return_value='host-5'):
            record_origin(state, replace(job, source=self.root / 'moved'), now=99)
        hosts = [o['host'] for o in state['origins']]
        self.assertEqual(hosts.count('host-5'), 1)
        self.assertEqual(state['origins'][-1], {'host': 'host-5', 'platform': state['origins'][-1]['platform'],
                                                'source': str(self.root / 'moved'),
                                                'destination': job.destination, 'last_seen': 99})
        path = self.root / 'state.json'
        atomic_json(path, {'schema': STATE_SCHEMA, 'identity': identity(job), 'active': {}, 'retired': [],
                           'origins': [{'host': 1}]})
        with self.assertRaisesRegex(BackupError, 'inválido'):
            load_state(path, identity(job))
