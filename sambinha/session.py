"""Shared runner, redaction, progress and audit log setup for backup and recovery runs."""
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable

from .disks import use_real_temporary_directory
from .logging import RunLog
from .process import Runner
from .progress import Progress


@dataclass(frozen=True)
class RunSession:
    runner: Any
    redactor: Callable[[str], str]
    progress: Progress
    log: RunLog
    all_dry_run: bool
    log_retention_days: int

    def prune_logs(self):
        """Purge expired audit logs; dry runs never delete anything."""
        if self.all_dry_run:
            return
        removed_logs = self.log.prune(self.log_retention_days)
        if removed_logs:
            self.log.event("log_cleanup", removed_logs=removed_logs, retention_days=self.log_retention_days)


@contextmanager
def open_session(config, *, runner=None):
    """Build the per-run services and release progress/log after the caller's final events."""
    use_real_temporary_directory(config)
    runner = runner or Runner(config.timeout, env=config.environment)
    redactor = Runner(env=config.environment, secrets=(config.cronitor_url,)).safe_error
    progress = Progress(redact=redactor)
    if isinstance(runner, Runner):
        runner.progress = progress
    job_levels = {job.name: (job.log_file_level, job.log_terminal_level) for job in config.jobs}
    first_job = config.jobs[0] if config.jobs else None
    log = RunLog(config.log_dir, redact=redactor, progress=progress,
                 file_level=first_job.log_file_level if first_job else "summary",
                 terminal_level=first_job.log_terminal_level if first_job else "summary",
                 job_levels=job_levels)
    all_dry_run = bool(config.jobs) and all(job.dry_run for job in config.jobs)
    log_retention_days = max((job.log_retention_days for job in config.jobs), default=14)
    try:
        yield RunSession(runner, redactor, progress, log, all_dry_run, log_retention_days)
    finally:
        # Callers log their end event and call progress.finish() on success first.
        progress.close()
        log.close()
