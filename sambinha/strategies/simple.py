"""Native rclone synchronization and run-scoped history retention."""
from datetime import UTC, datetime

from ..context import StrategyContext
from ..errors import BackupError
from ..names import HISTORY
from ..process import show_progress


def simple(ctx: StrategyContext) -> None:
    job, runner, log = ctx.job, ctx.runner, ctx.log
    history_id = datetime.fromtimestamp(ctx.now, UTC).strftime("%Y%m%dT%H%M%S") + "-" + log.id
    backup_dir = f"{HISTORY}/{history_id}"
    max_delete = -1 if ctx.allow_mass_deletion else job.max_delete
    log.event("sync_start", job=job.name, max_delete=max_delete, backup_dir=backup_dir)
    show_progress(runner, ctx.progress.scope(0.2, 0.95))
    storage = ctx.storage_factory(job, ctx.config, runner, log=log)
    newer = storage.sync_simple(job.source, backup_dir, max_delete=max_delete, dry_run=job.dry_run)
    retention_days = max(job.deleted_days, job.version_days)
    expired = storage.expire_simple_history(ctx.now, retention_days, dry_run=job.dry_run)
    for history in expired:
        log.event("would_expire" if job.dry_run else "expire_history",
                  job=job.name, path=history, retention_days=retention_days)
    if newer:
        raise BackupError("Arquivos mais novos preservados no destino; job marcado como falho "
                          "para revisão manual: " + ", ".join(newer))
