"""Real native-filter discovery plus bounded-failure/lifecycle regressions."""
import http.client
import json
import os
import subprocess
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from sambinha.discovery import Discovery, _ListParser
from sambinha.errors import BackupError
from sambinha.filtering import Selection
from sambinha.process import Runner, Terminated
from tests.helpers import REAL_TOOLS, Sandbox


@unittest.skipUnless(REAL_TOOLS, 'rclone and 7z required')
class DiscoveryTests(Sandbox):
    def rules(self, content='*.tmp\n/cache/**\n/root.txt\n'):
        path = self.root / 'exclude.txt'
        path.write_text(content)
        return path

    def test_native_patterns_keep_root_context_and_prune_excluded_subtrees(self):
        for name in ['root.txt', 'cache/private', 'a/root.txt', 'a/skip.tmp',
                     'a/cache/keep', 'ação.txt', 'a/[literal]*?.txt']:
            self.file(name)
        (self.source / 'empty').mkdir()
        calls = []
        original = Discovery.listing
        def listing(server, relative):
            calls.append(relative)
            return original(server, relative)
        with patch.object(Discovery, 'listing', listing):
            files, dirs = Selection(self.rules(), self.config, Runner()).source(self.source)
        self.assertEqual(files, {'a/root.txt', 'a/cache/keep', 'ação.txt', 'a/[literal]*?.txt'})
        self.assertEqual(dirs, {'a', 'a/cache'})
        self.assertNotIn('cache', calls)
        self.assertNotIn('cache/private', files)

    def test_many_directories_start_one_process_and_never_build_filter_trees(self):
        for i in range(80):
            self.file(f'{i}/keep')
            self.file(f'{i}/skip.tmp')
        with patch('sambinha.discovery.subprocess.Popen', wraps=subprocess.Popen) as start, \
             patch.object(Selection, 'include_paths', side_effect=AssertionError('dummy tree')):
            files, _ = Selection(self.rules(), self.config, Runner()).source(self.source)
        self.assertEqual(files, {f'{i}/keep' for i in range(80)})
        self.assertEqual(start.call_count, 1)
        self.assertEqual(start.call_args.args[0][1], 'rcd')
        self.assertNotIn('--recursive', start.call_args.args[0])

    def tree(self):
        for name in ['top.txt', 'a/x/y/deep.txt', 'a/x/skip.tmp', 'a/b.txt', 'b/ação/ü ñ.txt',
                     'b/[lit]*?/f', 'cache/private', 'c/one']:
            self.file(name)
        (self.source / 'empty').mkdir()
        (self.source / 'a' / 'emptychild').mkdir()
        (self.source / 'link').symlink_to(self.root, target_is_directory=True)
        (self.source / 'a' / 'flink').symlink_to(self.source / 'top.txt')

    def walk(self, **options):
        """Selection.source-style traversal returning (calls, files, dirs)."""
        calls = []
        original = Discovery._call
        def call(server, operation, values, deadline, **kwargs):
            calls.append((values['remote'], values['opt']['recurse']))
            return original(server, operation, values, deadline, **kwargs)
        with patch.object(Discovery, '_call', call):
            files, dirs = Selection(self.rules(), self.config, Runner()).source(self.source)
        return calls, files, dirs

    def test_one_recursive_call_per_first_level_subtree_and_flat_root(self):
        self.tree()
        calls, files, dirs = self.walk()
        self.assertEqual(sorted(calls), [('', False), ('a', True), ('b', True), ('c', True), ('empty', True)])
        self.assertEqual(files, {'top.txt', 'a/x/y/deep.txt', 'a/b.txt', 'b/ação/ü ñ.txt',
                                 'b/[lit]*?/f', 'c/one'})
        self.assertEqual(dirs, {'a', 'a/x', 'a/x/y', 'b', 'b/ação', 'b/[lit]*?', 'c'})

    def test_recursive_index_matches_per_directory_listing(self):
        self.tree()
        _, files, dirs = self.walk()
        with patch.object(Discovery, '_load_subtree', lambda server, top: server.oversized.add(top)):
            calls, flat_files, flat_dirs = self.walk()
        self.assertGreater(len(calls), 5)
        self.assertTrue(all(not recurse for _, recurse in calls))
        self.assertEqual((files, dirs), (flat_files, flat_dirs))
        with Discovery(self.config, Runner(), self.source, self.rules()) as server:
            indexed = {d: sorted(r['Path'] for r in server.listing(d)) for d in ['', 'a', 'a/x', 'b']}
        with Discovery(self.config, Runner(), self.source, self.rules()) as server:
            server.max_entries = 0
            flat = {d: sorted(r['Path'] for r in server.listing(d)) for d in ['', 'a', 'a/x', 'b']}
        self.assertEqual(indexed, flat)

    def test_oversized_subtree_falls_back_to_flat_calls_and_keeps_connection_usable(self):
        for i in range(5):
            self.file(f'big/d{i}/f')
        self.file('small/f')
        calls = []
        original = Discovery._call
        def call(server, operation, values, deadline, **kwargs):
            calls.append((values['remote'], values['opt']['recurse']))
            return original(server, operation, values, deadline, **kwargs)
        with patch.object(Discovery, '_call', call), \
             Discovery(self.config, Runner(), self.source, self.rules()) as server:
            server.max_entries = 6
            self.assertEqual(sorted(r['Path'] for r in server.listing('')), ['big', 'small'])
            self.assertEqual(len(server.listing('big')), 5)
            self.assertEqual([r['Path'] for r in server.listing('big/d1')], ['big/d1/f'])
            self.assertEqual([r['Path'] for r in server.listing('small')], ['small/f'])
        self.assertEqual(calls, [('', False), ('big', True), ('big', False), ('big/d1', False),
                                 ('small', True)])

    def test_subtree_failure_stops_discovery_and_reaps_server(self):
        self.file('a/f')
        with Discovery(self.config, Runner(), self.source, self.rules()) as server:
            with patch.object(server, '_call', side_effect=ConnectionResetError()):
                with self.assertRaisesRegex(BackupError, 'Falha de comunicação.*ConnectionResetError'):
                    server.listing('a')
            self.assertIsNotNone(server.process.poll())

    def test_subtree_results_outside_the_subtree_stop_discovery(self):
        for records in [[('other/x', False)], [('a/x', 1)], [('../a/x', False)]]:
            with Discovery(self.config, Runner(), self.source, self.rules()) as server:
                with patch.object(server, '_call', return_value={'list': records}):
                    with self.assertRaises(BackupError):
                        server.listing('a')
                self.assertIsNotNone(server.process.poll())

    def test_subtree_deadline_is_per_request_not_per_walk(self):
        self.file('a/f')
        self.file('b/f')
        with Discovery(self.config, Runner(), self.source, self.rules()) as server:
            server.timeout = 1
            now = [100.0]
            deadlines = []
            def call(operation, values, deadline, **kwargs):
                deadlines.append(deadline)
                now[0] += 0.75
                server._remaining(deadline)
                return {'list': []}
            with patch('sambinha.discovery.time.monotonic', side_effect=lambda: now[0]), \
                 patch.object(server, '_call', side_effect=call):
                server.listing('a')
                server.listing('b')
            self.assertEqual(len(deadlines), 2)
            self.assertNotEqual(deadlines[0], deadlines[1])

    def test_incremental_parser_handles_arbitrary_chunk_boundaries(self):
        body = json.dumps({'list': [{'Path': 'a/ação ü', 'IsDir': True, 'Size': -1},
                                    {'Path': 'a/b"c', 'IsDir': False}]}, ensure_ascii=False).encode()
        for size in (1, 2, 3, 7, len(body)):
            parser, entries = _ListParser(10), []
            for i in range(0, len(body), size):
                self.assertFalse(parser.feed(body[i:i + size], entries))
            parser.finish()
            self.assertEqual(entries, [('a/ação ü', True), ('a/b"c', False)])
        parser = _ListParser(1)
        self.assertTrue(parser.feed(body, []))
        with self.assertRaises(ValueError):
            truncated = _ListParser(10)
            truncated.feed(body[:-5], [])
            truncated.finish()

    def test_regex_rules_still_match_without_unsafe_manual_directory_pruning(self):
        self.file('a/skip.tmp')
        self.file('a/keep')
        files, _ = Selection(self.rules('{{.*\\.tmp}}\n'), self.config, Runner()).source(self.source)
        self.assertEqual(files, {'a/keep'})

    def test_icon_glob_excludes_control_character_without_matching_other_icons(self):
        for name in ['Icon\r', 'a/Icon\r', 'Icon', 'Icon1', 'Icona', 'Icon.png']:
            self.file(name)
        files, _ = Selection(self.rules('Icon[\\x0d␍]\n'), self.config, Runner()).source(self.source)
        self.assertEqual(files, {'Icon', 'Icon1', 'Icona', 'Icon.png'})

    def test_symlink_files_and_directories_are_not_followed(self):
        self.file('keep')
        external = self.root / 'outside'
        external.mkdir()
        (external / 'private').touch()
        (self.source / 'link').symlink_to(external, target_is_directory=True)
        (self.source / 'linked-file').symlink_to(external / 'private')
        files, _ = Selection(self.rules(), self.config, Runner()).source(self.source)
        self.assertEqual(files, {'keep'})

    def test_private_server_authentication_and_cleanup(self):
        with Discovery(self.config, Runner(), self.source, self.rules()) as server:
            pid = server.process.pid
            directory = Path(server.directory.name)
            connection = http.client.HTTPConnection('127.0.0.1', server.connection.port, timeout=5)
            try:
                connection.request('POST', '/operations/list', body=json.dumps({
                    'fs': str(self.source), 'remote': ''}), headers={'Content-Type': 'application/json'})
                response = connection.getresponse()
                response.read()
                self.assertEqual(response.status, 401)
            finally:
                connection.close()
            self.assertEqual(server.listing(''), [])
        self.assertIsNotNone(server.process.poll())
        self.assertFalse(directory.exists())
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)

    def test_timeout_kills_server_without_returning_partial_inventory(self):
        with Discovery(self.config, Runner(), self.source, self.rules()) as server:
            with patch.object(server.connection, 'getresponse', side_effect=TimeoutError):
                with self.assertRaisesRegex(BackupError, 'Timeout.*origem'):
                    server.listing('')
            self.assertIsNotNone(server.process.poll())

    def test_listing_is_one_synchronous_request_without_polling_or_sleep(self):
        self.file('keep')
        with Discovery(self.config, Runner(), self.source, self.rules()) as server:
            with patch.object(server, '_call', wraps=server._call) as call, \
                 patch('sambinha.discovery.time.sleep', side_effect=AssertionError('polling')):
                self.assertEqual([r['Path'] for r in server.listing('')], ['keep'])
            call.assert_called_once()
            operation, values, _ = call.call_args.args
            self.assertEqual(operation, 'operations/list')
            self.assertFalse(values['_async'])
            self.assertFalse(values['opt']['recurse'])

    def test_directory_deadline_is_renewed_for_each_request(self):
        with Discovery(self.config, Runner(), self.source, self.rules()) as server:
            server.timeout = 1
            now = [100.0]
            def listing(operation, values, deadline):
                now[0] += 0.75
                server._remaining(deadline)
                return {'list': []}
            with patch('sambinha.discovery.time.monotonic', side_effect=lambda: now[0]), \
                 patch.object(server, '_call', side_effect=listing):
                self.assertEqual(server.listing(''), [])
                self.assertEqual(server.listing(''), [])

    def test_incremental_response_cannot_extend_the_directory_deadline(self):
        with Discovery(self.config, Runner(), self.source, self.rules()) as server:
            server.timeout = 0.05
            now = [100.0]
            response = Mock(status=200)
            def read(size):
                now[0] += 0.03
                return b' '
            response.read1.side_effect = read
            with patch('sambinha.discovery.time.monotonic', side_effect=lambda: now[0]), \
                 patch.object(server.connection, 'getresponse', return_value=response):
                with self.assertRaisesRegex(BackupError, 'Timeout.*origem'):
                    server.listing('')
            self.assertIsNotNone(server.process.poll())

    def test_interruption_stops_the_outstanding_job_and_server(self):
        with Discovery(self.config, Runner(), self.source, self.rules()) as server:
            with patch.object(server.connection, 'getresponse', side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):
                    server.listing('')
            self.assertIsNotNone(server.process.poll())

    def test_sigterm_during_a_listing_terminates_the_server(self):
        # Terminated (SIGTERM in the main thread) unwinds through __exit__
        # exactly like Ctrl+C, so the rcd server never outlives the run.
        with self.assertRaises(Terminated):
            with Discovery(self.config, Runner(), self.source, self.rules()) as server:
                with patch.object(server.connection, 'getresponse', side_effect=Terminated):
                    server.listing('')
        self.assertIsNotNone(server.process.poll())
        self.assertFalse(Path(server.directory.name).exists())

    def test_missing_directory_reports_failure_instead_of_an_empty_listing(self):
        with Discovery(self.config, Runner(), self.source, self.rules()) as server:
            with self.assertRaises(BackupError):
                server.listing('missing')
            self.assertIsNotNone(server.process.poll())

    def test_transport_failure_reaps_server(self):
        with Discovery(self.config, Runner(), self.source, self.rules()) as server:
            with patch.object(server, '_call', side_effect=BrokenPipeError()):
                with self.assertRaisesRegex(BackupError, r'BrokenPipeError; rclone de descoberta .*Serving remote'):
                    server.listing('')
            self.assertEqual(len(server.restarts), 1)
            self.assertIsNotNone(server.process.poll())

    def test_server_write_timeout_follows_the_job_timeout(self):
        launched = []
        real = subprocess.Popen
        def popen(arguments, *args, **kwargs):
            launched.append(arguments)
            return real(arguments, *args, **kwargs)
        with patch('sambinha.discovery.subprocess.Popen', popen), \
             Discovery(self.config, Runner(timeout=7200), self.source, self.rules()):
            pass
        arguments = launched[0]
        for option in ('--rc-server-read-timeout', '--rc-server-write-timeout'):
            self.assertEqual(arguments[arguments.index(option) + 1], '7200s')

    def test_crashed_server_is_restarted_and_listing_is_repeated(self):
        self.file('a/f')
        with Discovery(self.config, Runner(), self.source, self.rules()) as server:
            server.process.kill()
            server.process.wait()
            self.assertEqual([r['Path'] for r in server.listing('a')], ['a/f'])
            self.assertEqual(server.starts, 2)
            self.assertEqual(len(server.restarts), 1)

    def test_server_that_keeps_failing_reports_exit_code_and_log(self):
        with Discovery(self.config, Runner(), self.source, self.rules()) as server:
            server.process.kill()
            server.process.wait()
            with patch.object(server, '_spawn', side_effect=BackupError('rclone de descoberta não iniciou: boom')):
                with self.assertRaisesRegex(BackupError, 'não iniciou: boom'):
                    server.listing('')

    def test_server_uses_runner_environment_without_mutating_it(self):
        environment = {**os.environ, 'RCLONE_RC_PASS': 'inherited-password',
                       'RCLONE_RC_USER': 'inherited-user', 'RCLONE_USE_JSON_LOG': 'true',
                       'RCLONE_RC_BASEURL': '/inherited',
                       'RCLONE_LOG_FILE': str(self.root / 'unwanted.log')}
        before = environment.copy()
        with Discovery(self.config, Runner(env=environment), self.source, self.rules()) as server:
            self.assertEqual(server.listing(''), [])
        self.assertEqual(environment, before)
        self.assertFalse((self.root / 'unwanted.log').exists())

    def test_invalid_rules_fail_at_startup_and_release_resources(self):
        server = Discovery(self.config, Runner(), self.source, self.rules('[\n'))
        with self.assertRaisesRegex(BackupError, 'não iniciou'), server:
            self.fail('invalid rules accepted')
        self.assertIsNotNone(server.process.poll())
        self.assertFalse(Path(server.directory.name).exists())

    def test_malformed_or_out_of_directory_results_stop_discovery(self):
        for records in [None, [{'Path': '../outside', 'IsDir': False}],
                        [{'Path': 'a/b', 'IsDir': False}], [{'Path': 'keep'}]]:
            with self.subTest(records=records):
                with Discovery(self.config, Runner(), self.source, self.rules()) as server:
                    with patch.object(server, '_call', return_value={'list': records}):
                        with self.assertRaises(BackupError):
                            server.listing('')
                    self.assertIsNotNone(server.process.poll())

    def test_directory_failure_preserves_committed_state_and_destination(self):
        self.file('keep')
        self.config = replace(self.config, jobs=(replace(self.config.jobs[0], logic='batch',
                                                       exclude_files=self.rules()),))
        self.assertEqual(self.execute(), 0)
        before = self.state()
        archives = {p.relative_to(self.destination).as_posix(): p.read_bytes()
                    for p in self.destination.rglob('*') if p.is_file()}
        with patch.object(Discovery, 'listing', side_effect=BackupError('directory timed out')):
            self.assertEqual(self.execute(now=2000), 1)
        self.assertEqual(self.state(), before)
        self.assertEqual({p.relative_to(self.destination).as_posix(): p.read_bytes()
                          for p in self.destination.rglob('*') if p.is_file()}, archives)

    def test_startup_timeout_and_popen_failure_cleanup(self):
        server = Discovery(self.config, Runner(timeout=0.03), self.source, self.rules())
        with patch.object(server, '_diagnostic', return_value=''):
            with self.assertRaisesRegex(BackupError, 'Timeout ao iniciar'):
                with server:
                    pass
        self.assertIsNotNone(server.process.poll())
        self.assertFalse(Path(server.directory.name).exists())
        server = Discovery(self.config, Runner(), self.source, self.rules())
        with patch('sambinha.discovery.subprocess.Popen', side_effect=OSError('not executable')):
            with self.assertRaises(OSError):
                with server:
                    pass
        self.assertFalse(Path(server.directory.name).exists())
