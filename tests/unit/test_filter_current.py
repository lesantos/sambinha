from unittest.mock import patch

from sambinha.filtering import Selection
from tests.helpers import Sandbox


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
