"""Bounded subprocess execution; no shell interpolation or hidden rclone retries."""
import shutil
import os
import re
import subprocess
import time

from .errors import BackupError

_CREDENTIAL = re.compile(r"SECRET|TOKEN|PASSWORD|ACCESS_KEY|(?:^|_)KEY(?:$|_)|CRONITOR_URL", re.I)


class Runner:
    def __init__(self, timeout=3600, sleep=time.sleep, env=None):
        self.timeout = timeout
        self.sleep = sleep
        self.last_stderr = None
        self.env = env

    def require(self, programs):
        search_path = self.env.get("PATH", os.defpath) if self.env is not None else None
        missing = sorted({p for p in programs if not shutil.which(p, path=search_path)})
        if missing:
            raise BackupError("Dependências ausentes: " + ", ".join(missing))

    def safe_error(self, message):
        # Backends can echo credential identifiers in errors. Remove known
        # credential values before errors reach execution logs or the CLI.
        environment = os.environ if self.env is None else self.env
        values = {value for key, value in environment.items() if value and _CREDENTIAL.search(key)}
        for value in sorted(values, key=len, reverse=True):
            message = message.replace(value, "[REDACTED]")
        return message

    def run(self, args, *, cwd=None, retry=False, input=None):
        # One original attempt plus at most three retries, two seconds apart.
        for attempt in range(4 if retry else 1):
            try:
                result = subprocess.run(args, cwd=cwd, input=input, capture_output=True,
                                        text=True, timeout=self.timeout, check=False, env=self.env)
                if result.returncode == 0:
                    self.last_stderr = result.stderr
                    return result.stdout
                error = BackupError(f"{args[0]} falhou ({result.returncode}): {self.safe_error(result.stderr)[-2000:]}")
            except (OSError, subprocess.TimeoutExpired) as exc:
                error = BackupError(f"{args[0]} não pôde concluir a operação: {type(exc).__name__}")
            if attempt == (3 if retry else 0):
                raise error
            self.sleep(2)
