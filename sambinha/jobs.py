"""One job's frozen rules, strategy execution, verification and summary."""
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from .config import Config, Job
from .context import Context, SimpleContext
from .disks import check_disks
from .errors import BackupError
from .filtering import frozen_rules
from .logging import RunLog
from .names import Logic
from .preflight import JobInventory, preflight
from .process import Runner, show_progress
from .progress import Progress
from .retention import DAY, DeletionForecast
from .state import drop_fingerprints, save_fingerprints
from .storage import Storage, StorageFactory


@dataclass
class JobExecution:
    config: Config
    job: Job
    runner: Runner
    log: RunLog
    progress: Progress
    forecast: DeletionForecast
    clock: Callable[[], float]
    allow_mass_deletion: bool
    strategies: Mapping[Logic, Callable[[Any], None]]
    rebalancing: bool = False
    storage_factory: StorageFactory = Storage
    context: Context | None = field(default=None, init=False)

    def execute(self):
        """Finish this job before any later job inspects its source or rules."""
        with frozen_rules(replace(self.config, jobs=(self.job,))) as effective:
            self.job = effective.jobs[0]
            if self.rebalancing:
                from .rebalance import execute
                self.context = execute(effective, self.job, self.runner, self.log, self.progress,
                                       self.forecast, self.clock(),
                                       on_context=lambda ctx: setattr(self, "context", ctx))
                return
            strategy = self._strategy()
            inventories = preflight(effective, self.runner, self.clock(), progress=self.progress.scope(0, 0.2),
                                    log=self.log)
            if self.job.logic == "simple":
                strategy(SimpleContext(effective, self.job, self.runner, self.log, self.clock(),
                                       self.progress, self.allow_mass_deletion, self.storage_factory))
            else:
                self._execute_batch(effective, inventories[self.job.name], strategy)

    def _strategy(self) -> Callable[[Any], None]:
        """Registry lookup keyed by ``Job.logic``; fails before any I/O for an unregistered logic."""
        try:
            return self.strategies[self.job.logic]
        except KeyError:
            raise BackupError(f"Lógica de backup sem estratégia registrada: {self.job.logic}") from None

    def _execute_batch(self, effective: Config, inventory: JobInventory, strategy: Callable[[Any], None]):
        ctx = Context(effective, self.job, self.runner, self.log, inventory.files, self.clock(),
                      selected_dirs=inventory.selected_dirs, forecast=self.forecast, state=inventory.previous,
                      progress=self.progress.scope(0.2, 0.9), storage_factory=self.storage_factory)
        self.context = ctx
        ctx.allow_mass_deletion = self.allow_mass_deletion
        cached_inventory = inventory.fingerprints
        # "Changed" = something state.json must persist: a new last_full_scan, or a legacy embedded
        # cache to migrate out (which also republishes the index without it). Fingerprint churn
        # alone never forces a publication; fingerprints.json is rewritten on its own below.
        ctx.inventory_changed = (inventory.legacy_cache or
                                 ctx.state.get("last_full_scan") != inventory.last_full_scan)
        if not self.job.dry_run:
            if self.job.fingerprint_cache and self.job.verification_mode == "hash":
                if inventory.legacy_cache or cached_inventory != (inventory.cached or {}):
                    save_fingerprints(ctx.directory, cached_inventory)
            else:
                drop_fingerprints(ctx.directory)
        # Scan-time signatures let verify skip re-reading unchanged files; never on a full-scan run.
        if self.job.fingerprint_cache and not inventory.full_scan:
            ctx.signatures = cached_inventory
        ctx.state["last_full_scan"] = inventory.last_full_scan
        ctx.forecast_deletions()
        check_disks(ctx.job, ctx.event, config=ctx.config)
        ctx.protect_deletions(self.allow_mass_deletion)
        strategy(ctx)
        check_disks(ctx.job, ctx.event, config=ctx.config)
        self._verify_if_due(effective)

    def _verify_if_due(self, effective):
        ctx = self.context
        interval = self.job.verify_interval_days * DAY
        due = interval and ("last_verified" not in ctx.state or
                            ctx.now - ctx.state["last_verified"] >= interval)
        if not due:
            return
        if self.job.dry_run:
            ctx.event("would_verify", mode="source_metadata")
            return
        from .recovery import verify_local_metadata_job

        checking = self.progress.scope(0.9, 1)
        show_progress(self.runner, checking)
        verify_local_metadata_job(effective, ctx.job, self.runner, self.log, ctx.state, progress=checking)
        ctx.state = {**ctx.state, "last_verified": ctx.now}
        ctx.save()

    def summary(self, success):
        if self.context is not None:
            self.context.summary(success)
        elif self.job.logic == "simple":
            self.log.event("summary", job=self.job.name, success=success, dry_run=self.job.dry_run,
                           strategy="rclone_sync",
                           max_delete=-1 if self.allow_mass_deletion else self.job.max_delete)
