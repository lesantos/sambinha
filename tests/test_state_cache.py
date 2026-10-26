import io
import json
import os
import tarfile
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from unittest.mock import patch

from sambinha import preflight
from sambinha.config_validation import validate
from sambinha.inventory import fingerprint
from sambinha.recovery import recover
from sambinha.strategies.batch import snapshot
from tests.helpers import REAL_TOOLS, Sandbox, contents, tick

DAY = 86400


class SnapshotTests(unittest.TestCase):
    def test_mutations_after_the_snapshot_do_not_leak(self):
        control = {'files': [{'path': 'a'}]}
        state = {'active': {'k': control}, 'excluded': {'x': control}, 'origins': [{'host': 'h'}],
                 'retired': [{'key': 'r', 'control': 'r.json', 'files': ['a']}], 'last_full_scan': 1}
        copy = snapshot(state)
        self.assertEqual(copy, state)
        copy['active']['new'] = control
        copy['excluded'].pop('x')
        copy['retired'][0].pop('control')
        copy['retired'].append({'key': 'q'})
        copy['origins'][0]['host'] = 'other'
        copy['last_full_scan'] = 2
        self.assertEqual(state, {'active': {'k': control}, 'excluded': {'x': control}, 'origins': [{'host': 'h'}],
                                 'retired': [{'key': 'r', 'control': 'r.json', 'files': ['a']}],
                                 'last_full_scan': 1})
        state['retired'][0]['files'] = ['z']  # and the other way around
        self.assertEqual(copy['retired'][0]['files'], ['a'])


