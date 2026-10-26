"""Backup orchestration and independent, sequential job lifecycles."""
import time
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
from typing import Any

from .batch_alerts import collect as collect_batch_alerts
from .config import Config
from .errors import BackupError, JobError
from .fresh import requested as fresh_requested
from .jobs import JobExecution
from .names import Logic
from .notify import notify
from .process import handle_termination
from .retention import DeletionForecast
from .session import open_session
from .state import lock
from .strategies.batch import batch
from .strategies.simple import simple
from .tempdirs import MIN_AGE, sweep_and_log
from .versions import check_rclone_version

STRATEGIES: dict[Logic, Callable[[Any], None]] = {"simple": simple, "batch": batch}
"""Strategy registry keyed by ``Job.logic``; ``JobExecution`` dispatches through it.

Registering a new strategy or backend (extension checklist):
1. Add the name to ``names.Logic`` (and ``Job.type`` values if it is a new destination kind) and
   extend ``config.validate`` so only the new combinations are accepted.
2. Write ``strategies/<name>.py`` exposing ``def strategy(ctx)``. It receives ``Context`` (full job
   state, as ``batch`` does) or ``SimpleContext`` (stateless, as ``simple`` does); both expose
   ``config/job/runner/log/now/progress/allow_mass_deletion/storage_factory`` (``StrategyContext``).
3. Register it here: ``STRATEGIES["name"] = strategy``. An unregistered logic fails with a
   ``BackupError`` before any I/O.
4. Obtain storage only through ``ctx.storage_factory``/``ctx.storage``, never ``Storage(...)``.
   A new destination implements ``storage.StorageBackend`` and is injected as the factory.
5. Cover it with a conformance test (see tests/unit/test_storage_contract.py) and update the docs.
"""


# SIGTERM unwinds through every finally below (end event, lock, temporary
# directories, children), exactly like Ctrl+C; see process.handle_termination.
@handle_termination()
def run(config: Config, *, runner=None, notifier=notify, clock=time.time, allow_mass_deletion=False,
        action='backup', adopt_identity=False, prune_orphans=False, fresh_start=False) -> int:
    if fresh_start:
        config = replace(config, jobs=tuple(replace(job, fresh_start=True) for job in config.jobs))
    if prune_orphans:
        config = replace(config, jobs=tuple(replace(job, prune_orphans=True) for job in config.jobs))
    if adopt_identity:
        # Identity adoption is per invocation; the jobs carry it to state/index checks.
        config = replace(config, jobs=tuple(replace(job, adopt_identity=True) for job in config.jobs))
    with open_session(config, runner=runner) as session:
        runner, redactor, progress, log = session.runner, session.redactor, session.progress, session.log
        all_dry_run = session.all_dry_run
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
                session.prune_logs()
                if not all_dry_run:
                    # A clean restart also clears recent leftovers of dead processes.
                    sweep_and_log(config, log, min_age=0 if any(map(fresh_requested, config.jobs)) else MIN_AGE)
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
    return 1 if failed else 0
