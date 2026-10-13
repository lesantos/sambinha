from dataclasses import replace
import copy
import io
import os
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from sambinha.batches import plan
from sambinha.errors import BackupError
from sambinha.inventory import scan, safe_relative
from sambinha.logging import RunLog
from sambinha.process import Runner
from sambinha.recovery import identity, unpack
from sambinha.state import atomic_json, load_state, lock
from tests.helpers import REAL_TOOLS, Sandbox


class SecurityTests(Sandbox):
    def test_root_null_and_nonstring_relative_paths_are_rejected(self):
        for value in ('.', 'a\x00b', None, 42, 'a/./b', '../outside'):
            with self.subTest(value=value), self.assertRaises(BackupError):
                safe_relative(value)

    def test_metadata_files_remain_private_with_permissive_umask(self):
        old = os.umask(0)
        try:
            log = RunLog(self.root / 'logs')
            log.close()
            state = self.root / 'state' / 'job' / 'state.json'
            atomic_json(state, {})
            with lock(self.root / 'lock'):
                self.assertEqual((self.root / 'lock/run.lock').stat().st_mode & 0o777, 0o600)
        finally:
            os.umask(old)
        self.assertEqual(log.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(log.path.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(state.stat().st_mode & 0o777, 0o600)

    def test_state_directory_and_lock_symlinks_do_not_redirect_writes(self):
        outside = self.root / 'outside'
        outside.mkdir()
        linked = self.root / 'linked'
        linked.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(BackupError):
            atomic_json(linked / 'state.json', {})
        directory = self.root / 'locks'
        directory.mkdir()
        target = outside / 'keep'
        target.write_text('keep')
        (directory / 'run.lock').symlink_to(target)
        with self.assertRaises(OSError):
            with lock(directory):
                pass
        self.assertEqual(target.read_text(), 'keep')

    def test_corrupt_controls_and_active_retirement_are_rejected_before_cleanup(self):
        self.file('file')
        job = replace(self.config.jobs[0], logic='batch')
        batch = plan(scan(self.source), 1000, 2)[0]
        initial = {'schema': 1, 'identity': identity(job), 'active': {batch.key: batch.control()}, 'retired': []}
        mutations = []
        for field, value in (('path', '../outside'), ('size', -1), ('sha256', 'bad'), ('mode', True)):
            state = copy.deepcopy(initial)
            state['active'][batch.key]['files'][0][field] = value
            mutations.append(state)
        state = copy.deepcopy(initial)
        state['retired'].append({'key': batch.key, 'expires': 0})
        mutations.append(state)
        path = self.root / 'state.json'
        for state in mutations:
            with self.subTest(state=state):
                atomic_json(path, state)
                with self.assertRaises(BackupError):
                    load_state(path, identity(job))

    def test_backend_passwords_urls_and_multiline_secrets_are_redacted(self):
        runner = Runner(env={'RCLONE_CONFIG_AWS_PASS': 'private-pass', 'API_TOKEN': 'first\nsecond'},
                        secrets=('https://example.com/private-monitor',))
        log = RunLog(self.root / 'logs', redact=runner.safe_error)
        from contextlib import redirect_stdout
        with redirect_stdout(io.StringIO()) as output:
            log.event('error', error='private-pass first\nsecond https://example.com/private-monitor https://user:pass@example.com')
        log.close()
        for text in (log.path.read_text(), output.getvalue()):
            self.assertNotIn('private-pass', text)
            self.assertNotIn('first', text)
            self.assertNotIn('private-monitor', text)
            self.assertNotIn('user:pass', text)

    def test_credentials_from_rclone_config_are_redacted_in_backend_failures(self):
        import subprocess
        config = self.root / 'rclone.conf'
        config.write_text('[aws]\ntype=s3\naccess_key_id=PRIVATE-ID\nsecret_access_key=PRIVATE-KEY\n')
        runner = Runner(env={})
        runner.load_config_secrets(config)
        failure = subprocess.CompletedProcess([], 1, '', 'authentication failed: PRIVATE-ID PRIVATE-KEY')
        with patch('subprocess.run', return_value=failure), self.assertRaises(BackupError) as error:
            runner.run(['rclone'])
        self.assertNotIn('PRIVATE-', str(error.exception))

    def test_encrypted_rclone_configuration_is_left_to_the_backend(self):
        config = self.root / 'encrypted.conf'
        config.write_text('RCLONE_ENCRYPT_V0:\nencrypted-payload\n')
        runner = Runner(env={'RCLONE_CONFIG_PASS': 'private-pass'})
        runner.load_config_secrets(config)
        self.assertEqual(runner.safe_error('decryption failed: private-pass'), 'decryption failed: [REDACTED]')

    def test_archive_traversal_and_links_are_refused_before_extraction(self):
        job = replace(self.config.jobs[0], logic='batch')
        storage = Mock(job=job)
        runner = Mock()
        for listing in ('Path = ../outside\nSize = 1\n',
                        'Path = file\nSize = 1\nAttributes = A_ lrwxrwxrwx\n'):
            with self.subTest(listing=listing):
                runner.run.return_value = listing
                with self.assertRaises(BackupError):
                    unpack(storage, runner, 'batch.7z', {'files': [{'path': 'file', 'size': 1}]}, self.root)
                self.assertEqual(runner.run.call_args.args[0][1], 'l')


@unittest.skipUnless(REAL_TOOLS, 'rclone required')
class MassDeletionTests(Sandbox):
    def prepare(self, logic):
        for name in ('a', 'b', 'c', 'd'):
            self.file(name)
        changes = ({'max_delete': 1} if logic == 'simple' else
                   {'deletion_min_files': 2, 'max_delete_percent': 50})
        self.config = replace(self.config, jobs=(replace(self.config.jobs[0], logic=logic, **changes),))
        self.assertEqual(self.execute(), 0)

    def test_simple_and_batch_block_before_any_destination_or_state_change(self):
        for logic in ('simple', 'batch'):
            with self.subTest(logic=logic):
                # Separate fixtures prevent one logic from reusing the other's index.
                self.prepare(logic)
                before_state = self.state() if logic == 'batch' else None
                destination = {str(p): p.read_bytes() for p in self.destination.rglob('*') if p.is_file()}
                for name in ('a', 'b', 'c'):
                    (self.source / name).unlink()
                self.assertEqual(self.execute(now=2000), 1)
                if logic == 'batch':
                    self.assertEqual(self.state(), before_state)
                    self.assertEqual({str(p): p.read_bytes() for p in self.destination.rglob('*') if p.is_file()}, destination)
                else:
                    self.assertFalse((self.config.state_dir / 'test' / 'state.json').exists())
                    after = {str(p): p.read_bytes() for p in self.destination.rglob('*') if p.is_file()
                             and '.sambinha/history' not in str(p)}
                    before = {path: payload for path, payload in destination.items()
                              if '.sambinha/history' not in path}
                    self.assertLessEqual(len(set(before) - set(after)), 1)
                self.assertEqual(self.notifications[-1][1], 'fail')

    def test_simple_max_delete_is_absolute_and_explicit_override_is_execution_only(self):
        self.prepare('simple')
        (self.source / 'a').unlink()
        (self.source / 'b').unlink()
        self.assertEqual(self.execute(now=2000), 1)
        remaining = {p.name for p in self.destination.iterdir() if p.is_file()}
        self.assertLessEqual(len({'a', 'b', 'c', 'd'} - remaining), 1)
        self.assertEqual(self.execute(now=2000, allow_mass_deletion=True), 0)
        self.assertEqual({p.name for p in self.destination.iterdir() if p.is_file()}, {'c', 'd'})
        self.assertEqual(self.config.jobs[0].max_delete, 1)

    def test_exact_percentage_is_allowed_and_explicit_override_is_execution_only(self):
        self.prepare('batch')
        (self.source / 'a').unlink()
        (self.source / 'b').unlink()
        self.assertEqual(self.execute(now=2000), 0)
        (self.source / 'c').unlink()
        (self.source / 'd').unlink()
        self.assertEqual(self.execute(now=3000), 1)
        self.assertEqual(self.execute(now=3000, allow_mass_deletion=True), 0)
