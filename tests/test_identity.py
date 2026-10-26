"""Name-based backup identity: one job, several machines, different mount points."""
import json
import shutil
import unittest
from dataclasses import replace
from unittest.mock import patch

from sambinha.errors import BackupError, ForeignStateError, UnsupportedStateError
from sambinha.metadata import control_directory
from sambinha.names import JOB_MARKER
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
from tests.helpers import REAL_TOOLS, Sandbox, contents


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


@unittest.skipUnless(REAL_TOOLS, 'rclone and 7z required')
class CrossMachineTests(Sandbox):
    """Machine A is the sandbox default; other machines get their own source copy and state."""

    def setUp(self):
        super().setUp()
        for name in ('a', 'b', 'c'):
            self.file(name, name.encode() * 20)
        self.file('nested/deep', b'd' * 50)

    def batch(self, config=None):
        config = config or self.config
        return replace(config, jobs=tuple(replace(j, logic='batch', max_megabytes=100 / 1024) for j in config.jobs))

    def machine(self, label, config=None, *, name='test', state_dir=None, source=None):
        config = config or self.config
        source = source or (self.root / f'source-{label}')
        if not source.exists():
            shutil.copytree(self.source, source)
        job = replace(config.jobs[0], name=name, source=source)
        return replace(config, jobs=(job,), state_dir=state_dir or self.root / f'state-{label}',
                       log_dir=self.root / f'logs-{label}')

    def run_as(self, host, config, **kwargs):
        with patch('sambinha.state.socket.gethostname', return_value=host):
            return self.execute(config, **kwargs)

    def state_of(self, config):
        return json.loads((config.state_dir / config.jobs[0].name / 'state.json').read_text())

    def restore_with(self, config, output):
        import io
        from contextlib import redirect_stdout

        from sambinha.config import validate
        from sambinha.recovery import recover
        with redirect_stdout(io.StringIO()):
            return recover(validate(config), 'restore', output=output)

    def error_text(self):
        return ' '.join(e.get('error', '') for e in self.events if e.get('event') == 'error')

    def marker(self):
        return json.loads((self.destination / JOB_MARKER).read_text())

    def test_batch_index_is_adopted_by_a_second_machine_and_restorable_there(self):
        a = self.batch()
        self.assertEqual(self.run_as('host-a', a, now=1000), 0, self.output.getvalue())
        before = self.state_of(a)
        self.assertEqual(self.marker()['name'], 'test')
        self.assertEqual(self.marker()['generation'], before['generation'])
        b = self.machine('b', a)
        self.assertEqual(self.run_as('host-b', b, now=2000), 0, self.output.getvalue())
        after = self.state_of(b)
        self.assertEqual(after['active'], before['active'])
        self.assertEqual(after['identity'], identity(b.jobs[0]))
        self.assertEqual([o['host'] for o in after['origins']], ['host-a', 'host-b'])
        self.assertEqual(after['origins'][1]['source'], str(b.jobs[0].source))
        self.assertNotIn('identity_adopted', {e['event'] for e in self.events})
        output = self.root / 'restored-b'
        self.assertEqual(self.restore_with(b, output), 0)
        self.assertEqual(contents(output), contents(self.source))

    def test_simple_copy_from_a_second_machine_shares_the_destination(self):
        self.assertEqual(self.run_as('host-a', self.config, now=1000), 0, self.output.getvalue())
        b = self.machine('b')
        (b.jobs[0].source / 'from-b').write_bytes(b'new')
        self.assertEqual(self.run_as('host-b', b, now=2000), 0, self.output.getvalue())
        output = self.root / 'restored'
        self.assertEqual(self.restore_with(b, output), 0)
        self.assertEqual(contents(output), contents(b.jobs[0].source))

    def test_another_job_name_is_refused_by_the_marker_unless_adopted(self):
        a = self.batch()
        self.assertEqual(self.run_as('host-a', a, now=1000), 0, self.output.getvalue())
        other = self.machine('b', a, name='other')
        self.assertEqual(self.run_as('host-b', other, now=2000), 1)
        self.assertIn("Destino pertence ao job 'test'; use --adopt-identity", self.error_text())
        self.assertFalse((other.state_dir / 'other').exists())
        self.assertEqual(self.marker()['name'], 'test')
        # Renamed job: adoption rewrites identity, state and marker; later runs need no flag.
        self.assertEqual(self.run_as('host-b', other, now=3000, adopt_identity=True), 0, self.output.getvalue())
        adopted = next(e for e in self.events if e['event'] == 'identity_adopted')
        self.assertEqual((adopted['previous_identity']['name'], adopted['identity']['name']), ('test', 'other'))
        self.assertEqual(self.state_of(other)['identity']['name'], 'other')
        self.assertEqual(self.marker()['name'], 'other')
        self.assertEqual(self.run_as('host-b', other, now=4000), 0, self.output.getvalue())
        output = self.root / 'restored'
        self.assertEqual(self.restore_with(other, output), 0)
        self.assertEqual(contents(output), contents(self.source))

    def legacy_state(self, config, **changes):
        job = config.jobs[0]
        path = config.state_dir / job.name / 'state.json'
        state = json.loads(path.read_text())
        state['identity'] = dict(legacy_identity(job), **changes)
        state['schema'] = 2
        state.pop('origins', None)
        path.write_text(json.dumps(state))
        manifest = control_directory(config, job) / '.index.json'
        data = json.loads(manifest.read_text())
        data['identity'] = state['identity']
        manifest.write_text(json.dumps(data))
        return state['generation']

    def test_legacy_state_on_the_same_machine_migrates_and_republishes(self):
        a = self.batch()
        self.assertEqual(self.run_as('host-a', a, now=1000), 0, self.output.getvalue())
        generation = self.legacy_state(a)
        (self.destination / JOB_MARKER).unlink()
        self.assertEqual(self.run_as('host-a', a, now=2000), 0, self.output.getvalue())
        self.assertIn('identity_migrated', {e['event'] for e in self.events})
        state = self.state_of(a)
        self.assertEqual((state['schema'], state['identity']), (STATE_SCHEMA, identity(a.jobs[0])))
        self.assertNotEqual(state['generation'], generation)
        self.assertEqual(self.marker()['generation'], state['generation'])
        manifest = json.loads((control_directory(a, a.jobs[0]) / '.index.json').read_text())
        self.assertEqual(manifest['identity'], identity(a.jobs[0]))

    def test_legacy_state_with_other_paths_requires_adoption(self):
        a = self.batch()
        self.assertEqual(self.run_as('host-a', a, now=1000), 0, self.output.getvalue())
        self.legacy_state(a, source='/mnt/old-disk/Docs')
        self.assertEqual(self.run_as('host-a', a, now=2000), 1)
        self.assertIn('adopt-identity', self.error_text())
        self.assertEqual(self.run_as('host-a', a, now=3000, adopt_identity=True), 0, self.output.getvalue())
        self.assertIn('identity_adopted', {e['event'] for e in self.events})
        self.assertEqual(self.state_of(a)['identity'], identity(a.jobs[0]))
        self.assertEqual(self.run_as('host-a', a, now=4000), 0, self.output.getvalue())

    def test_two_hosts_alternate_and_a_third_restores_everything(self):
        a = self.batch()
        self.assertEqual(self.run_as('host-a', a, now=1000), 0, self.output.getvalue())
        b = self.machine('b', a)
        (b.jobs[0].source / 'only-b').write_bytes(b'b' * 30)
        self.assertEqual(self.run_as('host-b', b, now=2000), 0, self.output.getvalue())
        generation_b = self.state_of(b)['generation']
        # Host A's local state is behind the destination: it must re-adopt the
        # confirmed index instead of planning from stale membership.
        (self.source / 'only-a').write_bytes(b'a' * 30)
        shutil.copy(self.source / 'only-a', b.jobs[0].source / 'only-a')
        self.assertEqual(self.run_as('host-a', a, now=3000), 0, self.output.getvalue())
        self.assertIn('remote_generation_differs', {e['event'] for e in self.events})
        state_a = self.state_of(a)
        self.assertNotEqual(state_a['generation'], generation_b)
        self.assertEqual(sorted(o['host'] for o in state_a['origins']), ['host-a', 'host-b'])
        self.assertEqual(self.marker()['generation'], state_a['generation'])
        c = self.machine('c', a)
        output = self.root / 'restored-c'
        self.assertEqual(self.restore_with(c, output), 0)
        # The last backup (host A, without only-b) is what a third machine restores.
        self.assertEqual(contents(output), contents(self.source))

    def test_shared_metadata_dir_manifest_is_accepted_by_another_machine(self):
        shared = self.root / 'shared-metadata'
        a = replace(self.batch(), metadata_dir=shared)
        self.assertEqual(self.run_as('host-a', a, now=1000), 0, self.output.getvalue())
        b = self.machine('b', a)
        self.assertEqual(self.run_as('host-b', b, now=2000), 0, self.output.getvalue())
        manifest = json.loads((shared / 'test' / '.index.json').read_text())
        self.assertEqual(manifest['identity'], identity(b.jobs[0]))

    def test_foreign_device_fingerprints_force_a_clean_rehash(self):
        a = self.batch()
        self.assertEqual(self.run_as('host-a', a, now=1000), 0, self.output.getvalue())
        path = a.state_dir / 'test' / 'state.json'
        state = json.loads(path.read_text())
        before = state['active']
        cache_path = path.with_name('fingerprints.json')
        cache = json.loads(cache_path.read_text())
        for entry in cache.values():
            entry['signature'][0] += 1  # another machine's st_dev
            entry['file']['sha256'] = '0' * 64
        cache_path.write_text(json.dumps(cache))
        self.assertEqual(self.run_as('host-a', a, now=2000), 0, self.output.getvalue())
        after = self.state_of(a)
        self.assertEqual(after['active'], before)
        cache = json.loads(path.with_name('fingerprints.json').read_text())
        self.assertTrue(cache and all(e['file']['sha256'] != '0' * 64 for e in cache.values()))
