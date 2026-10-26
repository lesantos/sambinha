import importlib
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from unittest import skipUnless
from unittest.mock import patch

from sambinha.archives import compress
from sambinha.errors import BackupError
from sambinha.storage import Storage
from tests.helpers import REAL_TOOLS, BatchContext, Sandbox

strategy = importlib.import_module('sambinha.strategies.batch')




@skipUnless(REAL_TOOLS, 'rclone and 7z required')
class BatchStagingIntegrationTests(BatchContext, Sandbox):
    def test_blocked_s3_uploads_hold_only_archives_and_do_not_start_more_batches(self):
        ctx, pending, active = self.context()
        staging = self.root / 'staging'
        staging.mkdir()
        ctx.config = replace(ctx.config, temp_dir=staging)
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

        with patch.object(strategy, 'compress', record), patch.object(Storage, 'put', put):
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
        ctx, pending, _active = self.context(count=1)
        work = self.root / 'work'
        work.mkdir()
        original_run = ctx.runner.run

        def run(args, **kwargs):
            if args[1] == 'l':
                return ''  # A valid .7z with an invalid member listing.
            return original_run(args, **kwargs)

        with patch.object(ctx.runner, 'run', run), self.assertRaisesRegex(BackupError, 'Lote incompleto'):
            compress(ctx, pending[0], work)
        self.assertFalse((work / 'snapshot').exists())
        self.assertTrue((work / 'archive.7z').is_file())

    def crc_listing(self, tamper):
        ctx, pending, _active = self.context(count=2)
        work = self.root / 'work'
        work.mkdir()
        original_run = ctx.runner.run

        def run(args, **kwargs):
            output = original_run(args, **kwargs)
            return tamper(output) if args[1] == 'l' else output

        return ctx, pending, work, run

    def test_7z_crc_is_cross_checked_against_the_inventory_crc(self):
        ctx, pending, work, run = self.crc_listing(lambda output: output)
        self.assertTrue(all(f.crc32 is not None for f in pending[0].files))
        with patch.object(ctx.runner, 'run', run):
            compress(ctx, pending[0], work)  # A genuine listing agrees with the inventory.

    def test_7z_crc_mismatch_names_the_member(self):
        victim = 'CRC = 00000000'
        def tamper(output):
            lines = output.splitlines()
            index = next(i for i, line in enumerate(lines) if line.startswith('CRC = '))
            lines[index] = victim
            return '\n'.join(lines)
        ctx, pending, work, run = self.crc_listing(tamper)
        with patch.object(ctx.runner, 'run', run), self.assertRaisesRegex(BackupError, r'CRC32.*\.bin'):
            compress(ctx, pending[0], work)

    def test_missing_7z_crc_is_skipped(self):
        ctx, pending, work, run = self.crc_listing(
            lambda output: '\n'.join(line for line in output.splitlines() if not line.startswith('CRC = ')))
        with patch.object(ctx.runner, 'run', run):
            compress(ctx, pending[0], work)
