import io
import os
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from typing import ClassVar
from unittest.mock import patch

from sambinha import archives
from sambinha.config_validation import validate
from sambinha.recovery import recover
from tests.helpers import COMPRESSOR, REAL_TOOLS, Sandbox, contents


@unittest.skipUnless(REAL_TOOLS, 'Install rclone and 7z for real local integration')
class SpecialNameBatchTests(Sandbox):
    """Names that 7-Zip listfiles or console tools mangle still round-trip."""

    batch_defaults: ClassVar[dict] = {'max_megabytes': 100 / 1024}

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
        listing = subprocess.run([COMPRESSOR, 'l', '-slt', '-ba', str(self.destination / key)],
                                 check=True, capture_output=True, text=True).stdout
        return {line.removeprefix('Path = ').removeprefix('./') for line in listing.split('\n') if line.startswith('Path = ')}

    def test_backslash_in_name_round_trips_through_restore_and_verification(self):
        self.file('a\\b', b'backslash')
        self.file('dir\\x/c\\', b'nested')
        self.use_batch()
        self.roundtrip()
        self.assertEqual(self.members(), {'a\\b', 'dir\\x/c\\'})

    def test_trailing_space_and_tab_in_names_round_trip(self):
        self.file('a ', b'space')
        self.file('c\t', b'tab')
        self.file('dir /x ', b'nested')
        self.file('@at ', b'at')
        self.use_batch()
        self.roundtrip()
        self.assertEqual(self.members(), {'a ', 'c\t', 'dir /x ', '@at '})

    def test_zip_format_keeps_special_names(self):
        self.file('a ', b'space')
        self.file('a\\b', b'backslash')
        self.use_batch(archive_format='zip')
        self.roundtrip()

    def test_listfile_fallback_rejects_names_it_cannot_express(self):
        self.file('ok.txt', b'fine')
        self.file('dir/c\t', b'tab')
        self.use_batch()
        with patch.object(archives, 'ARGUMENT_BUDGET', 0):
            self.assertEqual(self.execute(), 1)
        errors = [e['error'] for e in self.events if e['event'] == 'error']
        self.assertEqual(len(errors), 1)
        self.assertIn(repr('dir/c\t'), errors[0])
        self.assertFalse(list(self.destination.rglob('*.7z')))

    def test_listfile_fallback_still_archives_ordinary_names(self):
        self.file('@leading', b'at')
        self.file('-dash', b'dash')
        self.file(' lead', b'space')
        self.use_batch()
        with patch.object(archives, 'ARGUMENT_BUDGET', 0):
            self.roundtrip()

    @unittest.skipIf(sys.platform == 'darwin', 'APFS rejects non-UTF-8 file names')
    def test_non_utf8_name_fails_naming_the_path_without_crashing(self):
        fd = os.open(os.fsencode(self.source) + b'/bad\xff.txt', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
        os.write(fd, b'payload')
        os.close(fd)
        self.file('good.txt', b'fine')
        self.use_batch()
        self.assertEqual(self.execute(), 1)
        errors = [e['error'] for e in self.events if e['event'] == 'error']
        self.assertEqual(len(errors), 1)
        self.assertIn('bad', errors[0])
        self.assertIn('UTF-8', errors[0])
        self.assertNotIn('surrogates', errors[0])
        self.assertFalse(self.destination.exists() and list(self.destination.rglob('*.7z')))
        self.assertFalse((self.config.state_dir / 'test' / 'state.json').exists())
