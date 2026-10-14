"""Backup orchestration and independent, sequential job lifecycles."""
from contextlib import nullcontext
from pathlib import Path
import time

from .archives import compress as compress
from .config import Config
from .context import Context as Context
from .errors import BackupError, JobError
from .jobs import JobExecution
from .logging import RunLog
from .notify import notify
from .preflight import preflight as preflight
from .process import Runner
from .process import show_progress as show_progress
from .progress import Progress
from .retention import DAY as DAY
from .retention import DeletionForecast
from .state import lock
from .strategies.batch import batch
from .strategies.batch import upload_batch as upload_batch, upload_index as upload_index

STRATEGIES = {"batch": batch}


def run(config: Config, *, runner=None, notifier=notify, clock=time.time, allow_mass_deletion=False) -> int:
    runner = runner or Runner(config.timeout, env=config.environment)
    redactor = Runner(env=config.environment, secrets=(config.cronitor_url,)).safe_error
    progress = Progress(redact=redactor)
    if isinstance(runner, Runner):
        runner.progress = progress
    log = RunLog(config.log_dir, redact=redactor, progress=progress,
                 file_level=config.log_file_level, terminal_level=config.log_terminal_level)
    failed = False
    errors = []

    def report_job_error(error):
        nonlocal failed
        failed = True
        errors.append(str(error))
        log.event("error", job=error.job, error=str(error))

    def telemetry(state, message=""):
        nonlocal failed
        if config.dry_run:
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
    log.event("start", dry_run=config.dry_run, sources=[str(j.source) for j in config.jobs])
    try:
        runner.load_config_secrets(config.rclone_config)
        with nullcontext() if config.dry_run else lock(config.state_dir):
            if not config.dry_run:
                removed_logs = log.prune(config.log_retention_days)
                if removed_logs:
                    log.event("log_cleanup", removed_logs=removed_logs, retention_days=config.log_retention_days)
            telemetry("run")
            runner.require([config.rclone])
            runner.run([config.rclone, "version"])
            if config.rclone_config and not Path(config.rclone_config).is_file():
                raise BackupError("Arquivo de configuração rclone não existe")
            # Each job owns its full lifecycle. Later jobs must see the files,
            # rules and state left by earlier jobs, just as separate invocations do.
            for index, job in enumerate(config.jobs):
                job_progress = progress.scope(0.95 * index / len(config.jobs),
                                              0.95 * (index + 1) / len(config.jobs))
                success = False
                forecast = DeletionForecast(config.deletion_alert_threshold, log,
                                            lambda message: telemetry("fail", message))
                execution = JobExecution(config, job, runner, log, job_progress, forecast, clock,
                                         allow_mass_deletion, STRATEGIES)
                log.event("directory", job=job.name, source=str(job.source), destination=job.destination)
                try:
                    execution.execute()
                    success = True
                except (BackupError, OSError, ValueError, KeyError, TypeError) as exc:
                    report_job_error(exc if isinstance(exc, JobError) else JobError(execution.job.name, exc))
                finally:
                    if forecast.sent:
                        deletion_alerts.append(forecast.message)
                    execution.summary(success)
                    if success:
                        job_progress.update("Concluindo tarefa", progress.directory, 1, 1)
                if not success and not job.continue_on_job_error:
                    break
            if failed:
                telemetry("fail", "; ".join(errors + deletion_alerts))
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
        log.event("end", success=not failed, deletion_alert=bool(deletion_alerts))
        if not failed:
            progress.finish()
        progress.close()
        log.close()
    return 1 if failed else 0
