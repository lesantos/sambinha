"""Unit tests for the argparse CLI (no rclone needed)."""
import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import fields
from pathlib import Path
from unittest.mock import patch

from sambinha import __version__
from sambinha.cli import ACTIONS, main, parser
from sambinha.cli_options import OVERRIDES
from sambinha.config import Config, Job
from sambinha.errors import BackupError
from sambinha.process import Terminated


def parse_error(argv):
    """Parse expecting a usage error (exit 2); returns stderr text."""
    err = io.StringIO()
    with redirect_stderr(err), unittest.TestCase().assertRaises(SystemExit) as caught:
        parser().parse_args(argv)
    assert caught.exception.code == 2
    return err.getvalue()


class OptionTableTests(unittest.TestCase):
    def test_every_override_is_a_config_or_job_field(self):
        known = {f.name for f in fields(Config)} | {f.name for f in fields(Job)}
        # 'job' only selects a job; load() pops it before field validation.
        self.assertEqual(set(OVERRIDES) - known, {"job"})

    def test_every_option_has_help_and_a_flag(self):
        flags = {a.dest: a for a in parser()._actions}
        for name, option in OVERRIDES.items():
            with self.subTest(name=name):
                self.assertTrue(option.help)
                self.assertIn(name, flags)
                self.assertTrue(flags[name].help)

    def test_option_types_build_matching_arguments(self):
        args = parser().parse_args(["--timeout", "5", "--max-megabytes", "0.5", "--no-dry-run", "--verification-mode", "hash"])
        self.assertEqual((args.timeout, args.max_megabytes, args.dry_run, args.verification_mode), (5, 0.5, False, "hash"))


class ParserStructureTests(unittest.TestCase):
    def test_no_subcommand_means_backup(self):
        args = parser().parse_args(["--config", "x.toml"])
        self.assertEqual((args.action, args.config), ("backup", Path("x.toml")))
        self.assertEqual(parser().parse_args([]).action, "backup")

    def test_every_action_accepts_shared_options_after_it(self):
        for action in ACTIONS:
            with self.subTest(action=action):
                args = parser().parse_args([action, "--job", "j", "--config", "c.toml", "--dry-run"])
                self.assertEqual((args.action, args.job, args.dry_run), (action, "j", True))

    def test_backup_only_options(self):
        args = parser().parse_args(["backup", "--fresh-start", "--prune-orphans", "--adopt-identity", "--allow-mass-deletion"])
        self.assertTrue(args.fresh_start and args.prune_orphans and args.adopt_identity and args.allow_mass_deletion)
        self.assertTrue(parser().parse_args(["--fresh-start"]).fresh_start)
        for action in ("restore", "verify-local-metadata", "verify-destination-metadata", "verify-destination-integrity", "rebalance"):
            for flag in ("--fresh-start", "--prune-orphans", "--adopt-identity"):
                with self.subTest(action=action, flag=flag):
                    self.assertIn("unrecognized arguments", parse_error([action, flag]))

    def test_rebalance_keeps_mass_deletion_guard_only(self):
        self.assertTrue(parser().parse_args(["rebalance", "--allow-mass-deletion"]).allow_mass_deletion)

    def test_restore_only_options(self):
        args = parser().parse_args(["restore", "--output", "/tmp/o", "--path", "a", "--path", "b"])
        self.assertEqual((args.output, args.paths), (Path("/tmp/o"), ["a", "b"]))
        for argv in (["--output", "/tmp/o"], ["--path", "a"], ["backup", "--output", "o"], ["rebalance", "--path", "a"],
                     ["verify-local-metadata", "--output", "o"]):
            with self.subTest(argv=argv):
                self.assertRegex(parse_error(argv), "unrecognized arguments|só existe em 'sambinha restore'")

    def test_old_flag_order_suggests_new_one(self):
        message = parse_error(["--job", "x", "restore", "--output", "/tmp/o"])
        self.assertIn("sambinha restore --job x --output /tmp/o", message)
        self.assertIn("deve vir antes das opções", message)

    def test_option_value_equal_to_action_word_is_not_old_order(self):
        self.assertEqual(parser().parse_args(["--job", "restore"]).job, "restore")
        self.assertEqual(parser().parse_args(["restore", "--job", "backup"]).job, "backup")

    def test_invalid_choice_and_unknown_option(self):
        self.assertIn("invalid choice", parse_error(["--verification-mode", "x"]))
        self.assertIn("invalid choice", parse_error(["--max-bytes", "1"]))
        self.assertIn("unrecognized arguments", parse_error(["restore", "--max-bytes", "1"]))

    def test_version_and_help(self):
        out = io.StringIO()
        with redirect_stdout(out), self.assertRaises(SystemExit) as caught:
            parser().parse_args(["--version"])
        self.assertEqual((caught.exception.code, out.getvalue().strip()), (0, __version__))
        out = io.StringIO()
        with redirect_stdout(out), self.assertRaises(SystemExit):
            parser().parse_args(["restore", "--help"])
        self.assertIn("--output", out.getvalue())

    def test_root_help_lists_every_action(self):
        out = io.StringIO()
        with redirect_stdout(out), self.assertRaises(SystemExit):
            parser().parse_args(["--help"])
        for action in ACTIONS:
            self.assertIn(action, out.getvalue())


