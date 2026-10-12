from dataclasses import replace
import os
from pathlib import Path
import threading
import unittest
from unittest.mock import Mock, patch

from sambinha.batches import plan
from sambinha.config import validate
from sambinha.engine import preflight
from sambinha.filtering import Selection
from sambinha.inventory import fingerprint, scan
from sambinha.profiles import category, settings
from tests.helpers import REAL_TOOLS, Sandbox, contents


class InventoryPerformanceTests(Sandbox):
    def test_cache_avoids_reads_but_ctime_detects_same_size_same_mtime_edit(self):
        path = self.file('file', b'old')
        cache = {}
        before = scan(self.source, cache_out=cache)
        with patch('sambinha.inventory.fingerprint', wraps=fingerprint) as hash_file:
            self.assertEqual(scan(self.source, cache=cache), before)
            hash_file.assert_not_called()
            path.write_bytes(b'new')
            os.utime(path, ns=(before[0].mtime_ns, before[0].mtime_ns))
            after = scan(self.source, cache=cache)
            hash_file.assert_called_once()
        self.assertNotEqual(before[0].sha256, after[0].sha256)

    def test_each_job_has_its_own_inventory_even_with_the_same_source(self):
        self.file('file')
        job = self.config.jobs[0]
        config = replace(self.config, jobs=(job, replace(job, name='second', destination=str(self.root / 'second'))))
        with patch('sambinha.engine.scan', wraps=scan) as inventory:
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

    def test_profiles_classify_case_insensitive_extensions_and_separate_new_batches(self):
        for name, expected in (('README', 'text'), ('notes.MD', 'text'), ('movie.MP4', 'video'),
                               ('photo.JPEG', 'photo'), ('image.dng', 'photo'), ('blob.bin', 'other')):
            self.assertEqual(category(name), expected)
            self.file(name)
        files = scan(self.source)
        batches = plan(files, 100000, 1000, category=category)
        for batch in batches:
            self.assertEqual(len({category(f.path) for f in batch.files}), 1)
            kind, level, threads = settings(self.config.jobs[0], batch.files)
            self.assertEqual(level, getattr(self.config.jobs[0], f'compression_{kind}_level'))
            self.assertEqual(threads, getattr(self.config.jobs[0], f'compression_{kind}_threads'))


@unittest.skipUnless(REAL_TOOLS, 'rclone and 7z required')
class PerformanceIntegrationTests(Sandbox):
    def test_full_scan_interval_and_opt_out_rehash_unchanged_source(self):
        self.file('file')
        self.assertEqual(self.execute(now=1000), 0)
        with patch('sambinha.inventory.fingerprint', wraps=fingerprint) as hash_file:
            self.assertEqual(self.execute(now=1001), 0)
            hash_file.assert_not_called()
            self.assertEqual(self.execute(now=1000 + 7 * 86400), 0)
            hash_file.assert_called_once()
        with patch('sambinha.inventory.fingerprint', wraps=fingerprint) as hash_file:
            self.assertEqual(self.execute(replace(self.config, fingerprint_cache=False)), 0)
            hash_file.assert_called_once()

    def test_many_simple_files_use_bounded_bulk_copy_and_restore_exactly(self):
        for i in range(8):
            self.file(f'dir/file-{i}', str(i).encode())
        from sambinha.storage import Storage
        self.config = replace(self.config, transfer_group_size=3)
        original = Storage.put_many
        groups = []
        def bulk(storage, root, files):
            groups.append(len(files))
            return original(storage, root, files)
        with patch.object(Storage, 'put_many', bulk):
            self.assertEqual(self.execute(), 0, self.output.getvalue())
        self.assertEqual(groups, [3, 3, 2])
        self.assertEqual(contents(self.destination), contents(self.source))

    def test_batch_workers_run_concurrently_with_bounded_parallelism(self):
        for i in range(6):
            self.file(f'dir/file-{i}.txt', str(i).encode())
        self.config = replace(self.config, batch_workers=2,
                              jobs=(replace(self.config.jobs[0], logic='batch', max_files=2),))
        from sambinha.engine import upload_batch
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
        with patch('sambinha.engine.upload_batch', upload):
            self.assertEqual(self.execute(), 0, self.output.getvalue())
        self.assertEqual(peak, 2)
        self.assertEqual(len(self.state()['active']), 3)

    def test_modifying_one_file_does_not_rebuild_other_lots(self):
        for i in range(6):
            self.file(f'dir/{i}.txt', str(i).encode())
        self.config = replace(self.config, jobs=(replace(self.config.jobs[0], logic='batch', max_files=2),))
        self.assertEqual(self.execute(), 0)
        before = self.state()['active']
        (self.source / 'dir/0.txt').write_bytes(b'updated')
        self.assertEqual(self.execute(now=2000), 0)
        summary = next(e for e in self.events if e['event'] == 'summary')
        self.assertEqual(summary['reused_batches'], 2)
        self.assertEqual(summary['rebuilt_batches'], 1)
        self.assertEqual(len(set(before) & set(self.state()['active'])), 2)
