import inspect
import unittest
from dataclasses import replace
from pathlib import Path
from typing import ClassVar, get_args
from unittest.mock import Mock, patch

from sambinha import engine
from sambinha.context import Context, SimpleContext
from sambinha.errors import BackupError
from sambinha.jobs import JobExecution
from sambinha.names import Logic
from sambinha.storage import Storage, StorageBackend
from sambinha.strategies import batch as batch_strategy
from tests.helpers import BatchContext, Sandbox


def public_members(protocol):
    return {name: member for name, member in vars(protocol).items()
            if callable(member) and not name.startswith('_')}


def parameters(function):
    return [(p.name, p.kind, p.default is inspect.Parameter.empty)
            for p in inspect.signature(function).parameters.values() if p.name != 'self']


class MemoryStorage:
    """Fake in-memory StorageBackend: no rclone, no filesystem destination."""

    objects: ClassVar[dict] = {}

    def __init__(self, job, config, runner, log=None, *, temporary_directory=None, source_required=True):
        self.job, self.config, self.runner, self.log = job, config, runner, log
        self.temporary_directory = temporary_directory

    def path(self, key):
        return 'memory:' + key

    def command(self, operation, *args, **options):
        raise AssertionError(f'unexpected raw command {operation}')

    def inventory(self):
        return {key: {'Size': len(data)} for key, data in self.objects.items()}

    def put(self, source, key, *, standard=False, protected_mtime_ns=None):
        data = Path(source).read_bytes()
        changed = self.objects.get(key) != data
        self.objects[key] = data
        return changed

    def get(self, key, target):
        Path(target).write_bytes(self.objects[key])

    def stat(self, key, *, hashes=True):
        return {'Size': len(self.objects[key])}

    def delete(self, key):
        self.objects.pop(key, None)

    def sync_simple(self, root, backup_dir, *, max_delete, dry_run=False):
        return []

    def expire_simple_history(self, now, retention_days, *, dry_run=False):
        return []

    def check_simple(self):
        return None

    def restore_simple(self, output, selectors=(), *, dry_run=False):
        return None


class ConformanceTests(unittest.TestCase):
    def test_storage_and_fake_backend_match_the_protocol_signatures(self):
        for implementation in (Storage, MemoryStorage):
            for name, member in public_members(StorageBackend).items():
                with self.subTest(implementation=implementation.__name__, member=name):
                    actual = getattr(implementation, name, None)
                    self.assertTrue(callable(actual), f'{name} missing')
                    wanted = parameters(member)
                    have = parameters(actual)
                    if implementation is MemoryStorage and name == 'command':
                        continue  # the fake accepts **options instead of the named rclone options
                    self.assertEqual([(n, k) for n, k, _ in have][:len(wanted)],
                                     [(n, k) for n, k, _ in wanted])

    def test_storage_declares_the_protocol_attributes(self):
        storage = Storage(Mock(), Mock(), Mock())
        for name in ('job', 'config', 'runner', 'temporary_directory'):
            self.assertTrue(hasattr(storage, name), name)


class RegistryTests(Sandbox):
    def test_every_logic_is_registered_and_unknown_logic_fails_clearly(self):
        self.assertEqual(set(engine.STRATEGIES), set(get_args(Logic.__value__)))
        self.assertIs(engine.STRATEGIES['batch'], batch_strategy.batch)
        job = replace(self.config.jobs[0], logic='quantum')
        execution = JobExecution(self.config, job, Mock(), Mock(), Mock(), Mock(), lambda: 0.0, False,
                                 engine.STRATEGIES)
        with patch('sambinha.jobs.preflight') as preflight, self.assertRaisesRegex(BackupError, 'quantum'):
            execution.execute()
        preflight.assert_not_called()

    def test_simple_logic_dispatches_through_the_registry_with_the_injected_factory(self):
        job = replace(self.config.jobs[0], logic='simple')
        seen = []
        execution = JobExecution(self.config, job, Mock(), Mock(), Mock(), Mock(), lambda: 5.0, True,
                                 {'simple': seen.append, 'batch': Mock()}, storage_factory=MemoryStorage)
        with patch('sambinha.jobs.preflight', return_value={}):
            execution.execute()
        (ctx,) = seen
        self.assertIsInstance(ctx, SimpleContext)
        self.assertIs(ctx.storage_factory, MemoryStorage)
        self.assertTrue(ctx.allow_mass_deletion)
        self.assertEqual(ctx.now, 5.0)


class InjectedBackendTests(BatchContext, Sandbox):
    def test_upload_batch_writes_through_the_context_storage_factory(self):
        MemoryStorage.objects = {}
        ctx, pending, _active = self.context(workers=1, count=1)
        ctx = Context(ctx.config, ctx.job, ctx.runner, ctx.log, ctx.files, ctx.now,
                      storage_factory=MemoryStorage)
        self.assertIsInstance(ctx.storage, MemoryStorage)

        def compress(worker, item, work):
            archive = work / 'archive.7z'
            archive.write_bytes(b'archive')
            return archive

        with patch.object(batch_strategy, 'compress', compress):
            control, events = batch_strategy.upload_batch(ctx, pending[0])
        key = pending[0].key
        self.assertEqual(MemoryStorage.objects[key], b'archive')
        self.assertIn(key + '.json', MemoryStorage.objects)
        self.assertEqual([event for event, _ in events], ['transferred', 'transferred'])
        self.assertEqual(control['archive_size'], 7)
        self.assertEqual(ctx.storage.stat(key)['Size'], 7)
