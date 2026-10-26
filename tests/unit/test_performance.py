import os
from dataclasses import replace
from unittest.mock import Mock, patch

from sambinha.batches import plan
from sambinha.config_validation import validate
from sambinha.filtering import Selection
from sambinha.inventory import fingerprint, scan
from sambinha.preflight import preflight
from sambinha.profiles import settings
from tests.helpers import Sandbox, tick


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
