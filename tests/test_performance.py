import os
import threading
import unittest
from dataclasses import replace
from unittest.mock import Mock, patch

from sambinha.batches import plan
from sambinha.config_validation import validate
from sambinha.filtering import Selection
from sambinha.inventory import fingerprint, scan
from sambinha.preflight import preflight
from sambinha.profiles import settings
from tests.helpers import REAL_TOOLS, Sandbox, contents, tick


class InventoryPerformanceTests(Sandbox):
    def test_cache_avoids_reads_but_ctime_detects_same_size_same_mtime_edit(self):
        path = self.file('file', b'old')
        cache = {}
        before = scan(self.source, cache_out=cache)
        with patch('sambinha.inventory.fingerprint', wraps=fingerprint) as hash_file:
            self.assertEqual(scan(self.source, cache=cache), before)
            hash_file.assert_not_called()
            tick()
            path.write_bytes(b'new')
            os.utime(path, ns=(before[0].mtime_ns, before[0].mtime_ns))
            after = scan(self.source, cache=cache)
            hash_file.assert_called_once()
        self.assertNotEqual(before[0].sha256, after[0].sha256)

    def test_each_job_has_its_own_inventory_even_with_the_same_source(self):
        self.file('file')
        job = replace(self.config.jobs[0], logic='batch')
        config = replace(self.config, jobs=(job, replace(job, name='second', destination=str(self.root / 'second'))))
        with patch('sambinha.preflight.scan', wraps=scan) as inventory:
            result = preflight(validate(config), Mock(), now=1000)
        self.assertEqual(inventory.call_count, 2)
        self.assertEqual(result['test'][0], result['second'][0])

    def test_filter_cache_reuses_rclone_results_and_does_not_leak_mutable_sets(self):
        rules = self.root / 'rules'
        rules.write_text('*.tmp\n')
        selection = Selection(rules, self.config, Mock())
        with patch.object(selection, 'listing', return_value=[{'Path': 'keep'}]) as listing:
            first = selection.include_paths(['keep', 'omit.tmp'])
            first.clear()
            self.assertEqual(selection.include_paths(['omit.tmp', 'keep']), {'keep'})
        self.assertEqual(listing.call_count, 1)

    def test_one_profile_packs_mixed_file_types_together(self):
        for name in ('README', 'notes.MD', 'movie.MP4', 'photo.JPEG', 'image.dng', 'blob.bin'):
            self.file(name)
        files = scan(self.source)
        batches = plan(files, 100000)
        self.assertEqual(len(batches), 1)
        self.assertEqual(settings(self.config.jobs[0], batches[0].files), ('uniform', 3, 2))