class MainDispatchTests(unittest.TestCase):
    def run_main(self, argv, **mocks):
        with patch("sambinha.cli.load") as load, patch("sambinha.cli.run", **mocks.get("run", {})) as run, \
                patch("sambinha.cli.recover", **mocks.get("recover", {})) as recover, redirect_stderr(io.StringIO()):
            return main(argv), load, run, recover

    def test_default_runs_backup_with_flags_and_overrides(self):
        code, load, run, recover = self.run_main(
            ["--config", "c.toml", "--job", "j", "--timeout", "7", "--fresh-start", "--allow-mass-deletion"],
            run={"return_value": 0})
        self.assertEqual(code, 0)
        path, overrides = load.call_args.args
        self.assertEqual((path, overrides["job"], overrides["timeout"], overrides["dry_run"]), (Path("c.toml"), "j", 7, None))
        self.assertEqual(set(overrides), set(OVERRIDES))
        self.assertEqual(run.call_args.kwargs, {"allow_mass_deletion": True, "action": "backup", "adopt_identity": False,
                                                "prune_orphans": False, "fresh_start": True})
        recover.assert_not_called()

    def test_rebalance_runs_engine(self):
        _, _, run, recover = self.run_main(["rebalance"], run={"return_value": 0})
        self.assertEqual(run.call_args.kwargs["action"], "rebalance")
        recover.assert_not_called()

    def test_restore_and_verify_go_to_recovery(self):
        code, _, run, recover = self.run_main(["restore", "--output", "/tmp/o", "--path", "p"], recover={"return_value": 1})
        self.assertEqual(code, 1)
        self.assertEqual(recover.call_args.args[1], "restore")
        self.assertEqual((recover.call_args.kwargs["output"], recover.call_args.kwargs["selectors"]), (Path("/tmp/o"), ["p"]))
        run.assert_not_called()
        for action in ACTIONS[3:]:
            with self.subTest(action=action):
                _, _, _, recover = self.run_main([action], recover={"return_value": 0})
                self.assertEqual(recover.call_args.args[1], action)
                self.assertIsNone(recover.call_args.kwargs["output"])

    def test_exit_codes(self):
        for error, code, text in ((BackupError("falha"), 2, "falha"), (OSError("disco"), 2, "disco"),
                                  (Terminated(), 143, "SIGTERM"), (KeyboardInterrupt(), 130, "interrompida")):
            with self.subTest(error=error), patch("sambinha.cli.load"), patch("sambinha.cli.run", side_effect=error), \
                    redirect_stderr(io.StringIO()) as err:
                self.assertEqual(main([]), code)
                self.assertIn(text, err.getvalue())

    def test_config_error_returns_two(self):
        with patch("sambinha.cli.load", side_effect=BackupError("toml")), redirect_stderr(io.StringIO()) as err:
            self.assertEqual(main(["verify-local-metadata"]), 2)
        self.assertIn("sambinha: toml", err.getvalue())


if __name__ == "__main__":
    unittest.main()
