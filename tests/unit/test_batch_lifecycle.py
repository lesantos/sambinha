import os
from dataclasses import replace

from sambinha.config_validation import validate
from sambinha.errors import ConfigError
from sambinha.inventory import scan
from tests.helpers import Sandbox


class BatchOptionsAndTraversalTests(Sandbox):
    def test_threshold_validation_and_uniform_defaults(self):
        job = self.config.jobs[0]
        self.assertEqual((job.compression_level, job.compression_threads), (3, 2))
        self.assertEqual((job.individual_batch_percent, job.batch_size_alert_percent), (100, 200))
        for name, values in {'individual_batch_percent': (0, -1, 101, True, float('nan'), float('inf')),
                             'batch_size_alert_percent': (-1, True, float('nan'), float('inf'))}.items():
            for value in values:
                with self.subTest(name=name, value=value), self.assertRaises(ConfigError):
                    validate(replace(self.config, jobs=(replace(job, **{name: value}),)))
        validate(replace(self.config, jobs=(replace(job, individual_batch_percent=0.5, batch_size_alert_percent=0),)))

    def test_mixed_sibling_mtimes_drive_depth_first_order(self):
        paths = [self.file('older-directory/recent-file'), self.file('root-old'),
                 self.file('newer-directory/ancient-file'), self.file('root-new')]
        for path, stamp in zip(paths, (900, 20, 1, 40), strict=False):
            os.utime(path, ns=(stamp, stamp))
        for directory, stamp in (('older-directory', 10), ('newer-directory', 30)):
            os.utime(self.source / directory, ns=(stamp, stamp))
        self.assertEqual([f.path for f in scan(self.source, mode='simple')],
                         ['older-directory/recent-file', 'root-old', 'newer-directory/ancient-file', 'root-new'])

    def test_mtime_ties_use_names_and_do_not_follow_links(self):
        for name in ('z/a', 'b', 'a'):
            path = self.file(name)
            os.utime(path, ns=(100, 100))
        os.utime(self.source / 'z', ns=(100, 100))
        (self.source / 'link').symlink_to(self.source / 'z', target_is_directory=True)
        self.assertEqual([f.path for f in scan(self.source)], ['a', 'b', 'z/a'])
