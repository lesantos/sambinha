import unittest
from dataclasses import replace
from unittest.mock import patch

from sambinha.filtering import Selection
from tests.helpers import REAL_TOOLS, Sandbox


def tree_spy(trees):
    """Record the names of the dummy tree handed to rclone for classification."""
    real = Selection.listing

    def listing(selection, root, **kwargs):
        trees.append(sorted(p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()))
        return real(selection, root, **kwargs)
    return patch.object(Selection, 'listing', listing)


class IncludePathsTests(Sandbox):
    def selection(self):
        rules = self.root / 'rules'
        rules.write_text('*.tmp\n')
        return Selection(rules, self.config, None)

    def test_current_inventory_paths_skip_the_dummy_tree(self):
        selection, trees = self.selection(), []

        def fake(root, **kwargs):
            trees.append(sorted(p.name for p in root.rglob('*') if p.is_file()))
            return [{'Path': 'gone'}]
        with patch.object(selection, 'listing', side_effect=fake):
            included = selection.include_paths(['kept', 'kept2', 'gone', 'gone.tmp'], current={'kept', 'kept2'})
        self.assertEqual(included, {'kept', 'kept2', 'gone'})
        self.assertEqual(trees, [['gone', 'gone.tmp']])

    def test_only_current_paths_never_builds_a_tree_and_cache_is_per_unknown_set(self):
        selection = self.selection()
        with patch.object(selection, 'listing', return_value=[{'Path': 'x'}]) as listing:
            self.assertEqual(selection.include_paths(['a', 'b'], current=['a', 'b', 'c']), {'a', 'b'})
            listing.assert_not_called()
            self.assertEqual(selection.include_paths(['x', 'a'], current=['a']), {'x', 'a'})
            self.assertEqual(selection.include_paths(['a', 'x'], current=['a']), {'x', 'a'})
        self.assertEqual(listing.call_count, 1)


@unittest.skipUnless(REAL_TOOLS, 'rclone and 7z required')
class FilterReclassificationTests(Sandbox):
    def setUp(self):
        super().setUp()
        self.rules = self.root / 'rules.txt'
        self.rules.write_text('*.log\n')
        job = replace(self.config.jobs[0], logic='batch', exclude_files=self.rules, max_megabytes=100 / 1024)
        self.config = replace(self.config, jobs=(job,))
        for name in ('a.txt', 'b.txt', 'c.txt'):
            self.file(name, name.encode())

    def test_unchanged_files_are_not_reclassified(self):
        self.assertEqual(self.execute(now=1000), 0, self.output.getvalue())
        trees = []
        with tree_spy(trees):
            self.assertEqual(self.execute(now=1001), 0, self.output.getvalue())
        self.assertEqual(trees, [])

    def test_removed_file_is_the_only_one_classified_and_stays_removed(self):
        self.assertEqual(self.execute(now=1000), 0, self.output.getvalue())
        (self.source / 'b.txt').unlink()
        trees = []
        with tree_spy(trees):
            self.assertEqual(self.execute(now=1001, allow_mass_deletion=True), 0, self.output.getvalue())
        self.assertTrue(trees and all(tree == ['b.txt'] for tree in trees), trees)
        self.assertEqual(self.state()['excluded'], {})
        self.assertEqual(len(self.state()['retired']), 1)
        self.assertEqual(next(e for e in self.events if e['event'] == 'summary')['removed_files'], 1)

    def test_file_dropped_by_new_rules_is_excluded_not_removed(self):
        self.file('skip.tmp', b'tmp')
        self.assertEqual(self.execute(now=1000), 0, self.output.getvalue())
        old = set(self.state()['active'])
        self.rules.write_text('*.log\n*.tmp\n')  # frozen per run: applies from this run on
        trees = []
        with tree_spy(trees):
            self.assertEqual(self.execute(now=1001), 0, self.output.getvalue())
        # Only the path missing from the new inventory goes through the dummy tree.
        self.assertTrue(trees and all(tree == ['skip.tmp'] for tree in trees), trees)
        state = self.state()
        self.assertTrue(any(e['event'] == 'excluded_batch' for e in self.events))
        self.assertEqual(set(state['excluded']), old)
        self.assertEqual(state['retired'], [])
        self.assertEqual(next(e for e in self.events if e['event'] == 'summary')['removed_files'], 0)
        self.assertTrue(all((self.destination / key).exists() for key in old))
