"""read_index: local state first, remote recovery only as a bounded fallback."""
from dataclasses import replace
from unittest.mock import patch

from sambinha.errors import BackupError, ForeignStateError, UnsupportedStateError
from sambinha.names import STATE_FILE
from sambinha.recovery_index import read_index
from sambinha.state import atomic_json, identity
from tests.helpers import Sandbox


class ReadIndexTests(Sandbox):
    def setUp(self):
        super().setUp()
        self.batch = replace(self.config.jobs[0], logic='batch')
        self.directory = self.config.state_dir / self.batch.name
        self.path = self.directory / STATE_FILE
        self.remote = {'schema': 3, 'identity': identity(self.batch), 'active': {}, 'retired': [], 'remote': True}

    def local(self, **extra):
        atomic_json(self.path, {'schema': 3, 'identity': identity(self.batch), 'active': {}, 'retired': [], **extra})

    def read(self, **options):
        with patch('sambinha.index_generations.remote_index', return_value=(self.remote, {})) as remote:
            state = read_index(self.config, self.batch, object(), self.root / 'work', **options)
        return state, remote

    def test_valid_local_state_is_used_without_touching_the_remote(self):
        self.local()
        state, remote = self.read()
        self.assertNotIn('remote', state)
        remote.assert_not_called()

    def test_missing_local_state_falls_back_to_the_remote_index(self):
        state, remote = self.read()
        self.assertTrue(state['remote'])
        remote.assert_called_once()

    def test_unfinished_publication_marker_ignores_the_local_copy(self):
        self.local()
        (self.directory / 'publication.json').write_text('{}')
        state, remote = self.read()
        self.assertTrue(state['remote'])
        remote.assert_called_once()

    def test_corrupt_local_state_falls_back_to_the_remote_index(self):
        self.directory.mkdir(parents=True)
        self.path.write_text('{ corrupt')
        state, _ = self.read()
        self.assertTrue(state['remote'])

    def test_foreign_and_unsupported_local_states_are_never_replaced_by_the_remote(self):
        self.local(identity=identity(replace(self.batch, name='other')))
        with self.assertRaises(ForeignStateError):
            self.read()
        self.local(schema=99)
        with self.assertRaises(UnsupportedStateError):
            self.read()

    def test_metadata_only_requires_a_usable_local_index_and_downloads_nothing(self):
        with patch('sambinha.index_generations.remote_index') as remote, self.assertRaisesRegex(BackupError, 'índice local'):
            read_index(self.config, self.batch, object(), self.root / 'work', metadata_only=True)
        remote.assert_not_called()
        self.local()
        state, _ = self.read(metadata_only=True)
        self.assertEqual(state['active'], {})

    def test_simple_jobs_have_no_batch_index_to_recover(self):
        job = replace(self.batch, logic='simple')
        with patch('sambinha.index_generations.remote_index') as remote, self.assertRaisesRegex(BackupError, 'simples'):
            read_index(self.config, job, object(), self.root / 'work')
        remote.assert_not_called()

    def test_a_symlinked_state_file_is_rejected(self):
        self.directory.mkdir(parents=True)
        target = self.root / 'elsewhere.json'
        target.write_text('{}')
        self.path.symlink_to(target)
        with self.assertRaises(BackupError):
            self.read()
