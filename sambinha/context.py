"""Execution context shared by backup strategies."""
from .errors import BackupError
from .filtering import Selection, retired_paths
from .retention import alert_time, date
from .state import atomic_json, identity, indexed_files, load_state
from .storage import Storage


class Context:
    """Per-job state, metrics, deletion guards and retention for batch strategies."""

    def __init__(self, config, job, runner, log, files, now, selected_dirs=None, forecast=None, progress=None):
        self.config, self.job, self.runner, self.log = config, job, runner, log
        self.files, self.now = files, now
        self.selection = Selection(job.exclude_files, config, runner,
                                   ignored_roots=(job.source,))
        self.selected_dirs = selected_dirs
        self.forecast = forecast
        self.progress = progress
        self.storage = Storage(job, config, runner)
        self.directory = config.state_dir / job.name
        self.state_path = self.directory / "state.json"
        self.identity = identity(job)
        self.state = load_state(self.state_path, self.identity)
        self.metrics = {"reused_batches": 0, "rebuilt_batches": 0,
                        "bytes_planned": 0, "bytes_transferred": 0}
        self.reused_batches = set()
        self.file_sizes = {f.path: f.size for f in files}
        self.before_files = indexed_files(self.state, job.logic)
        self.removed = []
        self.inventory_changed = False
        self.max_delete = -1
        self.batch_progress = {}

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

    def protect_deletions(self, allowed=False):
        included = self.selection.include_paths(self.before_files)
        self.removed = sorted(included - self.file_sizes.keys())
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
        self.event("summary", success=success, dry_run=self.config.dry_run,
                   total_files=len(current), new_files=len(current.keys() - self.before_files.keys()),
                   modified_files=sum(p in self.before_files and f != self.before_files[p]
                                      for p, f in current.items()), removed_files=len(self.removed),
                   unchanged_files=sum(f == self.before_files.get(p) for p, f in current.items()),
                   pending_deletions=sum(len(r.get("files", [])) for r in self.state["retired"]), **self.metrics)

    def save(self):
        atomic_json(self.state_path, self.state)

    def forecast_deletions(self):
        if self.forecast is None:
            return
        paths = {entry["key"]: retired_paths(entry, self.directory) for entry in self.state["retired"]}
        included = self.selection.include_paths(p for members in paths.values() for p in members)
        candidates = [(entry, paths[entry["key"]]) for entry in self.state["retired"]
                      if alert_time(entry, self.config.deleted_days, self.config.version_days) <= self.now
                      and all(p in included for p in paths[entry["key"]])]
        self.forecast.add(self.job.name, candidates)

    def scheduled(self, entry):
        self.event("deletion_scheduled", path=entry["key"], files=entry["files"],
                   reason=entry["reason"], deletion_at=date(entry["expires"]),
                   alert_from=date(entry["alert_at"]))

    def expire(self):
        self.forecast_deletions()
        paths = {entry["key"]: retired_paths(entry, self.directory) for entry in self.state["retired"]}
        included = self.selection.include_paths(p for members in paths.values() for p in members)
        remaining = []
        for entry in self.state["retired"]:
            if any(p not in included for p in paths[entry["key"]]):
                self.event("excluded_history", path=entry["key"], files=paths[entry["key"]])
                remaining.append(entry)
                continue
            if entry["expires"] > self.now:
                remaining.append(entry)
                continue
            self.event("would_expire" if self.config.dry_run else "expire", path=entry["key"],
                       files=paths[entry["key"]], deletion_at=date(entry["expires"]))
            if not self.config.dry_run:
                self.storage.delete(entry["key"])
                if entry.get("control"):
                    self.storage.delete(entry["control"])
                    (self.directory / "controls" / entry["control"]).unlink(missing_ok=True)
        self.state["retired"] = remaining
