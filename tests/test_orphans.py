import os
import time
import unittest
from dataclasses import replace

from tests.helpers import REAL_TOOLS, Sandbox, tick


@unittest.skipUnless(REAL_TOOLS, 'rclone and 7z required')
class OrphanTests(Sandbox):
    """Unreferenced destination objects are reported after a commit and pruned only on request."""
    ORPHAN = 'batch-0123456789abcdef01234567.7z'
    GENERATION = '.sambinha/indexes/00000000000000000001-' + '0' * 32 + '.tar.gz'

    def setUp(self):
        super().setUp()
        job = replace(self.config.jobs[0], logic='batch', deletion_min_files=1)
        self.config = replace(self.config, jobs=(job,))
        self.file('a', b'aaaa')
        self.assertEqual(self.execute(), 0)

    def plant(self, key, content=b'x' * 10, age_days=0):
        path = self.destination / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        stamp = time.time() - age_days * 86400
        os.utime(path, (stamp, stamp))
        return path

    def commit(self, name, **kwargs):
        tick()
        self.file(name, name.encode() * 4)
        return self.execute(now=2000 + len(name), **kwargs)

    def named(self, event):
        return [e for e in self.events if e['event'] == event]

    def test_report_counts_unreferenced_archives_and_uncommitted_generations(self):
        self.plant(self.ORPHAN, b'x' * 10)
        self.plant(self.ORPHAN + '.json', b'{}')
        self.plant(self.GENERATION, b'y' * 7)
        self.plant('.sambinha/history/run/kept.bin', b'h')
        self.plant('notes.txt', b'user file, not a batch artifact')
        self.assertEqual(self.commit('b'), 0, self.output.getvalue())
        (orphans,) = self.named('orphans')
        self.assertEqual((orphans['count'], orphans['bytes']), (3, 19))
        self.assertEqual(sorted(orphans['examples']), sorted([self.ORPHAN, self.ORPHAN + '.json', self.GENERATION]))
        self.assertIsNotNone(orphans['oldest_age_days'])
        self.assertTrue((self.destination / self.ORPHAN).exists())

    def test_zero_orphans_emit_nothing(self):
        self.assertEqual(self.commit('b'), 0)
        self.assertEqual(self.named('orphans'), [])

    def test_prune_removes_only_old_orphans_and_never_history(self):
        old = self.plant(self.ORPHAN, age_days=3)
        old_generation = self.plant(self.GENERATION, age_days=3)
        young = self.plant('batch-fedcba9876543210fedcba98.7z', age_days=0)
        history = self.plant('.sambinha/history/run/kept.bin', age_days=30)
        self.assertEqual(self.commit('b', prune_orphans=True), 0, self.output.getvalue())
        self.assertFalse(old.exists() or old_generation.exists())
        self.assertTrue(young.exists() and history.exists())
        self.assertEqual(sorted(e['path'] for e in self.named('orphan_removed')),
                         sorted([self.ORPHAN, self.GENERATION]))
        self.assertEqual(self.named('orphans')[0]['count'], 3)

    def test_orphans_are_kept_without_the_flag(self):
        old = self.plant(self.ORPHAN, age_days=3)
        self.assertEqual(self.commit('b'), 0)
        self.assertTrue(old.exists())
        self.assertEqual(self.named('orphan_removed'), [])

    def test_prune_runs_on_an_unchanged_backup(self):
        old = self.plant(self.ORPHAN, age_days=3)
        self.assertEqual(self.execute(now=3000, prune_orphans=True), 0, self.output.getvalue())
        self.assertFalse(old.exists())

    def test_prune_respects_the_mass_deletion_guard(self):
        job = replace(self.config.jobs[0], max_delete_percent=10)
        config = replace(self.config, jobs=(job,))
        olds = [self.plant(f'batch-{i:024x}.7z', age_days=3) for i in range(2)]
        self.assertEqual(self.commit('b', config=config, prune_orphans=True), 1)
        self.assertTrue(all(p.exists() for p in olds))
        self.assertTrue(self.named('mass_deletion_blocked'))
        self.assertEqual(self.commit('c', config=config, prune_orphans=True, allow_mass_deletion=True), 0,
                         self.output.getvalue())
        self.assertFalse(any(p.exists() for p in olds))

    def test_dry_run_never_prunes(self):
        old = self.plant(self.ORPHAN, age_days=3)
        self.assertEqual(self.execute(config=replace(self.config, dry_run=True), prune_orphans=True), 0)
        self.assertTrue(old.exists())
        self.assertEqual(self.named('orphans'), [])
