"""Bounded subprocess execution; no shell interpolation or hidden rclone retries."""
import shutil
import os
import re
import subprocess
import time
import json
import threading
import math
from collections import deque
from configparser import ConfigParser, Error as ConfigParserError
from contextlib import nullcontext
from pathlib import Path

from .errors import BackupError

_CREDENTIAL = re.compile(r"SECRET|TOKEN|PASSWORD|(?:^|_)PASS(?:$|_)|ACCESS_KEY|(?:^|_)KEY(?:$|_)|CREDENTIAL|AUTHORIZATION|CRONITOR_URL", re.I)


def credential_values(environment):
    return {value for key, value in environment.items()
            if isinstance(value, str) and value and _CREDENTIAL.search(key)}


def rclone_progress(stats):
    """Read counters independently of rclone's human-readable stats message."""
    def number(key):
        value = stats.get(key)
        if type(value) not in (int, float) or value < 0:
            return None
        if isinstance(value, float) and not math.isfinite(value):
            return None
        return value

    def count(key):
        value = number(key)
        return int(value) if value is not None else 0

    def counter(done, total):
        value, maximum = count(done), count(total)
        return f"{value}/{maximum}" if maximum else str(value)

    transferred, total_bytes = number("bytes"), number("totalBytes")
    transfers, total_transfers = count("transfers"), count("totalTransfers")
    active = stats.get("transferring")
    # Local moves into --backup-dir also increase bytes. They are activity,
    # not completed transfer work; delete-before can still be running.
    transfer_work = transfers > 0 or total_transfers > 0 or (isinstance(active, list) and bool(active))
    percent = None
    if transfer_work and transferred is not None and total_bytes is not None and total_bytes > 0:
        percent = 100 * min(transferred, total_bytes) / total_bytes
    elif total_transfers > 0 and total_bytes == 0:
        # Empty files have no byte denominator but do have a transfer count.
        percent = 100 * min(transfers, total_transfers) / total_transfers
    activity = (f"conferidos: {counter('checks', 'totalChecks')} | "
                f"copiados: {counter('transfers', 'totalTransfers')} | "
                f"remoções: {count('deletes')} | movidos: {count('renames')}")
    return percent, activity


