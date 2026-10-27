import json
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

    def test_unreadable_mirror_falls_back_to_the_control_stored_at_the_destination(self):
        key = next(k for k, c in self.confirmed.items() if not c.get('passthrough'))
        (self.config.state_dir / 'test' / 'controls' / (key + '.json')).write_text('{broken')
        built, _ = self.rerun()
        self.assertEqual(built, [])
        self.assertEqual(self.adopted()[0]['from_destination'], 1)

    def test_without_mirrors_the_controls_stored_at_the_destination_are_adopted_and_mirrored_again(self):
        shutil.rmtree(self.config.state_dir / 'test' / 'controls')
        built, puts = self.rerun()
        self.assertEqual(built, [])
        self.assertEqual([key for key in puts if not key.startswith('.sambinha/')], [])
        event = self.adopted()[0]
        self.assertEqual((event['count'], event['from_destination'], event['rebuilt']),
                         (len(self.confirmed), len(self.confirmed), 0))
        self.assertEqual(set(self.state()['active']), set(self.confirmed))
        for key in self.confirmed:
            self.assertTrue((self.config.state_dir / 'test' / 'controls' / (key + '.json')).is_file())

    def test_stored_control_that_does_not_match_or_cannot_be_read_is_not_adopted(self):
        shutil.rmtree(self.config.state_dir / 'test' / 'controls')
        keys = [k for k, c in self.confirmed.items() if not c.get('passthrough')]
        (self.destination / (keys[0] + '.json')).write_text('{broken')
        stale = json.loads((self.destination / (keys[1] + '.json')).read_text())
        stale['files'][0]['size'] += 1
        (self.destination / (keys[1] + '.json')).write_text(json.dumps(stale))
        built, _ = self.rerun()
        self.assertEqual(sorted(built), sorted(keys[:2]))
        self.assertEqual(self.adopted()[0]['count'], len(self.confirmed) - 2)

    def test_stored_control_without_its_archive_is_not_adopted(self):
        shutil.rmtree(self.config.state_dir / 'test' / 'controls')
        key = next(k for k, c in self.confirmed.items() if not c.get('passthrough'))
        (self.destination / key).unlink()
        built, _ = self.rerun()
        self.assertEqual(built, [key])

    def test_fresh_start_ignores_the_mirrors(self):
        self.use(log_terminal_level='detailed', fresh_start=True)
        built, _ = self.rerun()
        self.assertEqual(sorted(built), sorted(self.confirmed))
        self.assertEqual(self.adopted(), [])
