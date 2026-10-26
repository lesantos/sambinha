from contextlib import redirect_stdout
from dataclasses import replace
import io
import subprocess
import unittest

from sambinha.config import validate
from sambinha.recovery import recover
from tests.helpers import REAL_TOOLS, Sandbox, contents


@unittest.skipUnless(REAL_TOOLS, 'Install rclone and 7z for real local integration')
class SpecialNameBatchTests(Sandbox):
    """Names that 7-Zip listfiles or console tools mangle still round-trip."""

    def use_batch(self, **values):
        self.config = replace(self.config, jobs=(replace(self.config.jobs[0], logic='batch',
                               max_megabytes=100 / 1024, **values),))

    def roundtrip(self):
        self.assertEqual(self.execute(), 0, self.output.getvalue())
        restored = self.root / 'restored'
        with redirect_stdout(io.StringIO()):
            self.assertEqual(recover(validate(self.config), 'restore', output=restored), 0)
            self.assertEqual(recover(validate(self.config), 'verify-destination-integrity'), 0)
        self.assertEqual(contents(restored), contents(self.source))

    def members(self):
        key = next(iter(self.state()['active']))
        self.assertTrue(key.endswith('.7z'))
        listing = subprocess.run(['7z', 'l', '-slt', '-ba', str(self.destination / key)],
                                 check=True, capture_output=True, text=True).stdout
        return {line.removeprefix('Path = ').removeprefix('./') for line in listing.split('\n') if line.startswith('Path = ')}

    def test_backslash_in_name_round_trips_through_restore_and_verification(self):
        self.file('a\\b', b'backslash')
        self.file('dir\\x/c\\', b'nested')
        self.use_batch()
        self.roundtrip()
        self.assertEqual(self.members(), {'a\\b', 'dir\\x/c\\'})
