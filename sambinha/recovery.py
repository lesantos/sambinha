"""Recovery action orchestration for restoration and metadata/integrity checks."""
from contextlib import nullcontext
import tarfile

from .archive_recovery import check_content as check_content, unpack as unpack
from .disks import check_disks
from .errors import BackupError, JobError
from .logging import RunLog
from .process import Runner
from .progress import Progress
from .recovery_index import read_index as read_index
from .restore import matches as matches, restore_job as restore_job, validate_output as validate_output
from .state import identity as identity, indexed_files as indexed_files, lock
from .verification import (verify_destination_integrity_job, verify_destination_metadata_job,
                           verify_local_metadata_job)


def recover(config, action, *, output=None, selectors=(), runner=None, notifier=None):
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
    failed = False
    log.event("start", action=action, dry_run=all_dry_run)
    try:
        runner.load_config_secrets(config.rclone_config)
        runner.require([config.rclone])
        if action == "restore" and (len(config.jobs) != 1 or output is None):
            raise BackupError("Restauração exige --job com uma tarefa e --output")
        with lock(config.state_dir) if action != "restore" and not all_dry_run else nullcontext():
            if not all_dry_run:
                removed_logs = log.prune(log_retention_days)
                if removed_logs:
                    log.event("log_cleanup", removed_logs=removed_logs, retention_days=log_retention_days)
            for job in config.jobs:
                try:
                    runner.require([job.compressor] if job.logic == "batch"
                                   and action in {"restore", "verify-destination-integrity"} else [])
                    if action != "verify-local-metadata" or job.logic == "simple":
                        check_disks(job, lambda event, **values: log.event(event, job=job.name, **values),
                                    config=config,
                                    temporary_path=output if action == "restore" else None,
                                    check_source=action == "verify-local-metadata",
                                    check_temporary=action == "restore" or
                                    (action == "verify-destination-integrity" and job.type == "s3"))
                    if action == "restore":
                        restore_job(config, job, runner, log, output, selectors)
                    elif job.dry_run:
                        log.event("would_verify", job=job.name, action=action)
                    else:
                        activities = {"verify-local-metadata": verify_local_metadata_job,
                                      "verify-destination-metadata": verify_destination_metadata_job,
                                      "verify-destination-integrity": verify_destination_integrity_job}
                        activities[action](config, job, runner, log, progress=progress)
                except (BackupError, OSError, ValueError, KeyError, TypeError, tarfile.TarError) as exc:
                    failed = True
                    message = str(JobError(job.name, exc))
                    log.event("error", job=job.name, error=message)
                    if notifier and not job.dry_run:
                        notifier(config.cronitor_url, "fail", log.id, redactor(message)[:2000])
                    if not job.continue_on_job_error:
                        break
    except (BackupError, OSError, ValueError) as exc:
        failed = True
        log.event("error", error=str(exc))
    finally:
        log.event("end", action=action, success=not failed)
        if not failed:
            progress.finish()
        progress.close()
        log.close()
    return int(failed)
