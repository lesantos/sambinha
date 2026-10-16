from collections import namedtuple
from concurrent.futures import ThreadPoolExecutor, wait as real_wait
from dataclasses import replace
import importlib
from pathlib import Path
import threading
from unittest import skipUnless
from unittest.mock import Mock, patch

from sambinha.archives import compress, temporary_bytes
from sambinha.batches import plan
from sambinha.context import Context
from sambinha.errors import BackupError
from sambinha.inventory import scan
from sambinha.process import Runner
from sambinha.storage import Storage
from tests.helpers import REAL_TOOLS, Sandbox

strategy = importlib.import_module('sambinha.strategies.batch')
Usage = namedtuple('Usage', 'total used free')
MIB = 1024 ** 2


class BatchContext:
    def context(self, workers=2, count=9):
        for index in range(count):
            self.file(f'{index}.bin', b'contents')
        job = replace(self.config.jobs[0], type='s3', logic='batch',
                      destination='aws:fake-bucket/', max_megabytes=1,
                      check_source_disk=False, check_destination_disk=False)
        config = replace(self.config, jobs=(replace(job, batch_workers=workers),))
        job = config.jobs[0]
        files = scan(self.source)
        ctx = Context(config, job, Runner(), Mock(), files, 1000)
        # Keep one tiny file per test batch while the configured size limit is
        # large enough that these batches are not treated as oversized.
        pending = plan(files, 1)
        active = {item.key: item.control() for item in pending}
        return ctx, pending, active


class BatchStagingTests(BatchContext, Sandbox):
    def test_first_failure_stops_pending_batches(self):
        ctx, pending, active = self.context(workers=1)
        with patch.object(strategy, 'upload_batch', side_effect=BackupError('AccessDenied')) as upload:
            with self.assertRaisesRegex(BackupError, 'AccessDenied'):
                strategy._upload_pending(ctx, pending, active)
        self.assertEqual(upload.call_count, 1)
        self.assertFalse(ctx.state_path.exists())

    def test_later_failure_is_detected_while_first_worker_is_still_busy(self):
        ctx, pending, active = self.context()
        first_started = threading.Event()
        failed_result = threading.Event()
        release = threading.Event()
        started = []
        lock = threading.Lock()

        def upload(worker, item):
            with lock:
                started.append(item.key)
            if item == pending[0]:
                first_started.set()
                if not release.wait(15):
                    raise AssertionError('First worker was not released')
                return item.control(), []
            self.assertEqual(item, pending[1], 'A pending batch started after failure')
            self.assertTrue(first_started.wait(10))
            raise BackupError('second worker failed')

        def observe(*args, **kwargs):
            result = real_wait(*args, **kwargs)
            if any(f.exception() for f in result.done):
                failed_result.set()
            return result

        with patch.object(strategy, 'upload_batch', upload), patch.object(strategy, 'wait', observe):
            with ThreadPoolExecutor(max_workers=1) as caller:
                future = caller.submit(strategy._upload_pending, ctx, pending, active)
                try:
                    self.assertTrue(failed_result.wait(10), 'Failure was hidden behind the busy worker')
                    self.assertFalse(future.done(), 'In-flight cleanup must finish before returning')
                    self.assertEqual(set(started), {pending[0].key, pending[1].key})
                finally:
                    release.set()
                with self.assertRaisesRegex(BackupError, 'second worker failed'):
                    future.result(timeout=10)
        self.assertEqual(len(started), 2)
        self.assertFalse(ctx.state_path.exists())

    def test_space_check_uses_largest_actual_batches_before_starting_workers(self):
        ctx, pending, active = self.context(count=3)
        self.file('0.bin', b'a' * 1000)
        self.file('1.bin', b'b' * 2000)
        self.file('2.bin', b'c' * 3000)
        pending = plan(scan(self.source), ctx.job.max_bytes)
        required = sorted((temporary_bytes(item) for item in pending), reverse=True)
        checked = threading.Event()

        def check(*args, **kwargs):
            self.assertEqual(kwargs['required_bytes'], sum(required[:2]))
            checked.set()

        def upload(worker, item):
            self.assertTrue(checked.is_set())
            return item.control(), []

        with patch.object(strategy, 'check_disk', check), patch.object(strategy, 'upload_batch', upload):
            strategy._upload_pending(ctx, pending, active)

    def test_combined_space_shortage_stops_before_any_batch_is_prepared(self):
        ctx, pending, active = self.context()
        # Each batch fits alone; two do not, despite occupancy below 95%.
        with patch('shutil.disk_usage', return_value=Usage(20 * MIB, 18.5 * MIB, 1.5 * MIB)), \
             patch.object(strategy, 'upload_batch') as upload:
            with self.assertRaisesRegex(BackupError, 'Espaço insuficiente.*necessários.*disponíveis') as error:
                strategy._upload_pending(ctx, pending, active)
        self.assertIn('lotes que definem a estimativa', str(error.exception))
        self.assertIn('maiores arquivos:', str(error.exception))
        self.assertIn('0.bin (8 bytes)', str(error.exception))
        self.assertIn('1.bin (8 bytes)', str(error.exception))
        upload.assert_not_called()
        self.assertFalse(ctx.state_path.exists())

    def test_single_worker_does_not_budget_for_entire_queue(self):
        ctx, pending, active = self.context(workers=1)
        with patch('shutil.disk_usage', return_value=Usage(20 * MIB, 18.5 * MIB, 1.5 * MIB)), \
             patch.object(strategy, 'upload_batch', side_effect=lambda c, item: (item.control(), [])) as upload:
            strategy._upload_pending(ctx, pending, active)
        self.assertEqual(upload.call_count, 9)

    def test_one_batch_does_not_budget_for_unused_workers(self):
        ctx, pending, active = self.context(workers=20, count=1)
        with patch('shutil.disk_usage', return_value=Usage(20 * MIB, 18.5 * MIB, 1.5 * MIB)), \
             patch.object(strategy, 'upload_batch', side_effect=lambda c, item: (item.control(), [])):
            strategy._upload_pending(ctx, pending, active)

    def test_space_is_rechecked_before_creating_archive(self):
        ctx, pending, active = self.context(workers=1)
        staging = self.root / 'staging'
        staging.mkdir()
        calls = 0

        def usage(path):
            nonlocal calls
            calls += 1
            return Usage(100 * MIB, 10 * MIB, 90 * MIB) if calls == 1 else Usage(10 * MIB, 9.4 * MIB, 0.6 * MIB)

        with patch('tempfile.tempdir', str(staging)), patch('shutil.disk_usage', side_effect=usage), \
             patch.object(Storage, 'put') as put:
            with self.assertRaisesRegex(BackupError, 'Espaço insuficiente'):
                strategy._upload_pending(ctx, pending, active)
        put.assert_not_called()
        self.assertEqual(list(staging.iterdir()), [])

    def test_oversized_batches_run_one_at_a_time(self):
        ctx, pending, active = self.context(workers=3, count=4)
        ctx.job = replace(ctx.job, max_megabytes=1 / MIB)
        pending = [replace(item, files=(replace(item.files[0], size=2 * MIB),)) for item in pending]
        active = {item.key: item.control() for item in pending}
        with patch.object(strategy, 'check_disk'), patch.object(strategy, 'upload_batch',
                side_effect=lambda c, item: (item.control(), [])) as upload:
            strategy._upload_pending(ctx, pending, active)
        self.assertEqual(upload.call_count, len(pending))
        self.assertEqual(upload.call_args_list[0].args[1], pending[0])


