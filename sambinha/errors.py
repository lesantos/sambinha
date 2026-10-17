"""Errors exposed to the command-line user without a traceback."""
from contextlib import contextmanager


class BackupError(Exception):
    """An operation could not be completed safely."""


class SourceChangedError(BackupError):
    """A source file or directory changed while being inspected or copied."""


class ConfigError(BackupError):
    """Configuration violates a required constraint."""


class JobError(BackupError):
    """A failure attributed to the job that was being processed."""

    def __init__(self, job, error):
        self.job = job
        super().__init__(f"Job {job}: {error}")


@contextmanager
def job_errors(job, on_error=None):
    """Preserve job context when errors escape the global preflight."""
    try:
        yield
    except (BackupError, OSError, ValueError, KeyError, TypeError) as exc:
        error = exc if isinstance(exc, JobError) else JobError(job, exc)
        if on_error is None:
            if error is exc:
                raise
            raise error from exc
        on_error(error)
