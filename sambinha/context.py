"""Execution context shared by backup strategies."""
import threading
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol

from .errors import BackupError
from .filtering import Selection, retired_paths
from .metadata import control_directory, prepare_controls, write_manifest
from .names import STATE_FILE
from .retention import alert_time, date
from .state import atomic_json, identity, identity_options, indexed_files, load_state, migrate_identity, record_origin
from .storage import Storage, StorageBackend, StorageFactory


class StrategyContext(Protocol):
    """What every registered strategy may rely on (see ``engine.STRATEGIES``)."""

    config: Any
    job: Any
    runner: Any
    log: Any
    now: float
    progress: Any
    allow_mass_deletion: bool
    storage_factory: StorageFactory

    def worker_view(self, runner, progress) -> Any:
        """A view for one worker thread: its own ``runner``/``progress``, everything else shared."""
        ...


@dataclass
class SimpleContext:
    """Minimal context of ``logic = "simple"`` jobs: no index state, controls or batch metrics."""

    config: Any
    job: Any
    runner: Any
    log: Any
    now: float
    progress: Any
    allow_mass_deletion: bool = False
    storage_factory: StorageFactory = Storage

    def worker_view(self, runner, progress) -> "SimpleContext":
        """Same contract as ``Context.worker_view``; this context has no shared mutable parts."""
        return replace(self, runner=runner, progress=progress)


@dataclass(frozen=True, slots=True)
class ContextRefs:
    """What a job context only refers to: fixed for the whole execution, safe to share across threads."""

    config: Any
    job: Any
    storage_factory: StorageFactory
    directory: Path
    controls_directory: Path
    state_path: Path
    identity: Any


class WorkerView:
    """One worker's window on a ``Context``: own runner, progress and storage; the rest is the parent's.

    Reads of anything else (journal and its lock, control memo, metrics, state, ``event``...) resolve to
    the parent, so the thread-safe parts are shared by construction. Writes stay on the view and never
    leak into the parent, which replaces the former ``copy.copy(ctx)``.
    """

    def __init__(self, parent: "Context", runner, progress, storage):
        self.parent, self.runner, self.progress, self.storage = parent, runner, progress, storage

    def __getattr__(self, name):
        # Only reached for names not set on the view itself. Guard ``parent`` so a half-built view cannot recurse.
        if name == "parent":
            raise AttributeError(name)
        return getattr(self.parent, name)

    def worker_view(self, runner, progress) -> "WorkerView":
        return self.parent.worker_view(runner, progress)


