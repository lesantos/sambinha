from dataclasses import replace
import unittest

from sambinha.batches import Batch, individual, plan
from sambinha.inventory import File


def file(path, size=10, mtime_ns=100):
    return File(path, size, 'a' * 64, 'b' * 32, mtime_ns, 0o640)


class BatchPlannerTests(unittest.TestCase):
    def controls(self, batches):
        return {b.key: b.control() for b in batches}

    def test_empty(self):
        self.assertEqual(plan([], 100), [])

    def test_packs_across_directories_and_types_without_count_cap(self):
        items = [file(f'dir{i % 3}/{i}.jpg' if i % 2 else f'{i}.txt', 1) for i in range(2500)]
        batches = plan(items, 3000)
        self.assertEqual(len(batches), 1)
        self.assertEqual(list(batches[0].files), items)

    def test_common_ancestor(self):
        batch = plan([file('tree/a/a'), file('tree/b/b')], 100)[0]
        self.assertTrue(batch.key.startswith('tree/batch-'))

    def test_exact_byte_boundary(self):
        batches = plan([file('a', 50), file('b', 50)], 100)
        self.assertEqual(len(batches), 1)
        self.assertEqual(sum(f.size for f in batches[0].files), 100)

    def test_diverts_large_file_without_closing_normal_batch(self):
        items = [file('a', 40), file('big', 200), file('c', 60)]
        batches = plan(items, 100)
        normal = next(b for b in batches if not b.individual)
        self.assertEqual([f.path for f in normal.files], ['a', 'c'])
        self.assertEqual(len(batches), 2)
        self.assertTrue(next(b for b in batches if b.individual).key.endswith('/big.7z'))

    def test_individual_threshold_is_inclusive_and_configurable(self):
        batches = plan([file('a', 49), file('b', 50), file('c', 51)], 100, individual_percent=50)
        self.assertEqual([b.individual for b in batches], [False, True, True])
        self.assertEqual([b.files[0].path for b in batches], ['a', 'b', 'c'])

    def test_zero_size_files_are_not_lost(self):
        items = [file('ação/@a', 0), file('ação/-b', 0)]
        self.assertEqual(list(plan(items, 1)[0].files), items)

    def test_same_stem_singletons_have_distinct_revision_keys(self):
        batches = plan([file('a.txt', 60), file('a.csv', 60)], 100)
        self.assertEqual(len({b.key for b in batches}), 2)

    def test_keys_deterministic_for_same_order_and_metadata(self):
        items = [file('b'), file('a')]
        self.assertEqual(plan(items, 100), plan(items, 100))
        self.assertNotEqual(plan(items, 100)[0].key,
                            plan([items[0], replace(items[1], sha256='c' * 64)], 100)[0].key)

    def test_modification_preserves_group_and_member_order_despite_growth(self):
        items = [file('a', 40, 1), file('b', 40, 2), file('c', 30, 3)]
        before = plan(items, 100)
        changed = [items[2], replace(items[0], size=400, mtime_ns=999), items[1]]
        after = plan(changed, 100, previous=self.controls(before))
        group = next(b for b in after if b.batch_id == before[0].batch_id)
        self.assertEqual([f.path for f in group.files], ['a', 'b'])
        self.assertEqual(sum(f.size for f in group.files), 440)
        self.assertIn(before[1], after)
        self.assertEqual(plan(changed, 10, previous=self.controls(after)), after)

    def test_removal_preserves_other_groups_and_empty_group_disappears(self):
        items = [file('a', 40), file('b', 40), file('c', 30)]
        before = plan(items, 100)
        after = plan(items[1:], 100, previous=self.controls(before))
        self.assertEqual(after[0].batch_id, before[0].batch_id)
        self.assertEqual([f.path for f in after[0].files], ['b'])
        self.assertIn(before[1], after)
        self.assertEqual(plan(items[2:], 100, previous=self.controls(before)), [before[1]])

    def test_new_files_do_not_move_or_reopen_confirmed_groups(self):
        old = plan([file('a'), file('b')], 100)
        after = plan([file('new', mtime_ns=1), file('b'), file('a')], 100, previous=self.controls(old))
        self.assertEqual(after[0], old[0])
        self.assertEqual([f.path for f in after[1].files], ['new'])

    def test_dedicated_group_remains_dedicated_after_shrinking(self):
        old = plan([file('big', 200)], 100)
        after = plan([file('big', 1), file('new', 2)], 100, previous=self.controls(old))
        self.assertTrue(after[0].individual)
        self.assertEqual(len(after[0].files), 1)
        self.assertEqual(after[0].batch_id, old[0].batch_id)

    def test_legacy_keys_and_member_order_are_reused(self):
        items = [file('dir/a', mtime_ns=300), file('dir/z', mtime_ns=100)]
        legacy = Batch('dir/batch-legacy.7z', tuple(items))
        self.assertEqual(plan(list(reversed(items)), 100, previous=self.controls([legacy])), [legacy])
        self.assertTrue(individual(Batch('old.7z', (items[0],)).control()))

    def test_changing_format_rebuilds_but_keeps_members(self):
        before = plan([file('a'), file('b')], 100)
        after = plan([file('b'), file('a')], 100, extension='zip', previous=self.controls(before))
        self.assertTrue(after[0].key.endswith('.zip'))
        self.assertEqual(before[0].files, after[0].files)
        self.assertEqual(before[0].batch_id, after[0].batch_id)

    def test_manual_rebalance_alters_only_oversized_nonindividual_groups(self):
        healthy = plan([file('h1', 40), file('h2', 40)], 100)[0]
        large = plan([file('big', 300)], 100)[0]
        growing = plan([file('a', 40), file('b', 40)], 100)[0]
        changed = [file('a', 70), file('b', 70), *healthy.files, *large.files]
        oversized = plan(changed, 100, previous=self.controls([growing, healthy, large]))
        after = plan(changed, 100, previous=self.controls(oversized), rebalance=True)
        self.assertIn(healthy, after)
        self.assertIn(large, after)
        self.assertTrue(all(b.individual or sum(f.size for f in b.files) <= 100 for b in after))
        self.assertCountEqual([f for b in after for f in b.files], changed)
        self.assertEqual(len(set(self.controls(oversized)) - set(self.controls(after))), 1)

    def test_manual_rebalance_diverts_member_that_grew_above_limit(self):
        old = plan([file('a', 20), file('b', 20), file('c', 20)], 100)
        changed = [file('a', 40), file('b', 300), file('c', 60)]
        after = plan(changed, 100, previous=self.controls(old), rebalance=True)
        self.assertEqual(len(after), 2)
        self.assertEqual([f.path for b in after if not b.individual for f in b.files], ['a', 'c'])

    def test_completeness_across_many_sizes_and_thresholds(self):
        for limit in (1, 10, 50, 1000):
            for percent in (20, 50, 100):
                items = [file(f'dir{i % 3}/f{i}', i * 3) for i in range(20)]
                batches = plan(items, limit, individual_percent=percent)
                paths = [f.path for b in batches for f in b.files]
                self.assertCountEqual(paths, [f.path for f in items])
                self.assertEqual(len(paths), len(set(paths)))
                self.assertTrue(all(b.individual or sum(f.size for f in b.files) <= limit for b in batches))
