"""Bounded subprocess execution; no shell interpolation or hidden rclone retries."""
import shutil
import subprocess
import time

from .errors import BackupError


class Runner:
    def __init__(self, timeout=3600, sleep=time.sleep):
        self.timeout = timeout
        self.sleep = sleep
        self.last_stderr = None

    def require(self, programs):
        missing = sorted({p for p in programs if not shutil.which(p)})
        if missing:
            raise BackupError("Dependências ausentes: " + ", ".join(missing))

    def run(self, args, *, cwd=None, retry=False, input=None):
        # One original attempt plus at most three retries, two seconds apart.
        for attempt in range(4 if retry else 1):
            try:
                result = subprocess.run(args, cwd=cwd, input=input, capture_output=True,
                                        text=True, timeout=self.timeout, check=False)
                if result.returncode == 0:
                    self.last_stderr = result.stderr
                    return result.stdout
                error = BackupError(f"{args[0]} falhou ({result.returncode}): {result.stderr[-2000:]}")
            except (OSError, subprocess.TimeoutExpired) as exc:
                error = BackupError(f"{args[0]} não pôde concluir a operação: {type(exc).__name__}")
            if attempt == (3 if retry else 0):
                raise error
            self.sleep(2)