class Context:
    """Per-job state, metrics, deletion guards and retention for batch strategies."""

    def __init__(self, config, job, runner, log, files, now, selected_dirs=None, forecast=None, progress=None,
                 state=None, rebalancing=False, storage_factory: StorageFactory = Storage):
        self.runner, self.log = runner, log
        self.files, self.now = files, now
        self.selection = Selection(job.exclude_files, config, runner,
                                   ignored_roots=(job.source,))
        self.selected_dirs = selected_dirs
        self.forecast = forecast
        self.progress = progress
        self.rebalancing = rebalancing
        self.source = job.source
        # ``storage_factory`` is the overridable seam: tests and alternate backends inject a StorageBackend.
        directory = config.state_dir / job.name
        self.refs = ContextRefs(config, job, storage_factory, directory, control_directory(config, job),
                                directory / STATE_FILE, identity(job))
        self.storage: StorageBackend = storage_factory(job, config, runner, source_required=not rebalancing)
        self.state = (load_state(self.state_path, self.identity, **identity_options(job))
                      if state is None else state)
        migrate_identity(self.state, job, log)
        # Controls already read and validated in this execution (see metadata.cached_control).
        self.control_memo = {}
        if not job.dry_run:
            prepare_controls(config, job, self.state, memo=self.control_memo)
        self.metrics = {"reused_batches": 0, "rebuilt_batches": 0,
                        "bytes_planned": 0, "bytes_transferred": 0}
        self.reused_batches = set()
        self.file_sizes = {f.path: f.size for f in files}
        self.before_files = indexed_files(self.state, job.logic)
        self.removed = []
        self.inventory_changed = False
        # Scan-time stat signatures for verify (hash mode, fingerprint cache, no full scan due).
        self.signatures: Any = None
        self.max_delete = -1
        self.batch_progress = {}
        # Upload journal (see journal.py): entries still believed valid, shared by worker threads.
        self.journal = {}
        # Destination listing shared by the upload planner and the orphan report, and the run's mass-deletion override.
        self.listing = None
        self.allow_mass_deletion = False
        self.journal_lock = threading.Lock()
        # Local control mirrors of expired history, removed only after the
        # state without those entries is durable (see expire/save).
        self.expired_mirrors = []

    # Immutable references live in ``refs``; ``config``/``job``/``storage_factory`` stay assignable (tests
    # swap them) by replacing the whole frozen record, so a view never sees a half-updated set.
    @property
    def config(self):
        return self.refs.config

    @config.setter
    def config(self, value):
        self.refs = replace(self.refs, config=value)

    @property
    def job(self):
        return self.refs.job

    @job.setter
    def job(self, value):
        self.refs = replace(self.refs, job=value)

    @property
    def storage_factory(self) -> StorageFactory:
        return self.refs.storage_factory

    @storage_factory.setter
    def storage_factory(self, value: StorageFactory):
        self.refs = replace(self.refs, storage_factory=value)

    @property
    def directory(self) -> Path:
        return self.refs.directory

    @property
    def controls_directory(self) -> Path:
        return self.refs.controls_directory

    @property
    def state_path(self) -> Path:
        return self.refs.state_path

    @property
    def identity(self):
        return self.refs.identity

    def worker_view(self, runner, progress) -> WorkerView:
        """View for one upload worker: private runner, progress and storage; journal lock, control memo,
        journal, metrics and state are the parent's own objects (see ``WorkerView``)."""
        storage = self.storage_factory(self.job, self.config, runner, source_required=not self.rebalancing)
        return WorkerView(self, runner, progress, storage)

    def event(self, event, **values):
        if event in {"batch_start", "would_batch"}:
            self.metrics["rebuilt_batches"] += 1
            self.metrics["bytes_planned"] += values["bytes"]
        elif event in {"transfer_start", "would_transfer"} and values.get("path") in self.file_sizes:
            self.metrics["bytes_planned"] += self.file_sizes[values["path"]]
        elif event == "identical" and "archive" in values:
            self.reused_batches.add(values["archive"])
            self.metrics["reused_batches"] = len(self.reused_batches)
        elif event == "transferred":
            self.metrics["bytes_transferred"] += values.get("bytes", 0)
        self.log.event(event, job=self.job.name, **values)

    def selected_paths(self):
        """Inventory paths already selected by the frozen rules during discovery (none when rebalancing,
        whose "inventory" is rebuilt from controls and was never filtered)."""
        return () if self.rebalancing else self.file_sizes.keys()

    def protect_deletions(self, allowed=False):
        included = self.selection.include_paths(self.before_files, self.selected_paths())
        self.removed = sorted(included - self.selected_paths())
        if not allowed and len(included) >= self.job.deletion_min_files:
            self.max_delete = int(len(included) * self.job.max_delete_percent / 100)
        if not allowed and self.max_delete >= 0 and len(self.removed) > self.max_delete:
            self.event("mass_deletion_blocked", removed_files=len(self.removed),
                       previous_files=len(included), threshold_percent=self.job.max_delete_percent,
                       paths=self.removed)
            raise BackupError(f"Remoções em massa bloqueadas: {len(self.removed)} de {len(included)} arquivos; "
                              "confira a origem e use --allow-mass-deletion apenas para remoções intencionais")

    def summary(self, success):
        current = {f.path: f.metadata() for f in self.files}
        self.event("summary", success=success, dry_run=self.job.dry_run,
                   total_files=len(current), new_files=len(current.keys() - self.before_files.keys()),
                   modified_files=sum(p in self.before_files and f != self.before_files[p]
                                      for p, f in current.items()), removed_files=len(self.removed),
                   unchanged_files=sum(f == self.before_files.get(p) for p, f in current.items()),
                   pending_deletions=sum(len(r.get("files", [])) for r in self.state["retired"]), **self.metrics)

    def save(self):
        record_origin(self.state, self.job, self.now)
        atomic_json(self.state_path, self.state)
        write_manifest(self.config, self.job, self.state)
        # Only now may the mirrors go: a crash before this point leaves a state
        # that still references them, and publication reads every referenced mirror.
        while self.expired_mirrors:
            mirror = self.expired_mirrors.pop()
            self.control_memo.pop(mirror.relative_to(self.controls_directory).as_posix().removesuffix(".json"), None)
            mirror.unlink(missing_ok=True)

    def forecast_deletions(self):
        if self.forecast is None:
            return
        paths = {entry["key"]: retired_paths(entry, self.directory, controls_directory=self.controls_directory)
                 for entry in self.state["retired"]}
        included = self.selection.include_paths((p for members in paths.values() for p in members), self.selected_paths())
        candidates = [(entry, paths[entry["key"]]) for entry in self.state["retired"]
                      if alert_time(entry, self.job.deleted_days, self.job.version_days) <= self.now
                      and all(p in included for p in paths[entry["key"]])]
        self.forecast.add(self.job.name, candidates)

    def scheduled(self, entry):
        self.event("deletion_scheduled", path=entry["key"], files=entry["files"],
                   reason=entry["reason"], deletion_at=date(entry["expires"]),
                   alert_from=date(entry["alert_at"]))

    def expire(self):
        self.forecast_deletions()
        paths = {entry["key"]: retired_paths(entry, self.directory, controls_directory=self.controls_directory)
                 for entry in self.state["retired"]}
        included = self.selection.include_paths((p for members in paths.values() for p in members), self.selected_paths())
        remaining = []
        for entry in self.state["retired"]:
            if any(p not in included for p in paths[entry["key"]]):
                self.event("excluded_history", path=entry["key"], files=paths[entry["key"]])
                remaining.append(entry)
                continue
            if entry["expires"] > self.now:
                remaining.append(entry)
                continue
            self.event("would_expire" if self.job.dry_run else "expire", path=entry["key"],
                       files=paths[entry["key"]], deletion_at=date(entry["expires"]))
            if not self.job.dry_run:
                # Destination deletes are idempotent; the local mirror waits for save().
                self.storage.delete(entry["key"])
                if entry.get("control"):
                    self.storage.delete(entry["control"])
                    self.expired_mirrors.append(self.controls_directory / entry["control"])
                if self.listing is not None:
                    for expired in (entry["key"], entry.get("control")):
                        self.listing.pop(expired, None)
        self.state["retired"] = remaining
