"""Recovery action orchestration with stable restore/verify entry points."""
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
from .verification import integrity_job, verify_job as verify_job, verify_metadata_job


def recover(config, action, *, output=None, selectors=(), runner=None, notifier=None):
    runner = runner or Runner(config.timeout, env=config.environment)
    redactor = Runner(env=config.environment, secrets=(config.cronitor_url,)).safe_error
    progress = Progress(redact=redactor)
    if isinstance(runner, Runner):
        runner.progress = progress
    log = RunLog(config.log_dir, redact=redactor, progress=progress,
                 file_level=config.log_file_level, terminal_level=config.log_terminal_level)
    failed = False
    log.event("start", action=action, dry_run=config.dry_run)
    try:
        runner.load_config_secrets(config.rclone_config)
        runner.require([config.rclone])
        if action == "restore" and (len(config.jobs) != 1 or output is None):
            raise BackupError("Restauração exige --job com uma tarefa e --output")
        with lock(config.state_dir) if action != "restore" and not config.dry_run else nullcontext():
            if not config.dry_run:
                removed_logs = log.prune(config.log_retention_days)
                if removed_logs:
                    log.event("log_cleanup", removed_logs=removed_logs, retention_days=config.log_retention_days)
            for job in config.jobs:
                try:
                    runner.require([job.compressor] if job.logic == "batch"
                                   and action in {"restore", "integrity-test"} else [])
                    if action != "verify" or job.logic == "simple":
                        check_disks(job, lambda event, **values: log.event(event, job=job.name, **values),
                                    temporary_path=output if action == "restore" else None,
                                    check_temporary=action == "restore" or (action == "integrity-test" and job.type == "s3"))
                    if action == "restore":
                        restore_job(config, job, runner, log, output, selectors)
                    elif config.dry_run:
                        log.event("would_verify", job=job.name, action=action)
                    else:
                        activities = {"verify": verify_job, "verify-metadata": verify_metadata_job,
                                      "integrity-test": integrity_job}
                        activities[action](config, job, runner, log, progress=progress)
                except (BackupError, OSError, ValueError, KeyError, TypeError, tarfile.TarError) as exc:
                    failed = True
                    message = str(JobError(job.name, exc))
                    log.event("error", job=job.name, error=message)
                    if notifier and not config.dry_run:
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
