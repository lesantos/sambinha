"""One job's frozen rules, strategy execution, verification and summary."""
import copy
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace

from .config import Config, Job
from .context import Context
from .disks import check_disks
from .filtering import frozen_rules
from .logging import RunLog
from .preflight import JobInventory, preflight
from .process import Runner, show_progress
from .progress import Progress
from .retention import DAY, DeletionForecast
from .strategies.simple import simple


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
    strategies: Mapping[str, Callable[[Context], None]]
    rebalancing: bool = False
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
            inventories = preflight(effective, self.runner, self.clock(), progress=self.progress.scope(0, 0.2),
                                    log=self.log)
            if self.job.logic == "simple":
                simple(effective, self.job, self.runner, self.log, self.clock(), self.progress,
                       allow_mass_deletion=self.allow_mass_deletion)
            else:
                self._execute_batch(effective, inventories[self.job.name])

    def _execute_batch(self, effective: Config, inventory: JobInventory):
        ctx = Context(effective, self.job, self.runner, self.log, inventory.files, self.clock(),
                      selected_dirs=inventory.selected_dirs, forecast=self.forecast, state=inventory.previous,
                      progress=self.progress.scope(0.2, 0.9))
        self.context = ctx
        ctx.allow_mass_deletion = self.allow_mass_deletion
        cached_inventory = inventory.fingerprints if self.job.fingerprint_cache else {}
        ctx.inventory_changed = (ctx.state.get("fingerprints") != cached_inventory or
                                 ctx.state.get("last_full_scan") != inventory.last_full_scan)
        ctx.state["fingerprints"] = cached_inventory
        # Scan-time signatures let verify skip re-reading unchanged files; never on a full-scan run.
        if self.job.fingerprint_cache and not inventory.full_scan:
            ctx.signatures = cached_inventory
        ctx.state["last_full_scan"] = inventory.last_full_scan
        ctx.forecast_deletions()
        check_disks(ctx.job, ctx.event, config=ctx.config)
        ctx.protect_deletions(self.allow_mass_deletion)
        self.strategies[ctx.job.logic](ctx)
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
        verified = copy.deepcopy(ctx.state)
        verified["last_verified"] = ctx.now
        ctx.state = verified
        ctx.save()

    def summary(self, success):
        if self.context is not None:
            self.context.summary(success)
        elif self.job.logic == "simple":
            self.log.event("summary", job=self.job.name, success=success, dry_run=self.job.dry_run,
                           strategy="rclone_sync",
                           max_delete=-1 if self.allow_mass_deletion else self.job.max_delete)
