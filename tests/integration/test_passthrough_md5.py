import shutil
import unittest
from dataclasses import replace
from unittest.mock import patch

from sambinha.inventory import fingerprint
from sambinha.state import atomic_json, identity
from sambinha.storage import Storage
from tests.helpers import REAL_TOOLS, Sandbox


@unittest.skipUnless(REAL_TOOLS, 'rclone and 7z required')
class PassthroughMd5Tests(Sandbox):
    """A lost state must not make an unchanged, already uploaded zip be read again."""

    def setUp(self):
        super().setUp()
        self.file('dir/backup.zip', bytes(range(256)) * 4)
        job = replace(self.config.jobs[0], logic='batch', max_megabytes=100 / 1024 ** 2,
                      log_terminal_level='detailed')
        self.config = replace(self.config, jobs=(job,))
        self.assertEqual(self.execute(), 0, self.output.getvalue())
        self.archive = next(key for key, control in self.state()['active'].items() if control.get('passthrough'))
        self.recorded = self.state()['active'][self.archive]
        self.lose_state()

    def lose_state(self):
        directory = self.config.state_dir / 'test'
        for name in ('state.json', 'uploads.json'):
            (directory / name).unlink(missing_ok=True)
        # A first run that never committed also left no recovery index at the destination.
        shutil.rmtree(self.destination / '.sambinha', ignore_errors=True)
        atomic_json(directory / 'initialization.json', {'identity': identity(self.config.jobs[0])})

    def run_counting(self):
        hashed, puts = [], []
        real_hash, real_put = fingerprint, Storage.put

        def counting_hash(root, relative, **kwargs):
            hashed.append(relative)
            return real_hash(root, relative, **kwargs)

        def put(storage, source, key, **kwargs):
            puts.append((key, kwargs))
            return real_put(storage, source, key, **kwargs)
        with patch('sambinha.strategies.batch.fingerprint', counting_hash), patch.object(Storage, 'put', put):
            self.assertEqual(self.execute(now=2000), 0, self.output.getvalue())
        return hashed, puts

    def test_unchanged_zip_reuses_the_recorded_md5(self):
        # With the object still at the destination the batch is adopted untouched (test_adopt_batches);
        # here it must be stored again, and its recorded md5 spares reading it.
        (self.destination / self.archive).unlink()
        hashed, puts = self.run_counting()
        self.assertEqual(hashed, [])
        self.assertTrue(any(e['event'] == 'md5_reused' and e['archive'] == self.archive for e in self.events))
        control = self.state()['active'][self.archive]
        self.assertEqual((control['archive_size'], control['archive_md5']),
                         (self.recorded['archive_size'], self.recorded['archive_md5']))
        self.assertEqual([kwargs for key, kwargs in puts if key == self.archive], [{'metadata_comparison': True}])

    def test_changed_zip_is_hashed_again(self):
        self.file('dir/backup.zip', bytes(range(255, -1, -1)) * 4)
        hashed, _ = self.run_counting()
        self.assertEqual(hashed, ['dir/backup.zip'])
        self.assertFalse(any(e['event'] == 'md5_reused' for e in self.events))
        md5s = {control['archive_md5'] for control in self.state()['active'].values() if control.get('passthrough')}
        self.assertEqual(len(md5s), 1)
        self.assertNotIn(self.recorded['archive_md5'], md5s)

    def test_unreadable_mirror_falls_back_to_hashing(self):
        # Neither the mirror nor the stored control can be adopted, so the file is hashed again.
        (self.config.state_dir / 'test' / 'controls' / (self.archive + '.json')).write_text('{broken')
        (self.destination / (self.archive + '.json')).write_text('{broken')
        hashed, _ = self.run_counting()
        self.assertEqual(hashed, ['dir/backup.zip'])
