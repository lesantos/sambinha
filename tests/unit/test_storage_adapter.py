"""Storage adapter behavior against a scripted runner: copy confirmation, get/stat/delete and inventory guards."""
import json
from dataclasses import replace

from sambinha.errors import BackupError
from sambinha.storage import Storage
from tests.helpers import FakeRunner, Reply, Sandbox

COPIED = json.dumps({'msg': 'Copied (new)', 'level': 'info'})
STAT = 'rclone lsjson'


def stat_reply(mod_time='2026-01-01T00:00:00Z'):
    return json.dumps({'Path': 'k', 'Size': 1, 'ModTime': mod_time})


class StoragePutTests(Sandbox):
    def storage(self, runner, **job):
        job = replace(self.config.jobs[0], type='s3', destination='aws:bucket/p', **job)
        return Storage(job, self.config, runner)

    def put(self, runner, protected=None, **job):
        return self.storage(runner, **job).put(self.source / 'f', 'k.7z', protected_mtime_ns=protected)

    def test_unprotected_put_checksums_and_reports_whether_bytes_moved(self):
        runner = FakeRunner({'rclone copyto': Reply('', COPIED + '\n')})
        self.assertTrue(self.put(runner))
        command = runner.commands[0]
        self.assertIn('--checksum', command)
        self.assertNotIn('--update', command)
        self.assertEqual(command[command.index('aws:bucket/p/k.7z') - 1], str(self.source / 'f'))
        skipped = FakeRunner({'rclone copyto': Reply('', '{"msg": "Unchanged skipping"}\n')})
        self.assertFalse(self.put(skipped))
        self.assertEqual(len(skipped.calls('rclone', 'lsjson')), 0)  # no confirmation without protection

    def test_missing_or_non_text_stderr_counts_as_a_transfer(self):
        self.assertTrue(self.put(FakeRunner()))

    def test_malformed_report_lines_are_ignored(self):
        report = 'not json\n[1]\n"text"\n{"msg": 5}\n' + COPIED
        self.assertTrue(self.put(FakeRunner({'rclone copyto': Reply('', report)})))
        self.assertFalse(self.put(FakeRunner({'rclone copyto': Reply('', 'garbage\n{"msg": 5}\n[]')})))

    def test_protected_put_uses_update_and_confirms_destination_mtime_after_skip(self):
        base = 1767225600 * 10 ** 9  # 2026-01-01T00:00:00Z
        for name, mod_time, protected, outcome in (('older', '2025-12-31T00:00:00Z', base, False),
                                                   ('equal', '2026-01-01T00:00:00Z', base, False),
                                                   ('fractional equal', '2026-01-01T00:00:00.5Z', base + 5 * 10 ** 8, False)):
            with self.subTest(name):
                runner = FakeRunner({'rclone copyto': Reply('', 'skipped\n'), STAT: stat_reply(mod_time)})
                self.assertIs(self.put(runner, protected), outcome)
                self.assertIn('--update', runner.commands[0])
                self.assertEqual(runner.commands[1][:4], ['rclone', 'lsjson', 'aws:bucket/p/k.7z', '--stat'])

    def test_newer_destination_after_skipped_copy_is_an_error(self):
        runner = FakeRunner({'rclone copyto': Reply('', 'skipped\n'), STAT: stat_reply('2026-01-01T00:00:01Z')})
        with self.assertRaisesRegex(BackupError, 'mais novo preservado'):
            self.put(runner, 1767225600 * 10 ** 9)

    def test_unconfirmable_destination_date_is_an_error(self):
        for name, reply in (('not json', 'oops'), ('not a dict', '[1]'), ('no date', '{"Path": "k"}'),
                            ('bad date', stat_reply('yesterday')), ('null', 'null')):
            with self.subTest(name):
                runner = FakeRunner({'rclone copyto': Reply('', 'skipped\n'), STAT: reply})
                with self.assertRaises(BackupError):
                    self.put(runner, 1)

    def test_simple_verification_ignores_checksums_when_protected(self):
        runner = FakeRunner({'rclone copyto': Reply('', COPIED), STAT: stat_reply()})
        self.assertTrue(self.put(runner, 1, verification_mode='simple'))
        command = runner.commands[0]
        self.assertIn('--ignore-checksum', command)
        self.assertEqual(command[command.index('--modify-window') + 1], '1ns')
        self.assertNotIn('--checksum', command)

    def test_failed_copy_is_not_confirmed(self):
        runner = FakeRunner({'rclone copyto': BackupError('boom')})
        with self.assertRaises(BackupError):
            self.put(runner, 1)
        self.assertEqual(len(runner.commands), 1)


