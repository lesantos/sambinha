import unittest
from dataclasses import replace

from tests.helpers import REAL_TOOLS, Sandbox


@unittest.skipUnless(REAL_TOOLS, 'rclone required')
class MassDeletionTests(Sandbox):
    def prepare(self, logic):
        self.destination = self.root / ('destination-' + logic)
        for name in ('a', 'b', 'c', 'd'):
            self.file(name)
        changes = ({'max_delete': 1} if logic == 'simple' else
                   {'deletion_min_files': 2, 'max_delete_percent': 50})
        self.config = replace(self.config, jobs=(replace(self.config.jobs[0], logic=logic, destination=str(self.destination), **changes),))
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
