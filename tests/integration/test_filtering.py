import json
import unittest
from dataclasses import replace
from unittest.mock import patch

from sambinha.config import load, with_job_overrides
from sambinha.filtering import Selection
from sambinha.process import Runner
from sambinha.retention import DAY
from tests.helpers import REAL_TOOLS, Sandbox, restore_batches


@unittest.skipUnless(REAL_TOOLS, 'rclone and 7z required')
class FilteringTests(Sandbox):
    def exclude(self, patterns, logic='simple'):
        path = self.root / 'exclude.txt'
        path.write_text(patterns)
        self.config = replace(self.config, jobs=(replace(self.config.jobs[0], logic=logic, exclude_files=path),))
        return path

    def test_native_patterns_rooted_paths_unicode_and_file_directory_replacement(self):
        path = self.exclude('# comment\n*.tmp\n/cache/**\n/only-root.txt\n*.{bak,old}\n')
        selected = Selection(path, self.config, Runner()).include_paths([
            'a.tmp', 'dir/a.tmp', 'cache/file', 'only-root.txt', 'dir/only-root.txt',
            'ação.txt', 'a.bak', 'node', 'node/child'])
        self.assertEqual(selected, {'dir/only-root.txt', 'ação.txt', 'node', 'node/child'})

    def test_simple_does_not_transfer_or_delete_excluded_destination_files(self):
        self.file('keep.txt', b'keep')
        excluded = self.file('skip.tmp', b'old')
        self.assertEqual(self.execute(), 0)
        excluded.write_bytes(b'new excluded')
        self.file('cache/private', b'not copied')
        self.exclude('*.tmp\n/cache/**\n')
        self.assertEqual(self.execute(), 0, self.output.getvalue())
        self.assertEqual((self.destination / 'skip.tmp').read_bytes(), b'old')
        self.assertFalse((self.destination / 'cache/private').exists())
        excluded.unlink()
        self.assertEqual(self.execute(), 0)
        self.assertEqual((self.destination / 'skip.tmp').read_bytes(), b'old')

    def test_excluded_files_are_not_hashed_and_included_empty_directories_remain(self):
        self.file('keep.txt')
        self.file('skip.tmp')
        (self.source / 'empty').mkdir()
        self.exclude('*.tmp\n')
        from unittest.mock import patch

        from sambinha.inventory import fingerprint
        visited = []
        def record(root, name):
            visited.append(name)
            return fingerprint(root, name)
        with patch('sambinha.inventory.fingerprint', record):
            self.assertEqual(self.execute(), 0)
        self.assertNotIn('skip.tmp', visited)
        self.assertTrue((self.destination / 'empty').is_dir())

    def test_expired_simple_history_is_removed_even_if_a_new_rule_excludes_its_path(self):
        target = self.file('file.tmp', b'old')
        self.assertEqual(self.execute(), 0)
        target.unlink()
        self.assertEqual(self.execute(now=2000), 0)
        history_root = self.destination / '.sambinha/history'
        histories = list(history_root.iterdir())
        self.assertEqual(len(histories), 1)
        self.exclude('*.tmp\n')
        self.assertEqual(self.execute(now=2000 + 10 * DAY), 0)
        self.assertFalse(histories[0].exists())

    def test_batch_exclusions_preserve_old_mixed_lot_and_restore_selected_files(self):
        self.file('keep.txt', b'keep')
        excluded = self.file('skip.tmp', b'excluded')
        self.config = replace(self.config, jobs=(replace(self.config.jobs[0], logic='batch'),))
        # Model an older mixed-profile lot, which must remain protected.
        with self.subTest(compression='uniform'):
            self.assertEqual(self.execute(), 0)
        old_keys = list(self.state()['active'])
        self.exclude('*.tmp\n', logic='batch')
        excluded.unlink()
        self.assertEqual(self.execute(now=2000), 0, self.output.getvalue())
        restored = restore_batches(self.state(), self.destination, self.root / 'restored')
        self.assertEqual(restored, {'keep.txt': b'keep'})
        self.assertTrue(all((self.destination / key).exists() for key in old_keys))
        self.assertEqual(set(self.state()['excluded']), set(old_keys))
        self.assertEqual(self.execute(now=2000 + 10 * DAY), 0)
        self.assertTrue(all((self.destination / key).exists() for key in old_keys))
        with __import__('tarfile').open(self.destination / '.sambinha/control.tar.gz') as tar:
            recovery = json.load(tar.extractfile('state.json'))
            self.assertEqual(recovery, self.state())

    def test_missing_exclude_file_fails_before_copy_and_notifies(self):
        self.file('file')
        self.config = replace(self.config, jobs=(replace(self.config.jobs[0], exclude_files=self.root / 'missing'),))
        self.assertEqual(self.execute(), 1)
        self.assertFalse(self.destination.exists())
        self.assertEqual(self.notifications[-1][1], 'fail')
        self.assertIn('exclude_files', self.notifications[-1][3])

    def test_config_resolves_exclude_file_and_cli_override(self):
        path = self.root / 'config.toml'
        path.write_text('[[jobs]]\nname="test"\nsource="source"\ndestination="destination"\nexclude_files="exclude.txt"\n')
        self.assertEqual(load(path).jobs[0].exclude_files, self.root / 'exclude.txt')
        self.assertIsNone(load(path, {'exclude_files': ''}).jobs[0].exclude_files)
        self.assertEqual(load(path, {'exclude_files': 'other.txt'}).jobs[0].exclude_files, self.root / 'other.txt')

    def test_simulation_selects_files_without_mutating_backup_or_state(self):
        from sambinha.process import Runner
        self.file('keep.txt')
        self.file('skip.tmp')
        self.exclude('*.tmp\n')
        calls = []
        original = Runner.run
        def record(runner, args, **kwargs):
            calls.append(args)
            return original(runner, args, **kwargs)
        with patch.object(Runner, 'run', record):
            self.assertEqual(self.execute(with_job_overrides(self.config, dry_run=True)), 0)
        self.assertEqual([e['event'] for e in self.events if e['event'] == 'sync_start'], ['sync_start'])
        sync = next(command for command in calls if command[1] == 'sync')
        self.assertIn('--dry-run', sync)
        self.assertFalse(self.destination.exists())
        self.assertFalse(self.config.state_dir.exists())

    def test_rules_changed_mid_run_only_affect_the_next_execution(self):
        from unittest.mock import patch

        from sambinha.storage import Storage
        self.file('keep.txt', b'keep')
        target = self.file('skip.tmp', b'old')
        self.assertEqual(self.execute(), 0)
        target.write_bytes(b'new')
        rules = self.exclude('*.tmp\n')
        original = Storage.sync_simple
        def change_rules(storage, *args, **kwargs):
            self.assertEqual(storage.job.exclude_files.read_text(), '*.tmp\n')
            rules.write_text('')
            return original(storage, *args, **kwargs)
        with patch.object(Storage, 'sync_simple', change_rules):
            self.assertEqual(self.execute(), 0, self.output.getvalue())
        self.assertEqual((self.destination / 'skip.tmp').read_bytes(), b'old')
        self.assertEqual(self.execute(), 0)
        self.assertEqual((self.destination / 'skip.tmp').read_bytes(), b'new')

    def test_source_listing_reuses_one_server_with_full_relative_paths(self):
        rules = self.exclude('*.tmp\n', logic='batch')
        selection = Selection(rules, self.config, Runner())
        root = self.source
        entries = {
            '': [{'Path': 'nested', 'IsDir': True}, {'Path': 'root.txt', 'IsDir': False}],
            'nested': [{'Path': 'nested/leaf', 'IsDir': True}],
            'nested/leaf': [{'Path': 'nested/leaf/keep.txt', 'IsDir': False}],
        }
        calls = []
        def listing(relative):
            calls.append(relative)
            return entries[relative]
        with patch('sambinha.filtering.Discovery') as server, \
             patch.object(selection, 'include_paths', side_effect=AssertionError('dummy tree')):
            server.return_value.__enter__.return_value.listing.side_effect = listing
            files, directories = selection.source(root)
        self.assertEqual(files, {'root.txt', 'nested/leaf/keep.txt'})
        self.assertEqual(directories, {'nested', 'nested/leaf'})
        self.assertEqual(calls, ['', 'nested', 'nested/leaf'])
        server.assert_called_once_with(self.config, selection.runner, root, rules)

    def test_invalid_native_pattern_stops_before_copy(self):
        self.file('file')
        self.exclude('[\n')
        self.assertEqual(self.execute(), 1)
        # Native sync may prepare an empty root before rclone parses its rules.
        if self.destination.exists():
            self.assertEqual(list(self.destination.iterdir()), [])
        self.assertFalse(self.config.state_dir.joinpath('test/state.json').exists())
        self.assertEqual(self.notifications[-1][1], 'fail')