@skipUnless(REAL_TOOLS, 'rclone and 7z required')
class BatchStagingIntegrationTests(BatchContext, Sandbox):
    def test_blocked_s3_uploads_hold_only_archives_and_do_not_start_more_batches(self):
        ctx, pending, active = self.context()
        staging = self.root / 'staging'
        staging.mkdir()
        ready = threading.Event()
        release = threading.Event()
        lock = threading.Lock()
        archives = []
        original_compress = strategy.compress
        compressed = []

        def record(worker, item, work):
            with lock:
                compressed.append(item.key)
            return original_compress(worker, item, work)

        def put(storage, path, key, **kwargs):
            if Path(path).suffix == '.7z':
                self.assertFalse((Path(path).parent / 'snapshot').exists())
                with lock:
                    archives.append(Path(path))
                    if len(archives) == 2:
                        ready.set()
                if not release.wait(30):
                    raise AssertionError('Blocked uploads were not released')
            return True

        with patch('tempfile.tempdir', str(staging)), patch.object(strategy, 'compress', record), \
             patch.object(Storage, 'put', put):
            with ThreadPoolExecutor(max_workers=1) as caller:
                future = caller.submit(strategy._upload_pending, ctx, pending, active)
                try:
                    self.assertTrue(ready.wait(20))
                    self.assertEqual(len(compressed), 2)
                    self.assertEqual(len(list(staging.glob('sambinha-batch-*'))), 2)
                    self.assertTrue(all(path.is_file() for path in archives))
                    self.assertEqual(len(list(self.source.iterdir())), 9)
                finally:
                    release.set()
                future.result(timeout=30)
        self.assertEqual(len(compressed), 9)
        self.assertEqual(list(staging.iterdir()), [])

    def test_denied_upload_cleans_staging_without_compressing_pending_batches(self):
        ctx, pending, active = self.context(workers=1)
        staging = self.root / 'staging'
        staging.mkdir()
        with patch('tempfile.tempdir', str(staging)), \
             patch.object(strategy, 'compress', wraps=strategy.compress) as compressed, \
             patch.object(Storage, 'put', side_effect=BackupError('AccessDenied')):
            with self.assertRaisesRegex(BackupError, 'AccessDenied'):
                strategy._upload_pending(ctx, pending, active)
        self.assertEqual(compressed.call_count, 1)
        self.assertEqual(list(staging.iterdir()), [])
        self.assertEqual(len(list(self.source.iterdir())), 9)
        self.assertFalse(ctx.state_path.exists())

    def test_direct_source_archive_is_rejected_if_member_listing_is_invalid(self):
        ctx, pending, active = self.context(count=1)
        work = self.root / 'work'
        work.mkdir()
        original_run = ctx.runner.run

        def run(args, **kwargs):
            if args[1] == 'l':
                return ''  # A valid .7z with an invalid member listing.
            return original_run(args, **kwargs)

        with patch.object(ctx.runner, 'run', run):
            with self.assertRaisesRegex(BackupError, 'Lote incompleto'):
                compress(ctx, pending[0], work)
        self.assertFalse((work / 'snapshot').exists())
        self.assertTrue((work / 'archive.7z').is_file())
