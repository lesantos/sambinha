import copy
import unittest
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from unittest.mock import Mock, patch

from sambinha.batches import plan
from sambinha.config import JOB_SCOPED_OPTIONS, Config, Job, with_job_overrides
from sambinha.config_validation import validate
from sambinha.context import Context, ContextRefs, SimpleContext, WorkerView
from sambinha.errors import BackupError
from sambinha.inventory import scan
from sambinha.strategies import batch as strategy
from tests.helpers import Sandbox


class JobAndConfigTests(unittest.TestCase):
    def job(self):
        return Job("name", Path("/src"), "/dst")

    def test_job_positional_construction_and_keyword_rest(self):
        job = Job("name", Path("/src"), "/dst", logic="batch")
        self.assertEqual((job.name, job.source, job.destination, job.logic), ("name", Path("/src"), "/dst", "batch"))

    def test_extra_positional_arguments_are_rejected(self):
        with self.assertRaises(TypeError):
            Job("name", Path("/src"), "/dst", "local")  # type: ignore[misc]
        with self.assertRaises(TypeError):
            Config((self.job(),), Path("state"))  # type: ignore[misc]

    def test_job_and_config_are_immutable_and_slotted(self):
        job, config = self.job(), Config(jobs=(self.job(),))
        for instance, field in ((job, "dry_run"), (config, "timeout")):
            with self.subTest(type=type(instance).__name__):
                with self.assertRaises(FrozenInstanceError):
                    setattr(instance, field, 1)
                self.assertFalse(hasattr(instance, "__dict__"))
                with self.assertRaises((AttributeError, TypeError)):
                    instance.extra = 1  # type: ignore[attr-defined]

    def test_replace_copy_and_field_introspection_still_work(self):
        job = self.job()
        self.assertEqual(replace(job, deleted_days=3).deleted_days, 3)
        self.assertEqual(copy.copy(job), job)
        self.assertEqual(copy.deepcopy(job), job)
        self.assertNotIn("_", Job.__dataclass_fields__)
        self.assertTrue(set(Job.__dataclass_fields__) >= JOB_SCOPED_OPTIONS)
        self.assertTrue(JOB_SCOPED_OPTIONS.isdisjoint(Config.__dataclass_fields__))

    def test_with_job_overrides_applies_to_every_job_and_validates_like_job_fields(self):
        values = dict(deleted_days=4, version_days=3, log_file_level="detailed", log_terminal_level="detailed",
                      verification_mode="hash", dry_run=True, batch_workers=1, recovery_index_versions=5)
        sandbox = Path("/tmp/sambinha-dataclass-test")
        config = Config(jobs=(Job("a", sandbox / "a", str(sandbox / "da")), Job("b", sandbox / "b", str(sandbox / "db"))),
                        state_dir=sandbox / "state", log_dir=sandbox / "logs")
        result = validate(with_job_overrides(config, **values))
        direct = validate(replace(config, jobs=tuple(replace(job, **values) for job in config.jobs)))
        self.assertEqual(result, direct)
        for job in result.jobs:
            for name, value in values.items():
                self.assertEqual(getattr(job, name), value)
        self.assertIs(with_job_overrides(config, deleted_days=None), config)
        self.assertEqual(config.jobs[0].deleted_days, 10)

    def test_with_job_overrides_rejects_non_job_options_and_invalid_values_fail_validation(self):
        config = Config(jobs=(self.job(),))
        with self.assertRaises(TypeError):
            with_job_overrides(config, timeout=1)
        from sambinha.errors import ConfigError
        for name, value in (("deleted_days", -1), ("log_file_level", "loud"), ("verification_mode", "x"),
                            ("version_days", True)):
            with self.subTest(name=name), self.assertRaises(ConfigError):
                validate(with_job_overrides(config, **{name: value}))


