"""SimpleOperations (sync/history/check/restore) driven through Storage with a scripted runner."""
import json
from dataclasses import replace
from unittest.mock import Mock

from sambinha.errors import BackupError
from sambinha.storage import Storage
from tests.helpers import FakeRunner, Sandbox

NEWER = {'msg': 'Skipped copy as --update is in use and destination file is newer', 'object': 'dir/new.txt',
         'level': 'debug'}
LSJSON = 'rclone lsjson'


class SimpleStorageTests(Sandbox):
    def storage(self, runner, *, local=False, **job):
        values = {} if local else {'type': 's3', 'destination': 'aws:bucket'}
        self.log = Mock()
        return Storage(replace(self.config.jobs[0], **values, **job), self.config, runner, self.log)

    def emit(self, *records):
        def reply(command, on_output, **options):
            for record in records:
                on_output(record)
            return ''
        return reply

    # sync_simple
    def test_sync_reports_destination_newer_files_and_logs_rclone_records(self):
        records = (NEWER, {'msg': 'Destination is newer than source, skipping', 'object': 'b.txt'},
                   {'msg': 'Destination is newer than source, skipping', 'object': 5},  # object must be text
                   {'msg': 5}, {'msg': 'plain', 'level': 'info'})
        runner = FakeRunner({'rclone sync': self.emit(*records)})
        newer = self.storage(runner).sync_simple(self.source, 'hist/run', max_delete=3)
        self.assertEqual(newer, ['b.txt', 'dir/new.txt'])
        events = [c.args[0] for c in self.log.event.call_args_list]
        self.assertEqual(events, ['rclone'] * 4)  # the non-text message is dropped
        self.assertEqual(self.log.event.call_args_list[0].kwargs['object'], 'dir/new.txt')
        command = runner.commands[0]
        self.assertEqual(command[command.index('--max-delete') + 1], '3')
        self.assertIn('--delete-before', command)
        self.assertEqual(command[command.index('--log-level') + 1], 'DEBUG')

    def test_non_detailed_logging_does_not_stream_records(self):
        runner = FakeRunner()
        storage = self.storage(runner, log_file_level='normal', log_terminal_level='normal')
        self.assertEqual(storage.sync_simple(self.source, 'h', max_delete=1), [])
        self.assertNotIn('on_output', runner.options[0])
        self.assertEqual(runner.commands[0][runner.commands[0].index('--log-level') + 1], 'ERROR')

    def test_dry_run_and_exclude_file_reach_the_sync_command(self):
        rules = self.root / 'rules'
        rules.write_text('- x\n')
        runner = FakeRunner()
        self.storage(runner, exclude_files=rules).sync_simple(self.source, 'h', max_delete=1, dry_run=True)
        command = runner.commands[0]
        self.assertIn('--dry-run', command)
        self.assertEqual(command[command.index('--exclude-from') + 1], str(rules))

    def test_local_sync_creates_the_destination_unless_dry_run(self):
        runner = FakeRunner()
        self.storage(runner, local=True).sync_simple(self.source, 'h', max_delete=1, dry_run=True)
        self.assertFalse(self.destination.exists())
        self.storage(runner, local=True).sync_simple(self.source, 'h', max_delete=1)
        self.assertTrue(self.destination.is_dir())

    def test_local_sync_replaces_destination_links_that_conflict_with_source_directories(self):
        (self.source / 'tree').mkdir()
        self.file('tree/a.txt')
        outside = self.root / 'outside'
        outside.mkdir()
        self.destination.mkdir()
        (self.destination / 'tree').symlink_to(outside)
        runner = FakeRunner()
        self.storage(runner, local=True).sync_simple(self.source, 'h', max_delete=1, dry_run=True)
        self.assertEqual(self.log.event.call_args_list[0].args[0], 'would_replace_link')
        self.assertTrue((self.destination / 'tree').is_symlink())  # a dry run changes nothing
        self.log.reset_mock()
        self.storage(runner, local=True).sync_simple(self.source, 'h', max_delete=1)
        self.assertEqual(self.log.event.call_args_list[0].args[0], 'replace_link')
        self.assertFalse((self.destination / 'tree').is_symlink())
        self.assertEqual(list(outside.iterdir()), [])  # the link target was never touched

    # expire_simple_history
    def history(self, *names):
        return json.dumps([{'Path': n, 'IsDir': True} for n in names])

    def test_history_expiry_purges_only_old_well_formed_run_directories(self):
        old, young = '20200101T000000-' + 'a' * 32, '20260101T000000-' + 'b' * 32
        listing = json.dumps([{'Path': old}, {'Path': young}, {'Path': 'junk'}, {'Path': 5}, 'text', {}])
        runner = FakeRunner({LSJSON: listing})
        storage = self.storage(runner)
        now = 1767225600 + 3 * 86400  # three days after `young`
        self.assertEqual(storage.expire_simple_history(now, 30, dry_run=True), [old])
        self.assertEqual(runner.calls('rclone', 'purge'), [])
        self.assertEqual(storage.expire_simple_history(now, 30), [old])
        self.assertEqual(runner.calls('rclone', 'purge')[0][2], f'aws:bucket/.sambinha/history/{old}')

    def test_history_listing_must_be_a_json_list(self):
        for reply in ('nope', '{}', 'null'):
            with self.subTest(reply=reply), self.assertRaisesRegex(BackupError, 'Histórico rclone inválido'):
                self.storage(FakeRunner({LSJSON: reply})).expire_simple_history(0, 1)

    def test_local_history_without_a_directory_has_nothing_to_expire(self):
        runner = FakeRunner()
        self.assertEqual(self.storage(runner, local=True).expire_simple_history(1, 1), [])
        self.assertEqual(runner.commands, [])

    # check_simple
    def test_check_compares_source_and_destination_with_the_exclude_rules(self):
        rules = self.root / 'rules'
        rules.write_text('- x\n')
        runner = FakeRunner()
        self.storage(runner, exclude_files=rules).check_simple()
        command = runner.commands[0]
        self.assertEqual(command[:4], ['rclone', 'check', str(self.source), 'aws:bucket'])
        self.assertIn(str(rules), command)

    # restore_simple
    def stat(self, **kinds):
        """lsjson --stat reply by selector path."""
        def reply(command, **options):
            return json.dumps(kinds[command[2].removeprefix('aws:bucket/')])
        return reply

    def test_full_restore_copies_the_whole_destination(self):
        runner = FakeRunner()
        self.storage(runner).restore_simple(self.root / 'out', dry_run=True)
        command = runner.commands[0]
        self.assertEqual(command[:4], ['rclone', 'copy', 'aws:bucket', str(self.root / 'out')])
        self.assertIn('--dry-run', command)

    def test_selective_restore_dispatches_files_and_directories_and_skips_nested_selectors(self):
        runner = FakeRunner({LSJSON: self.stat(**{'d': {'IsDir': True}, 'f.txt': {'IsDir': False}})})
        self.storage(runner).restore_simple(self.root / 'out', ['f.txt', 'd/', 'd/inner.txt'])
        self.assertEqual([c[1] for c in runner.commands if c[1] != 'lsjson'], ['copy', 'copyto'])
        self.assertEqual(len(runner.calls('rclone', 'lsjson')), 2)  # d/inner.txt is covered by d
        self.assertEqual(runner.calls('rclone', 'copy')[0][3], str(self.root / 'out' / 'd'))

    def test_selective_restore_refuses_the_reserved_namespace(self):
        for selector in ('.sambinha', '.sambinha/history/x'):
            with self.subTest(selector=selector), self.assertRaisesRegex(BackupError, 'reservado'):
                self.storage(FakeRunner()).restore_simple(self.root / 'out', [selector])

    def test_selective_restore_reports_missing_and_malformed_paths(self):
        with self.assertRaisesRegex(BackupError, 'não existe no backup: gone'):
            self.storage(FakeRunner({LSJSON: BackupError('directory not found')})).restore_simple(self.root, ['gone'])
        for reply in ('[]', '{}', '{"IsDir": 1}', 'null'):
            with self.subTest(reply=reply), self.assertRaises(BackupError):
                self.storage(FakeRunner({LSJSON: reply})).restore_simple(self.root, ['x'])

    def test_excluded_file_selector_is_refused_before_copyto(self):
        rules = self.root / 'rules'
        rules.write_text('- *.tmp\n')
        runner = FakeRunner({LSJSON: lambda command, **o: '{"IsDir": false}' if '--stat' in command else '[]'})
        with self.assertRaisesRegex(BackupError, 'excluído: skip.tmp'):
            self.storage(runner, exclude_files=rules).restore_simple(self.root / 'out', ['skip.tmp'])
        self.assertEqual(runner.calls('rclone', 'copyto'), [])

    def test_included_file_selector_passes_the_exclusion_check(self):
        rules = self.root / 'rules'
        rules.write_text('- *.tmp\n')

        def reply(command, **options):
            return '{"IsDir": false}' if '--stat' in command else json.dumps([{'Path': 'keep.txt', 'IsDir': False}])
        runner = FakeRunner({LSJSON: reply})
        self.storage(runner, exclude_files=rules).restore_simple(self.root / 'out', ['keep.txt'])
        self.assertEqual(len(runner.calls('rclone', 'copyto')), 1)
