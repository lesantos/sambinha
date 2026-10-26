"""filtering: frozen rule snapshots, retired path resolution, path grouping and rclone-backed Selection."""
import json
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from sambinha.errors import BackupError, JobError
from sambinha.filtering import SKIP_LINK_FLAGS, Selection, frozen_rules, path_groups, retired_paths
from tests.helpers import FakeRunner, Sandbox

LSJSON = 'rclone lsjson'


class FrozenRulesTests(Sandbox):
    def rules(self, content=b'- *.tmp\n'):
        path = self.root / 'rules.txt'
        path.write_bytes(content)
        return path

    def test_config_without_rules_is_passed_through_untouched(self):
        with frozen_rules(self.config) as frozen:
            self.assertIs(frozen, self.config)

    def test_rules_are_snapshotted_so_later_edits_do_not_apply(self):
        path = self.rules()
        config = replace(self.config, jobs=(self.job(exclude_files=path), replace(self.job(), name='plain')))
        with frozen_rules(config) as frozen:
            snapshot = frozen.jobs[0].exclude_files
            self.assertNotEqual(snapshot, path)
            path.write_text('- changed\n')
            self.assertEqual(snapshot.read_text(), '- *.tmp\n')
            self.assertIsNone(frozen.jobs[1].exclude_files)
        self.assertFalse(snapshot.exists())

    def test_missing_or_invalid_rule_files_stop_the_job(self):
        for name, path in (('missing', self.root / 'nope.txt'), ('not utf-8', self.rules(b'\xff\xfe\x00bad'))):
            with self.subTest(name), self.assertRaisesRegex(BackupError, 'ausente ou ilegível'):
                with frozen_rules(replace(self.config, jobs=(self.job(exclude_files=path),))):
                    self.fail('must not yield')

    def test_continue_on_job_error_reports_and_drops_the_rule_snapshot(self):
        errors = []
        job = self.job(exclude_files=self.root / 'nope.txt', continue_on_job_error=True)
        with frozen_rules(replace(self.config, jobs=(job,)), on_job_error=errors.append) as frozen:
            self.assertEqual(frozen.jobs, ())
        self.assertIsInstance(errors[0], JobError)


class RetiredPathTests(Sandbox):
    def test_explicit_file_lists_win(self):
        self.assertEqual(retired_paths({'key': 'k', 'files': ['a/b', 'c']}, self.root), ['a/b', 'c'])
        with self.assertRaises(BackupError):
            retired_paths({'key': 'k', 'files': ['../x']}, self.root)

    def test_control_mirror_lists_members_and_honours_a_custom_directory(self):
        for base, kwargs in ((self.root / 'controls', {}), (self.root / 'other', {'controls_directory': self.root / 'other'})):
            (base / 'dir').mkdir(parents=True)
            (base / 'dir' / 'k.7z.json').write_text(json.dumps({'files': [{'path': 'a'}, {'path': 'b/c'}]}))
            entry = {'key': 'dir/k.7z', 'control': 'dir/k.7z.json'}
            self.assertEqual(retired_paths(entry, self.root, **kwargs), ['a', 'b/c'])

    def test_missing_control_mirror_falls_back_to_the_key(self):
        entry = {'key': '.sambinha/history/20260101T000000-x/run/dir/f.txt', 'control': 'gone.json'}
        self.assertEqual(retired_paths(entry, self.root), ['dir/f.txt'])

    def test_plain_keys_are_their_own_path(self):
        self.assertEqual(retired_paths({'key': 'a/b.txt'}, self.root), ['a/b.txt'])
        self.assertEqual(retired_paths({'key': '.sambinha/other/x/y/z'}, self.root), ['.sambinha/other/x/y/z'])
        self.assertEqual(retired_paths({'key': '.sambinha/history/a/b'}, self.root), ['.sambinha/history/a/b'])


class PathGroupTests(Sandbox):
    def test_file_and_directory_replacements_never_share_a_group(self):
        groups = path_groups(['a', 'a/b', 'a/b/c', 'x/y', 'x', 'z'])
        self.assertEqual(sorted(sorted(g) for g in groups), [['a', 'x', 'z'], ['a/b', 'x/y'], ['a/b/c']])
        self.assertEqual(path_groups([]), [])
        self.assertEqual(path_groups(['q', 'q']), [{'q'}])
        with self.assertRaises(BackupError):
            path_groups(['../x'])