class WorkerViewTests(Sandbox):
    def context(self, runner=None):
        self.file("a.bin", b"contents")
        job = replace(self.config.jobs[0], logic="batch", check_source_disk=False, check_destination_disk=False)
        config = replace(self.config, jobs=(job,))
        return Context(config, job, runner or Mock(), Mock(), scan(self.source), 1000), config

    def test_refs_are_frozen_and_properties_delegate(self):
        ctx, config = self.context()
        self.assertIsInstance(ctx.refs, ContextRefs)
        with self.assertRaises(FrozenInstanceError):
            ctx.refs.job = None  # type: ignore[misc]
        self.assertIs(ctx.config, config)
        self.assertEqual(ctx.directory, config.state_dir / "test")
        new_job = replace(ctx.job, max_megabytes=2)
        ctx.job = new_job
        self.assertIs(ctx.refs.job, new_job)
        self.assertEqual(ctx.directory, config.state_dir / "test")

    def test_view_isolates_runner_progress_and_storage_and_shares_thread_safe_parts(self):
        ctx, _ = self.context()
        runner, progress = Mock(name="runner"), Mock(name="progress")
        view = ctx.worker_view(runner, progress)
        self.assertIsInstance(view, WorkerView)
        self.assertIs(view.runner, runner)
        self.assertIs(view.progress, progress)
        self.assertIsNot(ctx.runner, runner)
        self.assertIsNot(view.storage, ctx.storage)
        self.assertIs(view.storage.runner, runner)
        self.assertIs(view.journal_lock, ctx.journal_lock)
        self.assertIs(view.control_memo, ctx.control_memo)
        self.assertIs(view.journal, ctx.journal)
        self.assertIs(view.metrics, ctx.metrics)
        self.assertIs(view.job, ctx.job)
        self.assertIs(view.source, ctx.source)

    def test_mutating_a_view_does_not_leak_into_the_parent_or_siblings(self):
        ctx, _ = self.context()
        first, second = ctx.worker_view(Mock(), Mock()), ctx.worker_view(Mock(), Mock())
        before = (ctx.runner, ctx.progress, ctx.storage, ctx.job, ctx.max_delete)
        first.runner, first.progress, first.storage, first.job, first.max_delete = "r", "p", "s", "j", 9
        self.assertEqual((ctx.runner, ctx.progress, ctx.storage, ctx.job, ctx.max_delete), before)
        self.assertNotEqual(second.runner, "r")
        self.assertIs(second.job, ctx.job)
        # Shared containers are the same objects on purpose: workers record into them under the lock.
        with first.journal_lock:
            first.control_memo["k"] = 1
        self.assertEqual(ctx.control_memo["k"], 1)

    def test_view_methods_act_on_the_parent(self):
        ctx, _ = self.context()
        view = ctx.worker_view(Mock(), None)
        view.event("transferred", bytes=7)
        self.assertEqual(ctx.metrics["bytes_transferred"], 7)

    def test_simple_context_view_replaces_runner_and_progress_only(self):
        simple = SimpleContext(Mock(), Mock(), Mock(), Mock(), 1, Mock())
        view = simple.worker_view("runner", "progress")
        self.assertEqual((view.runner, view.progress), ("runner", "progress"))
        self.assertEqual((simple.runner, simple.progress) == ("runner", "progress"), False)
        self.assertIs(view.config, simple.config)

    def test_upload_batch_hands_compress_a_worker_view_not_a_copy_of_the_context(self):
        ctx, _ = self.context()
        item = plan(ctx.files, 1)[0]
        seen = []

        def stop(worker, *args):
            seen.append(worker)
            raise BackupError("stop")

        with patch.object(strategy, "compress", stop), patch.object(strategy, "check_disks"), \
             patch.object(strategy, "show_progress"), self.assertRaises(BackupError):
            strategy.upload_batch(ctx, item)
        worker, = seen
        self.assertIsInstance(worker, WorkerView)
        self.assertIs(worker.parent, ctx)
        self.assertIsNot(worker.runner, ctx.runner)
        self.assertIsNot(worker.storage, ctx.storage)
        self.assertIs(worker.journal_lock, ctx.journal_lock)
        worker.runner = None
        self.assertIsNotNone(ctx.runner)


if __name__ == "__main__":
    unittest.main()
