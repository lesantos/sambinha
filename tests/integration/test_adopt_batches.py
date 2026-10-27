import os
import shutil
import unittest
from dataclasses import replace
from unittest.mock import patch

from sambinha.archives import compress
from sambinha.state import atomic_json, identity
from sambinha.storage import Storage
from tests.helpers import REAL_TOOLS, Sandbox


@unittest.skipUnless(REAL_TOOLS, 'rclone and 7z required')
class AdoptBatchesTests(Sandbox):
    """After a lost state, batches whose control mirror and destination object are intact are adopted."""

    def setUp(self):
        super().setUp()
        # 10-byte batches of 4-byte files: two per batch, so six files make three batches.
        for name in 'abcdef':
            self.file(name, name.encode() * 4)
        self.file('dir/backup.zip', bytes(range(256)) * 4)
        self.use(log_terminal_level='detailed')
        self.assertEqual(self.execute(), 0, self.output.getvalue())
        self.confirmed = self.state()['active']
        self.assertGreaterEqual(len(self.confirmed), 4)
        self.lose_state()

    def use(self, **overrides):
        job = replace(self.config.jobs[0], logic='batch', max_megabytes=10 / 1024 ** 2, batch_workers=1, **overrides)
        self.config = replace(self.config, jobs=(job,))

    def lose_state(self):
        directory = self.config.state_dir / 'test'
        for name in ('state.json', 'uploads.json'):
            (directory / name).unlink(missing_ok=True)
        shutil.rmtree(self.destination / '.sambinha', ignore_errors=True)
        atomic_json(directory / 'initialization.json', {'identity': identity(self.config.jobs[0])})

    def rerun(self):
        built, puts = [], []
        real_put = Storage.put

        def counting_compress(worker, item, temp):
            built.append(item.key)
            return compress(worker, item, temp)

        def put(storage, source, key, **kwargs):
            puts.append(key)
            return real_put(storage, source, key, **kwargs)
        with patch('sambinha.strategies.batch.compress', counting_compress), patch.object(Storage, 'put', put):
            self.assertEqual(self.execute(now=2000), 0, self.output.getvalue())
        return built, puts

    def adopted(self):
        return [e for e in self.events if e['event'] == 'batches_adopted']

    def test_intact_batches_are_adopted_without_reading_or_uploading_them(self):
        built, puts = self.rerun()
        self.assertEqual(built, [])
        self.assertEqual([key for key in puts if not key.startswith('.sambinha/')], [])
        self.assertEqual(self.adopted()[0]['count'], len(self.confirmed))
        self.assertEqual(set(self.state()['active']), set(self.confirmed))

    def test_changed_member_rebuilds_only_its_batch(self):
        # Same size and scan position (the last file), new date: only the batch holding it changes.
        stat = (self.source / 'f').stat()
        os.utime(self.source / 'f', ns=(stat.st_atime_ns, stat.st_mtime_ns + 5 * 10 ** 9))
        built, _ = self.rerun()
        self.assertEqual(len(built), 1)
        self.assertEqual(self.adopted()[0]['count'], len(self.confirmed) - 1)

    def test_batch_missing_at_the_destination_is_rebuilt(self):
        key = next(k for k, c in self.confirmed.items() if not c.get('passthrough'))
        (self.destination / key).unlink()
        built, puts = self.rerun()
        self.assertEqual(built, [key])
        self.assertIn(key, puts)
        self.assertEqual(self.adopted()[0]['count'], len(self.confirmed) - 1)

    def test_unreadable_mirror_is_not_adopted(self):
        key = next(k for k, c in self.confirmed.items() if not c.get('passthrough'))
        (self.config.state_dir / 'test' / 'controls' / (key + '.json')).write_text('{broken')
        built, _ = self.rerun()
        self.assertEqual(built, [key])

    def test_fresh_start_ignores_the_mirrors(self):
        self.use(log_terminal_level='detailed', fresh_start=True)
        built, _ = self.rerun()
        self.assertEqual(sorted(built), sorted(self.confirmed))
        self.assertEqual(self.adopted(), [])
