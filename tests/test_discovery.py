"""Real native-filter discovery plus bounded-failure/lifecycle regressions."""
from dataclasses import replace
import http.client
import json
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import Mock, patch

from sambinha.discovery import Discovery
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
            with patch.object(server, '_call', side_effect=ConnectionResetError()):
                with self.assertRaisesRegex(BackupError, 'Inventário filtrado inválido'):
                    server.listing('')
            self.assertIsNotNone(server.process.poll())

    def test_crashed_server_does_not_turn_into_an_empty_inventory(self):
        with Discovery(self.config, Runner(), self.source, self.rules()) as server:
            server.process.kill()
            server.process.wait()
            with self.assertRaises(BackupError):
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
        with self.assertRaisesRegex(BackupError, 'não iniciou'):
            with server:
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
