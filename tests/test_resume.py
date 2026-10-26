import json
import unittest
from dataclasses import replace
from unittest.mock import patch

from sambinha.archives import compress
from sambinha.errors import BackupError
from sambinha.storage import Storage
from tests.helpers import REAL_TOOLS, Sandbox, tick


@unittest.skipUnless(REAL_TOOLS, 'rclone and 7z required')
class ResumeTests(Sandbox):
    """Interrupted batch backups resume from state/<job>/uploads.json."""

    def setUp(self):
        super().setUp()
        # 10-byte batches of 4-byte files: two per batch, so six files make three batches.
        job = replace(self.config.jobs[0], logic='batch', max_megabytes=10 / 1024 ** 2, batch_workers=1)
        self.config = replace(self.config, jobs=(job,))
        for name in 'abcdef':
            self.file(name, name.encode() * 4)
        self.journal_path = self.config.state_dir / 'test' / 'uploads.json'

    def fail_second_archive(self):
        real, archives = Storage.put, []

        def put(storage, source, key, **kwargs):
            if not key.endswith('.json') and not key.startswith('.sambinha/'):
                archives.append(key)
                if len(archives) == 2:
                    raise BackupError('falha simulada')
            return real(storage, source, key, **kwargs)
        return patch.object(Storage, 'put', put)

    def interrupted_run(self):
        with self.fail_second_archive():
            self.assertEqual(self.execute(), 1)
        return json.loads(self.journal_path.read_text())['entries']

    def test_failed_second_upload_resumes_without_recompressing_or_reuploading_the_first(self):
        entries = self.interrupted_run()
        self.assertEqual(len(entries), 1)
        done = next(iter(entries))
        self.assertFalse((self.config.state_dir / 'test' / 'state.json').exists())
        puts, compressed = [], []
        real_put, real_compress = Storage.put, compress

        def put(storage, source, key, **kwargs):
            puts.append(key)
            return real_put(storage, source, key, **kwargs)

        def counting_compress(worker, item, temp):
            compressed.append(item.key)
            return real_compress(worker, item, temp)
        with patch.object(Storage, 'put', put), patch('sambinha.strategies.batch.compress', counting_compress):
            self.assertEqual(self.execute(now=2000), 0, self.output.getvalue())
        self.assertEqual(len(compressed), 2)
        self.assertNotIn(done, compressed)
        self.assertNotIn(done, puts)
        self.assertIn(done, self.state()['active'])
        self.assertEqual(len(self.state()['active']), 3)
        self.assertTrue(any(e['event'] == 'journal_reused' and e['archive'] == done for e in self.events))
        self.assertFalse(self.journal_path.exists())
        self.assertEqual(len(list(self.destination.rglob('*.7z'))), 3)

    def test_journal_entry_is_discarded_when_a_member_changed(self):
        entries = self.interrupted_run()
        done = next(iter(entries))
        tick()
        self.file(entries[done]['files'][0]['path'], b'changed!')
        puts = []
        real_put = Storage.put

        def put(storage, source, key, **kwargs):
            puts.append(key)
            return real_put(storage, source, key, **kwargs)
        with patch.object(Storage, 'put', put):
            self.assertEqual(self.execute(now=2000), 0, self.output.getvalue())
        discarded = [e for e in self.events if e['event'] == 'journal_entry_discarded']
        self.assertEqual([(e['archive'], e['reason']) for e in discarded], [(done, 'changed')])
        self.assertNotIn(done, self.state()['active'])
        self.assertGreaterEqual(len([k for k in puts if k.endswith('.7z')]), 3)
        self.assertNotIn(done, puts)

    def test_entry_is_discarded_when_the_object_vanished_or_changed_size(self):
        entries = self.interrupted_run()
        done = next(iter(entries))
        (self.destination / done).write_bytes(b'truncated')
        self.assertEqual(self.execute(now=2000), 0, self.output.getvalue())
        self.assertTrue(any(e['event'] == 'journal_entry_discarded' and e['reason'] == 'missing'
                            for e in self.events))
        self.assertEqual(self.state()['active'][done]['archive_size'], (self.destination / done).stat().st_size)

    def test_dry_run_never_writes_the_journal(self):
        self.assertEqual(self.execute(config=replace(self.config, dry_run=True)), 0, self.output.getvalue())
        self.assertFalse(self.journal_path.exists())
        self.interrupted_run()
        before = self.journal_path.read_bytes()
        self.assertEqual(self.execute(config=replace(self.config, dry_run=True), now=2000), 0)
        self.assertEqual(self.journal_path.read_bytes(), before)
        self.assertTrue(any(e['event'] == 'journal_reused' for e in self.events))

    def test_corrupt_journal_is_ignored(self):
        self.journal_path.parent.mkdir(parents=True)
        self.journal_path.write_text('{not json')
        self.assertEqual(self.execute(), 0, self.output.getvalue())
        self.assertFalse(self.journal_path.exists())