@unittest.skipUnless(REAL_TOOLS, 'rclone and 7z required')
class FingerprintCacheTests(Sandbox):
    def setUp(self):
        super().setUp()
        self.configure('hash')
        for name in ('a', 'b', 'c'):
            self.file(name, name.encode() * 4)
        self.directory = self.config.state_dir / 'test'
        self.cache_path = self.directory / 'fingerprints.json'

    def configure(self, mode, **options):
        job = replace(self.config.jobs[0], logic='batch', verification_mode=mode, **options)
        self.config = replace(self.config, jobs=(job,))

    def hashed(self, now, **kwargs):
        """Files hashed by the inventory scan (verify/archives hash again on their own)."""
        calls = []
        real_scan = preflight.scan

        def scanning(*args, **options):
            with patch('sambinha.inventory.fingerprint', wraps=fingerprint) as hashing:
                try:
                    return real_scan(*args, **options)
                finally:
                    calls.append(hashing.call_count)
        with patch('sambinha.preflight.scan', scanning):
            code = self.execute(now=now, **kwargs)
        self.assertEqual(code, 0, self.output.getvalue())
        return sum(calls)

    def published(self):
        with tarfile.open(self.destination / '.sambinha/control.tar.gz') as tar:
            return json.load(tar.extractfile('state.json'))

    def restore(self, name='restored'):
        output = self.root / name
        with redirect_stdout(io.StringIO()):
            recover(validate(self.config), 'restore', output=output)
        return contents(output)

    def test_simple_mode_persists_no_fingerprints_anywhere(self):
        self.configure('simple')
        self.directory.mkdir(parents=True)
        self.cache_path.write_text('{}')  # leftover from an earlier hash-mode run
        self.assertEqual(self.execute(now=1000), 0, self.output.getvalue())
        self.assertNotIn('fingerprints', self.state())
        self.assertNotIn('fingerprints', self.published())
        self.assertFalse(self.cache_path.exists())

    def test_hash_mode_keeps_cache_in_private_file_reuses_it_and_rehashes_changed_files(self):
        self.assertEqual(self.hashed(1000), 3)
        self.assertNotIn('fingerprints', self.state())
        self.assertNotIn('fingerprints', self.published())
        cache = json.loads(self.cache_path.read_text())
        self.assertEqual(set(cache), {'a', 'b', 'c'})
        self.assertEqual(self.cache_path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.directory.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.hashed(1001), 0)
        tick()
        (self.source / 'b').write_bytes(b'changed')
        self.assertEqual(self.hashed(1002), 1)
        self.assertNotEqual(json.loads(self.cache_path.read_text())['b'], cache['b'])

    def test_missing_corrupt_or_unreadable_cache_degrades_to_a_full_rehash_and_is_rewritten(self):
        self.assertEqual(self.hashed(1000), 3)
        good = json.loads(self.cache_path.read_text())
        now = 1000
        for label, damage in (('missing', lambda: self.cache_path.unlink()),
                              ('corrupt', lambda: self.cache_path.write_text('{not json')),
                              ('invalid', lambda: self.cache_path.write_text('{"a": {"signature": 1}}')),
                              ('not a mapping', lambda: self.cache_path.write_text('[1]')),
                              ('unreadable', lambda: (self.cache_path.write_text('{}'), self.cache_path.chmod(0)))):
            with self.subTest(label):
                damage()
                now += 1
                if label == 'unreadable' and os.access(self.cache_path, os.R_OK):
                    continue  # running as root
                self.assertEqual(self.hashed(now), 3)
                self.assertEqual(json.loads(self.cache_path.read_text()).keys(), good.keys())
                self.assertEqual(self.hashed(now + 0.5), 0)

    def test_legacy_embedded_fingerprints_are_used_once_then_migrated_out(self):
        self.assertEqual(self.hashed(1000), 3)
        generation = self.state()['generation']
        state = self.state()
        state['fingerprints'] = json.loads(self.cache_path.read_text())
        self.cache_path.unlink()
        (self.directory / 'state.json').write_text(json.dumps(state))
        self.assertEqual(self.hashed(1001), 0)  # embedded cache served this run
        after = self.state()
        self.assertNotIn('fingerprints', after)
        self.assertNotIn('fingerprints', self.published())
        self.assertNotEqual(after['generation'], generation)  # republished without it
        self.assertEqual(set(json.loads(self.cache_path.read_text())), {'a', 'b', 'c'})
        self.assertEqual(self.hashed(1002), 0)

    def test_inventory_changed_means_state_json_content_not_fingerprint_churn(self):
        self.assertEqual(self.hashed(1000), 3)
        generation, before = self.state()['generation'], self.cache_path.read_text()
        info = (self.source / 'a').stat()
        os.utime(self.source / 'a', ns=(info.st_atime_ns + 5, info.st_mtime_ns))  # ctime moves, metadata not
        self.assertEqual(self.hashed(1001), 1)
        self.assertNotEqual(self.cache_path.read_text(), before)  # cache rewritten on its own
        self.assertEqual(self.state()['generation'], generation)  # no republication
        # A new full scan moves last_full_scan, which state.json persists.
        self.configure('hash', fingerprint_full_scan_days=1)
        self.assertEqual(self.hashed(1001 + 2 * DAY), 3)
        self.assertEqual(self.state()['last_full_scan'], 1001 + 2 * DAY)

    def test_published_index_has_no_fingerprints_and_restore_works_from_it(self):
        self.assertEqual(self.hashed(1000), 3)
        self.assertNotIn('fingerprints', self.published())
        (self.directory / 'state.json').unlink()
        self.cache_path.unlink()
        self.assertEqual(self.restore(), {'a': b'aaaa', 'b': b'bbbb', 'c': b'cccc'})

    def test_bootstrap_from_an_older_index_embedding_fingerprints_keeps_working(self):
        import sambinha.index_generations as generations
        real = generations.atomic_json
        cache = {}

        def legacy(path, data, **kwargs):
            if path.name == 'state.json' and path.parent.name != 'test' and 'active' in data:
                data = {**data, 'fingerprints': cache}  # what <= 0.16 published
            return real(path, data, **kwargs)
        self.assertEqual(self.hashed(1000), 3)
        cache.update(json.loads(self.cache_path.read_text()))
        tick()
        (self.source / 'c').write_bytes(b'cc')
        with patch.object(generations, 'atomic_json', legacy):
            self.assertEqual(self.hashed(1001), 1)
        self.assertIn('fingerprints', self.published())
        (self.directory / 'state.json').unlink()  # lost state: bootstrap from the remote index
        self.cache_path.unlink()
        self.assertEqual(self.hashed(1002), 3)  # the publisher's cache is another machine's: rehash
        self.assertNotIn('fingerprints', self.state())
        self.assertNotIn('fingerprints', self.published())
        self.assertEqual(self.restore(), {'a': b'aaaa', 'b': b'bbbb', 'c': b'cc'})

    def test_fresh_start_removes_the_cache_file_and_rebuilds_it(self):
        self.assertEqual(self.hashed(1000), 3)
        self.assertTrue(self.cache_path.exists())
        self.assertEqual(self.hashed(1001, fresh_start=True), 3)
        (event,) = [e for e in self.events if e['event'] == 'fresh_start']
        self.assertIn('fingerprints', event['discarded'])
        self.assertTrue(self.cache_path.exists())  # rebuilt by the full scan
        self.configure('hash', dry_run=True)
        self.assertEqual(self.execute(now=1002, fresh_start=True), 0, self.output.getvalue())
        self.assertTrue(self.cache_path.exists())  # a dry run discards nothing on disk

    def test_state_json_shrinks_with_many_indexed_files(self):
        for index in range(2000):
            self.file(f'many/{index // 100}/file-{index}', b'x' * (index % 7 + 1))
        self.configure('hash', max_megabytes=1)
        self.assertEqual(self.hashed(1000), 2003)
        state = self.state()
        cache = json.loads(self.cache_path.read_text())
        legacy = len(json.dumps({**state, 'fingerprints': cache}, indent=2, sort_keys=True))
        size = (self.directory / 'state.json').stat().st_size
        self.assertLess(size, legacy / 2, (size, legacy))
        self.assertLess(len(json.dumps(self.published())), legacy / 2)