class Runner:
    def __init__(self, timeout=3600, sleep=time.sleep, env=None, secrets=()):
        self.timeout = timeout
        self.sleep = sleep
        self.last_stderr = None
        self.env = env
        self.secrets = tuple(value for value in secrets if value)
        self.progress = None

    def require(self, programs):
        search_path = self.env.get("PATH", os.defpath) if self.env is not None else None
        missing = sorted({p for p in programs if not shutil.which(p, path=search_path)})
        if missing:
            raise BackupError("Dependências ausentes: " + ", ".join(missing))

    def load_config_secrets(self, path):
        if not path:
            return
        config = ConfigParser(interpolation=None, strict=False)
        try:
            with open(path, encoding="utf-8") as stream:
                # Encrypted rclone files contain ciphertext rather than INI.
                # Leave decryption to rclone and redact its environment key.
                if stream.readline().startswith("RCLONE_ENCRYPT_"):
                    return
                stream.seek(0)
                config.read_file(stream)
            values = set(self.secrets)
            for section in config.sections():
                values.update(credential_values(dict(config[section])))
            self.secrets = tuple(values)
        except (OSError, UnicodeError, ConfigParserError):
            raise BackupError("Configuração rclone ausente ou inválida; confira o arquivo privado") from None

    def safe_error(self, message):
        # Backends can echo credential identifiers in errors. Remove known
        # credential values before errors reach execution logs or the CLI.
        environment = os.environ if self.env is None else self.env
        values = credential_values(environment)
        values.update(self.secrets)
        for value in sorted(values, key=len, reverse=True):
            message = message.replace(value, "[REDACTED]")
        message = re.sub(r"(https?://)[^\s/@]+:[^\s/@]+@", r"\1[REDACTED]@", message)
        message = re.sub(r"(https://cronitor\.link/p/)[^\s?]+", r"\1[REDACTED]", message)
        return message

    def run(self, args, *, cwd=None, retry=False, input=None, progress_directory=None, on_output=None, env=None):
        child_env = self.env
        if env is not None:
            child_env = {**(os.environ if self.env is None else self.env), **env}
        # One original attempt plus at most three retries, two seconds apart.
        for attempt in range(4 if retry else 1):
            try:
                stages = {"lsjson": "Lendo destino", "lsf": "Lendo diretórios", "copy": "Copiando arquivos",
                          "sync": "Sincronizando arquivos",
                          "copyto": "Copiando arquivo", "delete": "Aplicando retenção",
                          "a": "Comprimindo lote", "t": "Conferindo lote", "x": "Extraindo lote"}
                stage = stages.get(args[1]) if len(args) > 1 else None
                directory = cwd or (args[2] if len(args) > 2 else ".")
                if stage in {"Copiando arquivo", "Conferindo lote"} or (stage == "Lendo destino" and "--stat" in args):
                    directory = Path(directory).parent
                directory = progress_directory or directory
                measured = bool(self.progress and self.progress.enabled and len(args) > 1
                                and args[1] in {"copy", "sync"})
                command = [*args, "--stats", "1s", "--stats-one-line", "--stats-log-level", "ERROR"] if measured else args
                if measured and "--use-json-log" not in command:
                    command = [*command, "--use-json-log"]
                task = self.progress.task(stage, directory, measured=measured) if self.progress and stage else nullcontext()
                with task as report:
                    if measured or on_output is not None:
                        result = self._run_with_progress(command, cwd, input, report, on_output=on_output, env=child_env)
                    else:
                        result = subprocess.run(command, cwd=cwd, input=input, capture_output=True,
                                                text=True, timeout=self.timeout, check=False, env=child_env)
                if result.returncode == 0:
                    self.last_stderr = result.stderr
                    return result.stdout
                error = BackupError(f"{args[0]} falhou ({result.returncode}): {self.safe_error(result.stderr)[-2000:]}")
            except (OSError, subprocess.TimeoutExpired) as exc:
                error = BackupError(f"{args[0]} não pôde concluir a operação: {type(exc).__name__}")
            if attempt == (3 if retry else 0):
                raise error
            self.sleep(2)

    def _run_with_progress(self, args, cwd, input, report, on_output=None, env=None):
        """Stream structured records and stats, retaining only an error tail when detailed."""
        process = subprocess.Popen(args, cwd=cwd, stdin=subprocess.PIPE if input is not None else None,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, bufsize=1, env=self.env if env is None else env)
        if input is not None:
            process.stdin.write(input)
            process.stdin.close()

        output = deque(maxlen=2000) if on_output is not None else []
        callback_errors = []
        # Compatibility with plain/full and one-line stats without a prefix.
        percentage = re.compile(r"(?:^|\n)\s*(?:Transferred:\s*)?(?=\d)[^,\n]*/[^,\n]*,\s*(\d+(?:\.\d+)?)%")

        def collect():
            for line in process.stdout:
                output.append(line)
                message = line
                try:
                    record = json.loads(line)
                    if isinstance(record, dict):
                        if on_output is not None and "msg" in record and not callback_errors:
                            try:
                                on_output(record)
                            except Exception as exc:
                                # Keep draining the pipe so rclone can exit.
                                callback_errors.append(exc)
                        stats = record.get("stats")
                        if isinstance(stats, dict):
                            percent, activity = rclone_progress(stats)
                            if report is not None:
                                report(percent, activity=activity)
                            continue
                        message = record.get("msg", line)
                except (ValueError, TypeError):
                    pass
                if not isinstance(message, str):
                    continue
                match = percentage.search(message)
                if match and report is not None:
                    report(min(100, float(match.group(1))), activity="transferindo arquivos")

        reader = threading.Thread(target=collect, daemon=True)
        reader.start()
        try:
            returncode = process.wait(timeout=self.timeout)
        except BaseException:
            # An interruption must also release the reader and child process.
            process.kill()
            process.wait()
            raise
        finally:
            reader.join()
            process.stdout.close()
        output = "".join(output)
        self.last_stderr = output
        if callback_errors:
            raise callback_errors[0]
        return subprocess.CompletedProcess(args, returncode, "", output)


def show_progress(runner, progress):
    if isinstance(runner, Runner):
        runner.progress = progress
