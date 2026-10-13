"""Bounded subprocess execution; no shell interpolation or hidden rclone retries."""
import shutil
import os
import re
import subprocess
import time
import json
import threading
from configparser import ConfigParser, Error as ConfigParserError
from contextlib import nullcontext
from pathlib import Path

from .errors import BackupError

_CREDENTIAL = re.compile(r"SECRET|TOKEN|PASSWORD|(?:^|_)PASS(?:$|_)|ACCESS_KEY|(?:^|_)KEY(?:$|_)|CREDENTIAL|AUTHORIZATION|CRONITOR_URL", re.I)


def credential_values(environment):
    return {value for key, value in environment.items()
            if isinstance(value, str) and value and _CREDENTIAL.search(key)}


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

    def run(self, args, *, cwd=None, retry=False, input=None, progress_directory=None):
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
                command = [*args, "--stats", "1s", "--stats-one-line", "--stats-log-level", "NOTICE"] if measured else args
                task = self.progress.task(stage, directory, measured=measured) if self.progress and stage else nullcontext()
                with task as report:
                    if measured:
                        result = self._run_with_progress(command, cwd, input, report)
                    else:
                        result = subprocess.run(command, cwd=cwd, input=input, capture_output=True,
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

    def _run_with_progress(self, args, cwd, input, report):
        """Stream rclone stats while retaining its output for audit parsing/errors."""
        process = subprocess.Popen(args, cwd=cwd, stdin=subprocess.PIPE if input is not None else None,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, bufsize=1, env=self.env)
        if input is not None:
            process.stdin.write(input)
            process.stdin.close()

        output = []
        percentage = re.compile(r"Transferred:\s*[^,]*,\s*(\d+(?:\.\d+)?)%")

        def collect():
            for line in process.stdout:
                output.append(line)
                message = line
                try:
                    record = json.loads(line)
                    if isinstance(record, dict):
                        message = record.get("msg", line)
                except (ValueError, TypeError):
                    pass
                match = percentage.search(message)
                if match:
                    report(float(match.group(1)))

        reader = threading.Thread(target=collect, daemon=True)
        reader.start()
        try:
            returncode = process.wait(timeout=self.timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            reader.join()
            raise
        reader.join()
        output = "".join(output)
        self.last_stderr = output
        return subprocess.CompletedProcess(args, returncode, "", output)
