"""Backup orchestration and independent, sequential job lifecycles."""
from contextlib import nullcontext
from pathlib import Path
import time

from .archives import compress as compress
from .batch_alerts import collect as collect_batch_alerts
from .config import Config
from .disks import use_real_temporary_directory
from .context import Context as Context
from .errors import BackupError, JobError
from .jobs import JobExecution
from .logging import RunLog
from .notify import notify
from .preflight import preflight as preflight
from .process import Runner, handle_termination
from .process import show_progress as show_progress
from .progress import Progress
from .retention import DAY as DAY
from .retention import DeletionForecast
from .state import lock
from .strategies.batch import batch
from .strategies.batch import upload_batch as upload_batch, upload_index as upload_index
from .versions import check_rclone_version

STRATEGIES = {"batch": batch}


# SIGTERM unwinds through every finally below (end event, lock, temporary
# directories, children), exactly like Ctrl+C; see process.handle_termination.
@handle_termination()
def run(config: Config, *, runner=None, notifier=notify, clock=time.time, allow_mass_deletion=False,
        action='backup') -> int:
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
    failed = False
    errors = []

    def report_job_error(error):
        nonlocal failed
        failed = True
        errors.append(str(error))
        log.event("error", job=error.job, error=str(error))

    def telemetry(state, message=""):
        nonlocal failed
        if all_dry_run:
            return
        try:
            if message:
                notifier(config.cronitor_url, state, log.id, redactor(message)[:2000])
            else:
                notifier(config.cronitor_url, state, log.id)
        except BackupError as exc:
            failed = True
            log.event("notification_error", error=str(exc))

    deletion_alerts = []
    batch_alerts = []
    log.event("start", dry_run=all_dry_run, sources=[str(j.source) for j in config.jobs])
    try:
        runner.load_config_secrets(config.rclone_config)
        with nullcontext() if all_dry_run else lock(config.state_dir):
            if not all_dry_run:
                removed_logs = log.prune(log_retention_days)
                if removed_logs:
                    log.event("log_cleanup", removed_logs=removed_logs, retention_days=log_retention_days)
            telemetry("run")
            runner.require([config.rclone])
            check_rclone_version(runner, config.rclone)
            if config.rclone_config and not Path(config.rclone_config).is_file():
                raise BackupError("Arquivo de configuração rclone não existe")
            # Each job owns its full lifecycle. Later jobs must see the files,
            # rules and state left by earlier jobs, just as separate invocations do.
            for index, job in enumerate(config.jobs):
                job_progress = progress.scope(0.95 * index / len(config.jobs),
                                              0.95 * (index + 1) / len(config.jobs))
                success = False
                forecast = DeletionForecast(job.deletion_alert_threshold, log,
                                            lambda message, dry_run=job.dry_run: (None if dry_run else telemetry("fail", message)))
                execution = JobExecution(config, job, runner, log, job_progress, forecast, clock,
                                         allow_mass_deletion, STRATEGIES, rebalancing=action == 'rebalance')
                log.event("directory", job=job.name, source=str(job.source), destination=job.destination)
                try:
                    execution.execute()
                    success = True
                except (BackupError, OSError, ValueError, KeyError, TypeError) as exc:
                    report_job_error(exc if isinstance(exc, JobError) else JobError(execution.job.name, exc))
                finally:
                    if forecast.sent:
                        deletion_alerts.append(forecast.message)
                    if execution.context is not None:
                        batch_alerts.extend(collect_batch_alerts(execution.context))
                    execution.summary(success)
                    if success:
                        job_progress.update("Concluindo tarefa", progress.directory, 1, 1)
                if not success and not job.continue_on_job_error:
                    break
            if failed:
                telemetry("fail", "; ".join(errors + deletion_alerts + batch_alerts))
            elif batch_alerts:
                telemetry("fail", "; ".join(batch_alerts + deletion_alerts))
            elif not deletion_alerts:
                telemetry("complete")
    except KeyboardInterrupt:
        failed = True
        raise
    except (BackupError, OSError, ValueError) as exc:
        failed = True
        details = {"job": exc.job} if isinstance(exc, JobError) else {}
        log.event("error", error=str(exc), **details)
        telemetry("fail", "; ".join([*errors, str(exc)]))
    except BaseException:
        failed = True
        raise
    finally:
        log.event("end", success=not failed, deletion_alert=bool(deletion_alerts), batch_size_alert=bool(batch_alerts))
        if not failed:
            progress.finish()
        progress.close()
        log.close()
    return 1 if failed else 0