class SelectionTests(Sandbox):
    def selection(self, runner, rules=True, **kwargs):
        path = self.root / 'rules.txt'
        path.write_text('- *.tmp\n')
        return Selection(path if rules else None, self.config, runner, **kwargs)

    def test_listing_builds_the_rclone_command_and_validates_paths(self):
        config = replace(self.config, rclone_config='/etc/rc.conf')
        runner = FakeRunner({LSJSON: json.dumps([{'Path': 'a', 'IsDir': False}])})
        selection = Selection(self.root / 'r.txt', config, runner)
        self.assertEqual(selection.listing(self.root, files_only=True), [{'Path': 'a', 'IsDir': False}])
        command = runner.commands[0]
        for flag in (*SKIP_LINK_FLAGS, '--recursive', '--files-only'):
            self.assertIn(flag, command)
        self.assertEqual(command[command.index('--config') + 1], '/etc/rc.conf')
        self.assertEqual(command[command.index('--exclude-from') + 1], str(self.root / 'r.txt'))
        selection.listing(self.root, recursive=False, apply_exclusions=False)
        for flag in ('--recursive', '--files-only', '--exclude-from'):
            self.assertNotIn(flag, runner.commands[1])

    def test_malformed_listings_are_rejected(self):
        for reply in ('nope', '[{}]', '[{"Path": "../x"}]', '[1]', '{"Path": 1}'):
            with self.subTest(reply=reply), self.assertRaises(BackupError):
                self.selection(FakeRunner({LSJSON: reply})).listing(self.root)

    def test_without_rules_nothing_is_filtered_or_discovered(self):
        selection = self.selection(FakeRunner(), rules=False)
        with selection.source_listing(self.root) as listing:
            self.assertIsNone(listing)
        self.assertEqual(selection.source(self.root), (None, None))
        self.assertEqual(selection.include_paths({'a', 'b'}), {'a', 'b'})
        self.assertEqual(selection.include_directories(['d', 'd/e']), {'d', 'd/e'})
        with self.assertRaises(BackupError):
            selection.include_directories(['../x'])

    def test_ignored_roots_hide_paths_behind_links(self):
        outside = self.root / 'outside'
        outside.mkdir()
        (self.source / 'link').symlink_to(outside)
        self.file('real/x.txt')
        selection = self.selection(FakeRunner(), rules=False, ignored_roots=[self.source])
        self.assertEqual(selection.include_paths({'link/x.txt', 'real/x.txt'}), {'real/x.txt'})

    def test_include_paths_classifies_only_paths_missing_from_the_current_inventory(self):
        def classify(command, **options):
            root = Path(command[2])
            return json.dumps([{'Path': p.relative_to(root).as_posix(), 'IsDir': False}
                               for p in root.rglob('*') if p.is_file() and p.suffix != '.tmp'])
        runner = FakeRunner({LSJSON: classify})
        selection = self.selection(runner)
        self.assertEqual(selection.include_paths({'keep.txt', 'drop.tmp', 'cur.tmp'}, current={'cur.tmp'}),
                         {'keep.txt', 'cur.tmp'})
        self.assertEqual(len(runner.commands), 1)
        # Identical questions are answered from the cache; a fully known set needs no rclone at all.
        self.assertEqual(selection.include_paths({'keep.txt', 'drop.tmp'}), {'keep.txt'})
        self.assertEqual(len(runner.commands), 1)
        self.assertEqual(selection.include_paths({'cur.tmp'}, current={'cur.tmp'}), {'cur.tmp'})
        self.assertEqual(len(runner.commands), 1)
        self.assertEqual(selection.include_paths(set()), set())

    def test_file_and_directory_replacements_are_classified_in_separate_trees(self):
        runner = FakeRunner({LSJSON: '[]'})
        self.assertEqual(self.selection(runner).include_paths({'a', 'a/b'}), set())
        self.assertEqual(len(runner.commands), 2)

    def test_include_directories_keeps_only_directories_rclone_lists(self):
        def reply(command, **options):
            return json.dumps([{'Path': 'kept', 'IsDir': True}, {'Path': 'kept/file', 'IsDir': False},
                               {'Path': 'excluded-looking', 'IsDir': False}])
        selection = self.selection(FakeRunner({LSJSON: reply}))
        self.assertEqual(selection.include_directories(['kept', 'dropped', 'excluded-looking']), {'kept'})

    def test_source_walks_included_directories_and_derives_parents(self):
        tree = {'': [{'Path': 'a', 'IsDir': True}, {'Path': 'top.txt', 'IsDir': False}, {'Path': 'lnk', 'IsDir': True}],
                'a': [{'Path': 'a/b', 'IsDir': True}, {'Path': 'a/f.txt', 'IsDir': False}],
                'a/b': [{'Path': 'a/b/deep.txt', 'IsDir': False}]}
        outside = self.root / 'outside'
        outside.mkdir()
        (self.source / 'lnk').symlink_to(outside)

        class FakeDiscovery:
            def __init__(self, config, runner, root, exclude_file):
                self.visited = []

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def listing(self, relative):
                return tree[relative]
        runner = FakeRunner()
        runner.progress = Mock()
        with patch('sambinha.filtering.Discovery', FakeDiscovery):
            files, directories = self.selection(runner).source(self.source)
        self.assertEqual(files, {'top.txt', 'a/f.txt', 'a/b/deep.txt'})
        self.assertEqual(directories, {'a', 'a/b'})
        self.assertGreaterEqual(runner.progress.update.call_count, 3)  # the linked directory is never listed

    def test_source_listing_hands_out_the_discovery_listing(self):
        entered = []

        @contextmanager
        def discovery(config, runner, root, exclude):
            entered.append(root)
            yield Mock(listing='LISTING')
        with patch('sambinha.filtering.Discovery', discovery), self.selection(FakeRunner()).source_listing(self.source) as listing:
            self.assertEqual(listing, 'LISTING')
        self.assertEqual(entered, [self.source])
