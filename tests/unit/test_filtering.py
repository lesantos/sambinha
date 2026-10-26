import json
import unittest
from pathlib import Path

from sambinha.filtering import path_groups, retired_paths


class GroupingTests(unittest.TestCase):
    def test_file_directory_replacements_are_separated_without_losing_names(self):
        groups = path_groups(['a', 'a/b', 'a/b/c', 'other', 'a/b'])
        self.assertEqual(set().union(*groups), {'a', 'a/b', 'a/b/c', 'other'})
        self.assertEqual(len(groups), 3)

    def test_legacy_retired_entry_with_missing_mirror_falls_back_to_key_path(self):
        import tempfile
        with tempfile.TemporaryDirectory() as temp:
            controls = Path(temp) / 'controls'
            controls.mkdir()
            entry = {'key': 'batch-1.7z', 'control': 'batch-1.7z.json', 'expires': 0}
            self.assertEqual(retired_paths(entry, Path(temp)), ['batch-1.7z'])
            (controls / 'batch-1.7z.json').write_text(json.dumps({'files': [{'path': 'dir/a'}]}))
            self.assertEqual(retired_paths(entry, Path(temp)), ['dir/a'])
            self.assertEqual(retired_paths({**entry, 'files': ['b']}, Path(temp)), ['b'])
