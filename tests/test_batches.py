from dataclasses import replace
import unittest

from sambinha.batches import Batch, plan
from sambinha.inventory import File


def file(path, size=10, mtime_ns=100):
    return File(path, size, "a" * 64, "b" * 32, mtime_ns, 0o640)


class BatchPlannerTests(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(plan([], 100000, 1000), [])

    def test_collect_multiple_directories_until_limit(self):
        batches = plan([file("a/a"), file("b/b"), file("b/c"), file("c/d")], 100000, 3)
        self.assertEqual([len(b.files) for b in batches], [2, 2])
        self.assertTrue(all("/" not in b.key for b in batches))

    def test_common_ancestor(self):
        batches = plan([file("tree/a/a"), file("tree/b/b")], 100000, 1000)
        self.assertTrue(batches[0].key.startswith("tree/batch-"))

    def test_maximum_count(self):
        batches = plan([file(str(i)) for i in range(7)], 100000, 3)
        self.assertEqual([len(b.files) for b in batches], [3, 2, 2])

    def test_exact_byte_boundary(self):
        batches = plan([file("a", 50000), file("b", 50000)], 100000, 1000)
        self.assertEqual(len(batches), 1)
        self.assertEqual(sum(f.size for f in batches[0].files), 100000)

    def test_oversized_individual_preserves_basename(self):
        batch = plan([file("sub/big.txt", 100001)], 100000, 1000)[0]
        self.assertTrue(batch.key.startswith("sub/.versions/"))
        self.assertTrue(batch.key.endswith("/big.7z"))

    def test_singleton_when_no_pair_fits(self):
        batches = plan([file("a", 60000), file("b", 60000)], 100000, 2)
        self.assertEqual([len(b.files) for b in batches], [1, 1])

    def test_zero_byte_files_and_unicode(self):
        batches = plan([file("ação/@a", 0), file("ação/-b", 0)], 1, 2)
        self.assertEqual(len(batches), 1)
        self.assertEqual(len(batches[0].files), 2)

    def test_same_stem_different_extensions_no_collision(self):
        batches = plan([file("a.txt", 60000), file("a.csv", 60000)], 100000, 2)
        self.assertEqual(len({b.key for b in batches}), 2)
        self.assertTrue(all(b.key.endswith("/a.7z") for b in batches))

    def test_deterministic_and_changes_modify_key(self):
        items = [file("b"), file("a")]
        self.assertEqual(plan(items, 100000, 2), plan(list(reversed(items)), 100000, 2))
        self.assertNotEqual(plan(items, 100000, 2)[0].key,
                            plan([items[0], replace(items[1], sha256="c" * 64)], 100000, 2)[0].key)

    def test_oldest_first_within_each_directory_and_path_breaks_ties(self):
        items = [file("b/old", mtime_ns=1), file("a/a", mtime_ns=300),
                 file("a/z", mtime_ns=100), file("a/c", mtime_ns=200),
                 file("a/b", mtime_ns=200)]
        batches = plan(items, 100000, 2)
        self.assertEqual([f.path for b in batches for f in b.files],
                         ["a/z", "a/b", "a/c", "a/a", "b/old"])
        self.assertEqual(batches, plan(list(reversed(items)), 100000, 2))

    def test_inserting_old_file_preserves_every_existing_lot(self):
        items = [file(f"dir/{i}", mtime_ns=i + 100) for i in range(6)]
        previous = plan(items, 100000, 2)
        controls = {b.key: b.control() for b in previous}
        for additions in ([file("dir/new", mtime_ns=1)],
                          [file("dir/new", mtime_ns=1), file("dir/newer", mtime_ns=999)]):
            with self.subTest(additions=additions):
                batches = plan(additions + items, 100000, 2, previous=controls)
                self.assertTrue(all(b in batches for b in previous))
                self.assertCountEqual([f for b in batches for f in b.files], items + additions)

    def test_edit_or_removal_only_replaces_affected_lot(self):
        items = [file(f"dir/{i}", mtime_ns=i + 100) for i in range(8)]
        previous = plan(items, 100000, 2)
        controls = {b.key: b.control() for b in previous}
        for changed in (items[:2] + [replace(items[2], sha256="c" * 64, mtime_ns=999)] + items[3:],
                        items[:2] + items[3:]):
            with self.subTest(changed=changed):
                batches = plan(changed, 100000, 2, previous=controls)
                self.assertEqual({b.key for b in previous} - {b.key for b in batches}, {previous[1].key})
                self.assertCountEqual([f for b in batches for f in b.files], changed)

    def test_legacy_member_order_and_keys_are_preserved(self):
        items = [file("dir/a", mtime_ns=300), file("dir/z", mtime_ns=100)]
        legacy = Batch("dir/batch-legacy.7z", tuple(items))
        self.assertEqual(plan(list(reversed(items)), 100000, 2,
                              previous={legacy.key: legacy.control()}), [legacy])

    def test_changed_limits_and_format_only_replace_incompatible_lots(self):
        items = [file("a/one", 40), file("a/two", 40)] + [file(f"b/{i}") for i in range(3)]
        previous = plan(items, 100000, 3)
        controls = {b.key: b.control() for b in previous}
        for max_bytes, max_files, extension in ((50, 3, "7z"), (100000, 2, "7z"),
                                                 (100000, 3, "zip")):
            with self.subTest(max_bytes=max_bytes, max_files=max_files, extension=extension):
                batches = plan(items, max_bytes, max_files, extension, previous=controls)
                self.assertCountEqual([f for b in batches for f in b.files], items)
                for batch in batches:
                    self.assertLessEqual(len(batch.files), max_files)
                    self.assertTrue(sum(f.size for f in batch.files) <= max_bytes or len(batch.files) == 1)
                    self.assertTrue(batch.key.endswith("." + extension))

    def test_replanned_members_are_ordered_by_modification_time(self):
        items = [file(f"dir/{i}", mtime_ns=i + 100) for i in range(4)]
        previous = plan(items, 100000, 2)
        changed = [replace(items[0], mtime_ns=999), *items[1:], file("dir/new", mtime_ns=1)]
        batches = plan(changed, 100000, 3, previous={b.key: b.control() for b in previous})
        new = [b for b in batches if b.key not in {old.key for old in previous}]
        self.assertEqual([f.path for b in new for f in b.files], ["dir/new", "dir/1", "dir/0"])

    def test_packing_completeness_across_many_small_limits(self):
        for max_bytes in (1, 10, 50, 100000):
            for max_files in (2, 3, 10):
                with self.subTest(max_bytes=max_bytes, max_files=max_files):
                    items = [file(f"dir{i % 3}/f{i}", i * 3) for i in range(15)]
                    batches = plan(items, max_bytes, max_files)
                    actual = [f.path for batch in batches for f in batch.files]
                    self.assertCountEqual(actual, [f.path for f in items])
                    self.assertEqual(len(actual), len(set(actual)))
                    for batch in batches:
                        self.assertLessEqual(len(batch.files), max_files)
                        self.assertTrue(sum(f.size for f in batch.files) <= max_bytes or
                                        len(batch.files) == 1 and batch.files[0].size > max_bytes)
