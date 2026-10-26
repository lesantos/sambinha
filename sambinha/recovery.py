"""Recovery action orchestration for restoration and metadata/integrity checks."""
from contextlib import nullcontext
import tarfile

from .archive_recovery import check_content as check_content, unpack as unpack
from .disks import check_disks
from .errors import BackupError, JobError
from .process import handle_termination
from .recovery_index import read_index as read_index
from .restore import matches as matches, restore_job as restore_job, validate_output as validate_output
from .session import open_session
from .state import identity as identity, indexed_files as indexed_files, lock
from .verification import (verify_destination_integrity_job, verify_destination_metadata_job,
                           verify_local_metadata_job)
from .versions import check_compressor_version, check_rclone_version


# SIGTERM unwinds like Ctrl+C: the end event, lock and staging cleanup run.
@handle_termination()
def recover(config, action, *, output=None, selectors=(), runner=None, notifier=None):
    with open_session(config, runner=runner) as session:
        runner, redactor, progress, log = session.runner, session.redactor, session.progress, session.log
        all_dry_run = session.all_dry_run
        failed = False
        log.event("start", action=action, dry_run=all_dry_run)
        try:
            runner.load_config_secrets(config.rclone_config)
            runner.require([config.rclone])
            check_rclone_version(runner, config.rclone)
            if action == "restore" and (len(config.jobs) != 1 or output is None):
                raise BackupError("Restauração exige --job com uma tarefa e --output")
            with lock(config.state_dir) if action != "restore" and not all_dry_run else nullcontext():
                session.prune_logs()
                for job in config.jobs:
                    try:
                        needs_compressor = job.logic == "batch" and action in {"restore", "verify-destination-integrity"}
                        runner.require([job.compressor] if needs_compressor else [])
                        if needs_compressor:
                            check_compressor_version(runner, job.compressor)
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
                    # Mirror engine.run: unexpected per-job errors become a logged event
                    # and exit 1 instead of a traceback that hides the job name.
                    except (BackupError, OSError, ValueError, KeyError, TypeError, AttributeError,
                            tarfile.TarError) as exc:
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
        except BaseException:
            # Interruptions (Ctrl+C, SIGTERM) still record an unsuccessful end.
            failed = True
            raise
        finally:
            log.event("end", action=action, success=not failed)
            if not failed:
                progress.finish()
    return int(failed)