@unittest.skipUnless(REAL_TOOLS, 'rclone and 7z required')
class PerformanceIntegrationTests(Sandbox):
    def test_full_scan_interval_and_opt_out_rehash_unchanged_source(self):
        self.config = replace(self.config, jobs=(replace(self.config.jobs[0], logic='batch'),))
        self.file('file')
        self.assertEqual(self.execute(now=1000), 0)
        with patch('sambinha.inventory.fingerprint', wraps=fingerprint) as hash_file:
            self.assertEqual(self.execute(now=1001), 0)
            hash_file.assert_not_called()
            self.assertEqual(self.execute(now=1000 + 7 * 86400), 0)
            self.assertGreaterEqual(hash_file.call_count, 1)
        with patch('sambinha.inventory.fingerprint', wraps=fingerprint) as hash_file:
            self.assertEqual(self.execute(replace(self.config, fingerprint_cache=False)), 0)
            self.assertGreaterEqual(hash_file.call_count, 1)

    def test_many_simple_files_use_one_native_sync(self):
        for i in range(8):
            self.file(f'dir/file-{i}', str(i).encode())
        from sambinha.process import Runner
        operations = []
        original = Runner.run
        def record(runner, args, **kwargs):
            if len(args) > 1 and args[1] in {'copy', 'sync'}:
                operations.append(args[1])
            return original(runner, args, **kwargs)
        with patch.object(Runner, 'run', record):
            self.assertEqual(self.execute(), 0, self.output.getvalue())
        self.assertEqual(operations, ['sync'])
        self.assertEqual(contents(self.destination), contents(self.source))

    def test_batch_workers_run_concurrently_with_bounded_parallelism(self):
        for i in range(6):
            self.file(f'dir/file-{i}.txt', str(i).encode())
        self.config = replace(self.config, batch_workers=2,
                              jobs=(replace(self.config.jobs[0], logic='batch', max_megabytes=2 / 1024 ** 2),))
        from sambinha.strategies.batch import upload_batch
        gate = threading.Barrier(2)
        mutex = threading.Lock()
        running, peak, calls = 0, 0, 0
        def upload(ctx, item):
            nonlocal running, peak, calls
            with mutex:
                calls += 1
                number = calls
                running += 1
                peak = max(peak, running)
            try:
                if number <= 2:
                    gate.wait(timeout=10)
                return upload_batch(ctx, item)
            finally:
                with mutex:
                    running -= 1
        with patch('sambinha.strategies.batch.upload_batch', upload):
            self.assertEqual(self.execute(), 0, self.output.getvalue())
        self.assertEqual(peak, 2)
        self.assertEqual(len(self.state()['active']), 3)

    def test_modifying_one_file_does_not_rebuild_other_lots(self):
        for i in range(6):
            self.file(f'dir/{i}.txt', str(i).encode())
        self.config = replace(self.config, jobs=(replace(self.config.jobs[0], logic='batch', max_megabytes=2 / 1024 ** 2),))
        self.assertEqual(self.execute(), 0)
        before = self.state()['active']
        (self.source / 'dir/0.txt').write_bytes(b'updated')
        self.assertEqual(self.execute(now=2000), 0)
        summary = next(e for e in self.events if e['event'] == 'summary')
        self.assertEqual(summary['reused_batches'], 2)
        self.assertEqual(summary['rebuilt_batches'], 1)
        self.assertEqual(len(set(before) & set(self.state()['active'])), 2)

    def test_verify_rereads_only_changed_files_with_cache_and_everything_without(self):
        for i in range(6):
            self.file(f'dir/{i}.txt', str(i).encode())
        self.config = replace(self.config, jobs=(replace(self.config.jobs[0], logic='batch', max_megabytes=2 / 1024 ** 2),))
        self.assertEqual(self.execute(), 0)
        (self.source / 'dir/0.txt').write_bytes(b'updated')

        def source_reads(config, now):
            from sambinha import inventory
            reads, original = [], inventory._fingerprint
            def spy(relative, *args):
                reads.append(relative)
                return original(relative, *args)
            with patch('sambinha.inventory._fingerprint', side_effect=spy):
                self.assertEqual(self.execute(config, now=now), 0)
            return [r for r in reads if r.startswith('dir/')]

        cached = source_reads(self.config, 2000)
        # Only the scan hashes the changed file; no verify pass reads any content.
        self.assertEqual(cached, ['dir/0.txt'])
        (self.source / 'dir/1.txt').write_bytes(b'again')
        full = source_reads(replace(self.config, fingerprint_cache=False), 3000)
        # Without the cache the scan hashes all six and each verify pass re-reads them again.
        self.assertGreater(len(full), 6 + 6)

    def test_unchanged_batch_run_reads_each_local_control_once(self):
        from sambinha.metadata import read_control
        for i in range(4):
            self.file(f'dir/{i}.txt', str(i).encode())
        self.config = replace(self.config, jobs=(replace(self.config.jobs[0], logic='batch', max_megabytes=2 / 1024 ** 2),))
        self.assertEqual(self.execute(), 0)
        controls = len(self.state()['active'])
        self.assertGreater(controls, 1)
        (self.source / 'dir/0.txt').write_bytes(b'updated')
        with patch('sambinha.metadata.read_control', wraps=read_control) as reads:
            self.assertEqual(self.execute(now=2000), 0)
        # Before memoization Context and batch() each read every control.
        self.assertLessEqual(reads.call_count, controls)