class StorageObjectTests(Sandbox):
    def storage(self, runner, **job):
        return Storage(replace(self.config.jobs[0], type='s3', destination='aws:bucket', **job), self.config, runner)

    def test_get_creates_private_staging_parent_and_ignores_the_source_check(self):
        runner = FakeRunner()
        target = self.root / 'stage' / 'deep' / 'archive'
        self.storage(runner).get('k.7z', target)
        self.assertEqual(target.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(runner.commands[0][:5], ['rclone', 'copyto', 'aws:bucket/k.7z', str(target), '--checksum'])
        self.assertTrue(runner.options[0]['retry'])

    def test_stat_returns_metadata_and_can_skip_hashes(self):
        item = {'Path': 'k', 'Size': 3, 'Hashes': {'md5': 'x'}}
        runner = FakeRunner({STAT: json.dumps(item)})
        storage = self.storage(runner)
        self.assertEqual(storage.stat('k'), item)
        self.assertIn('--hash', runner.commands[0])
        storage.stat('k', hashes=False)
        self.assertNotIn('--hash', runner.commands[1])

    def test_stat_rejects_malformed_metadata(self):
        for name, reply in (('not json', 'x'), ('list', '[]'), ('no size', '{}'), ('bool size', '{"Size": true}'),
                            ('negative', '{"Size": -1}'), ('float', '{"Size": 1.5}'),
                            ('hashes type', '{"Size": 1, "Hashes": []}')):
            with self.subTest(name), self.assertRaisesRegex(BackupError, 'Metadados do destino inválidos'):
                self.storage(FakeRunner({STAT: reply})).stat('k')

    def test_delete_sends_one_literal_key_through_stdin(self):
        runner = FakeRunner()
        self.storage(runner).delete('dir/we*ird name.7z')
        command = runner.commands[0]
        self.assertEqual(command[:2], ['rclone', 'delete'])
        self.assertIn('--files-from-raw', command)
        self.assertEqual(runner.options[0]['input'], 'dir/we*ird name.7z\n')

    def test_delete_of_a_key_behind_a_destination_link_is_ignored(self):
        outside = self.root / 'outside'
        outside.mkdir()
        (outside / 'x').write_bytes(b'keep')
        self.destination.mkdir()
        (self.destination / 'link').symlink_to(outside)
        runner = FakeRunner()
        Storage(self.config.jobs[0], self.config, runner).delete('link/x')
        self.assertEqual(runner.commands, [])
        self.assertTrue((outside / 'x').exists())

    def test_local_path_rejects_a_link_introduced_after_planning(self):
        self.destination.mkdir()
        (self.destination / 'link').symlink_to(self.root)
        with self.assertRaisesRegex(BackupError, 'link simbólico'):
            Storage(self.config.jobs[0], self.config, FakeRunner()).path('link/x')

    def test_local_inventory_of_a_missing_destination_is_empty_without_running_rclone(self):
        runner = FakeRunner()
        self.assertEqual(Storage(self.config.jobs[0], self.config, runner).inventory(), {})
        self.assertEqual(runner.commands, [])

    def test_inventory_hides_namespace_and_linked_paths_and_applies_filters(self):
        self.destination.mkdir()
        outside = self.root / 'outside'
        outside.mkdir()
        (self.destination / 'link').symlink_to(outside)
        rules = self.root / 'rules.txt'
        rules.write_text('- *.tmp\n')
        listing = [{'Path': n, 'Size': 1} for n in ('a.txt', '.sambinha/history/x', 'link/y')]
        runner = FakeRunner({'rclone lsjson': json.dumps(listing)})
        storage = Storage(replace(self.config.jobs[0], exclude_files=rules), self.config, runner)
        self.assertEqual(set(storage.inventory()), {'a.txt'})
        command = runner.commands[0]
        self.assertIn('--hash', command)  # verification_mode == hash in the sandbox
        self.assertEqual(command[command.index('--exclude-from') + 1], str(rules))

    def test_inventory_rejects_malformed_listings(self):
        for name, reply in (('not json', 'x'), ('not list', '{}'), ('item not dict', '[1]'), ('no size', '[{"Path": "a"}]'),
                            ('negative size', '[{"Path": "a", "Size": -1}]'),
                            ('hashes type', '[{"Path": "a", "Size": 1, "Hashes": 3}]'),
                            ('unsafe path', '[{"Path": "../a", "Size": 1}]')):
            with self.subTest(name), self.assertRaises(BackupError):
                self.storage(FakeRunner({'rclone lsjson': reply})).inventory()

    def test_writes_stop_when_the_runner_was_cancelled(self):
        runner = FakeRunner()
        runner.cancel()
        with self.assertRaises(KeyboardInterrupt):
            self.storage(runner).delete('k')
