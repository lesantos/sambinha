"""Three-tier compression with real 7-Zip: member methods, one command per group, races and interruptions."""
import io
import json
import os
import subprocess
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from unittest.mock import patch

from sambinha.config import with_job_overrides
from sambinha.config_validation import validate
from sambinha.engine import run
from sambinha.extensions import DEFAULT_COMPRESSED_EXTENSIONS, DEFAULT_TEXT_EXTENSIONS, NONE
from sambinha.process import Runner
from sambinha.recovery import recover
from tests.helpers import COMPRESSOR, REAL_TOOLS, Sandbox, contents, restore_batches, tick

TEXT = (b"The quick brown fox jumps over the lazy dog.\n" * 400)
PATTERN = bytes(range(251)) * 80
RANDOM = {name: os.urandom(20000) for name in ("photo.jpg", "bundle.zip", "clip.mp4")}


@unittest.skipUnless(REAL_TOOLS, "rclone and 7z required")
class TieredCompressionTests(Sandbox):
    def setUp(self):
        super().setUp()
        self.temp_root = self.root / "tmp"
        self.temp_root.mkdir()
        self.config = replace(self.config, temp_dir=self.temp_root)
        self.use_batch(compression_threads=1)
        self.config = replace(self.config, jobs=(replace(self.config.jobs[0], max_megabytes=100),))

    def seed(self):
        for name in ("notes.txt", "app.log", "README", ".hidden"):
            self.file(name, TEXT)
        for name, data in RANDOM.items():
            self.file(name, data)
        for name in ("tool.bin", "prog.exe"):
            self.file(name, PATTERN)

    def execute(self, config=None, now=1000, **kwargs):
        self.output = io.StringIO()
        with redirect_stdout(self.output):
            code = run(validate(with_job_overrides(config or self.config, log_terminal_level="detailed")),
                       clock=lambda: now, notifier=lambda *args: self.notifications.append(args), **kwargs)
        self.events = [json.loads(line) for line in self.output.getvalue().splitlines()]
        return code

    def state(self):
        return json.loads((self.config.state_dir / "test" / "state.json").read_text())

    def recover(self, action, **kwargs):
        with redirect_stdout(io.StringIO()):
            return recover(validate(self.config), action, **kwargs)

    def methods(self):
        """Member path -> Method of the single uploaded archive, as listed by 7z."""
        keys = list(self.state()["active"])
        self.assertEqual(len(keys), 1)
        archive = self.destination / keys[0]
        self.assertEqual(subprocess.run([COMPRESSOR, "t", str(archive)], capture_output=True).returncode, 0)
        listing = subprocess.run([COMPRESSOR, "l", "-slt", "-ba", str(archive)], capture_output=True, text=True, check=True).stdout
        result = {}
        for record in listing.strip().split("\n\n"):
            fields = dict(line.split(" = ", 1) for line in record.splitlines() if " = " in line)
            result[fields["Path"].removeprefix("./")] = fields.get("Method", "")
        return result

    def assert_round_trip(self):
        expected = contents(self.source)
        self.assertEqual(restore_batches(self.state(), self.destination, self.root / "restored"), expected)
        self.assertEqual(self.recover("verify-destination-integrity"), 0)
        output = self.root / "restore-api"
        self.assertEqual(self.recover("restore", output=output), 0)
        self.assertEqual(contents(output), expected)

    def count_adds(self):
        counter, original = [], Runner.run

        def spy(runner, args, **kwargs):
            if len(args) > 1 and args[1] == "a":
                counter.append(list(args))
            return original(runner, args, **kwargs)
        return counter, patch.object(Runner, "run", spy)

    def test_each_tier_gets_its_method_and_everything_round_trips(self):
        self.seed()
        adds, spy = self.count_adds()
        with spy:
            self.assertEqual(self.execute(), 0, self.output.getvalue())
        self.assertEqual([next(a for a in c if a.startswith("-mx=")) for c in adds], ["-mx=1", "-mx=1", "-mx=0"])
        methods = self.methods()
        for name in ("notes.txt", "app.log", "README", ".hidden", "tool.bin", "prog.exe"):
            with self.subTest(name=name):
                self.assertIn("LZMA2", methods[name])
        for name in RANDOM:
            with self.subTest(name=name):
                self.assertIn("Copy", methods[name])
        start = next(e for e in self.events if e["event"] == "batch_start")
        self.assertEqual({k: v["files"] for k, v in start["compression_groups"].items()},
                         {"text": 4, "other": 2, "compressed": 3})
        self.assert_round_trip()

    def test_none_keyword_in_the_compressed_list_stores_extensionless_files(self):
        self.seed()
        job = replace(self.config.jobs[0],
                      compression_text_extensions=tuple(e for e in DEFAULT_TEXT_EXTENSIONS if e != NONE),
                      compression_compressed_extensions=(*DEFAULT_COMPRESSED_EXTENSIONS, NONE))
        self.assertEqual(self.execute(replace(self.config, jobs=(job,))), 0, self.output.getvalue())
        methods = self.methods()
        self.assertIn("Copy", methods["README"])
        self.assertIn("Copy", methods[".hidden"])
        self.assertIn("LZMA2", methods["notes.txt"])
        self.assert_round_trip()

    def test_single_tier_batches_run_exactly_one_command(self):
        for names, data in ((("a.txt", "b.md"), TEXT), (("a.zip", "b.jpg"), RANDOM["bundle.zip"])):
            for name in names:
                self.file(name, data)
            adds, spy = self.count_adds()
            with spy:
                self.assertEqual(self.execute(), 0, self.output.getvalue())
            self.assertEqual(len(adds), 1)
            for name in names:
                (self.source / name).unlink()

    def test_zip_format_mixes_stored_and_deflated_members(self):
        self.seed()
        self.config = replace(self.config, jobs=(replace(self.config.jobs[0], archive_format="zip"),))
        self.assertEqual(self.execute(), 0, self.output.getvalue())
        methods = self.methods()
        self.assertEqual(methods["clip.mp4"], "Store")
        self.assertNotEqual(methods["notes.txt"], "Store")
        self.assertNotEqual(methods["tool.bin"], "Store")
        self.assert_round_trip()

    def test_hash_mode_crc_cross_check_passes_with_several_commands(self):
        self.seed()
        self.assertEqual(self.config.jobs[0].verification_mode, "hash")
        adds, spy = self.count_adds()
        with spy:
            self.assertEqual(self.execute(), 0, self.output.getvalue())
        self.assertEqual(len(adds), 3)
        self.assertFalse([e for e in self.events if e["event"] == "error"])

    def test_source_modified_between_commands_fails_and_publishes_nothing(self):
        self.seed()
        victim = self.source / "clip.mp4"
        original, calls = Runner.run, []

        def run_and_touch(runner, args, **kwargs):
            before = os.path.exists(args[-1]) if len(args) > 1 and args[1] == "a" and not args[-1].startswith("@") else None
            result = original(runner, args, **kwargs)
            if len(args) > 1 and args[1] == "a":
                calls.append(args)
                if len(calls) % 3 == 1:  # after the first group's command, every attempt
                    tick()
                    victim.write_bytes(os.urandom(len(RANDOM["clip.mp4"])))
            del before
            return result
        with patch.object(Runner, "run", run_and_touch):
            self.assertEqual(self.execute(), 1)
        self.assertTrue(any(e["event"] == "error" for e in self.events))
        self.assertEqual(list(self.destination.rglob("*.7z")) if self.destination.exists() else [], [])
        self.assertFalse((self.config.state_dir / "test" / "state.json").exists())
        self.assertEqual(list(self.temp_root.iterdir()), [])

    def test_interruption_between_commands_leaves_nothing_and_rerun_succeeds(self):
        self.seed()
        original, seen = Runner.run, []

        def interrupt_second_add(runner, args, **kwargs):
            if len(args) > 1 and args[1] == "a":
                seen.append(args)
                if len(seen) == 2:
                    raise KeyboardInterrupt
            return original(runner, args, **kwargs)
        with patch.object(Runner, "run", interrupt_second_add), self.assertRaises(KeyboardInterrupt):
            self.execute()
        self.assertEqual(list(self.temp_root.iterdir()), [])
        self.assertEqual(list(self.destination.rglob("*.7z")) if self.destination.exists() else [], [])
        self.assertEqual(self.execute(now=2000), 0, self.output.getvalue())
        self.assert_round_trip()
        adds, spy = self.count_adds()
        with spy:
            self.assertEqual(self.execute(now=2001), 0)
        self.assertEqual(adds, [])

    def test_rebalance_of_a_mixed_batch_keeps_tiers_and_bytes(self):
        self.seed()
        self.assertEqual(self.execute(), 0, self.output.getvalue())
        expected = contents(self.source)
        import shutil
        shutil.rmtree(self.source)
        small = replace(self.config.jobs[0], max_megabytes=40000 / 1024 ** 2)
        config = replace(self.config, jobs=(small,))
        adds, spy = self.count_adds()
        with spy:
            self.assertEqual(self.execute(config, now=3000, action="rebalance"), 0, self.output.getvalue())
        self.assertGreater(len(adds), 1)
        state = self.state()
        self.assertGreater(len(state["active"]), 1)
        self.assertEqual(restore_batches(state, self.destination, self.root / "rebalanced"), expected)
