"""Directory-bounded native filtering through one private rclone RC process."""
import base64
import http.client
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import subprocess
import tempfile
import time

from .errors import BackupError
from .inventory import safe_relative


class Discovery:
    """Own the server and cancel all work on timeout, failure or interruption."""

    def __init__(self, config, runner, root, exclude_file):
        self.config, self.runner = config, runner
        self.root, self.exclude_file = Path(root), exclude_file
        self.process = None
        self.connection = None
        self.directory = None
        self.log = None
        self.timeout = runner.timeout
        self.authorization = None

    def __enter__(self):
        self.directory = tempfile.TemporaryDirectory(prefix="sambinha-discovery-",
                                                     dir=self.config.temp_dir)
        try:
            self.log_path = Path(self.directory.name) / "rclone.log"
            self.log = self.log_path.open("wb")
            password = secrets.token_urlsafe(32)
            self.authorization = "Basic " + base64.b64encode(
                ("sambinha:" + password).encode()).decode()
            environment = dict(os.environ if self.runner.env is None else self.runner.env)
            # Authentication never appears in process arguments or diagnostics.
            environment.update(RCLONE_RC_USER="sambinha", RCLONE_RC_PASS=password)
            arguments = [self.config.rclone, "rcd", "--rc-addr", "127.0.0.1:0",
                         "--rc-no-auth=false", "--rc-serve=false", "--rc-files", "",
                         "--rc-web-gui=false", "--rc-baseurl", "",
                         "--log-level", "NOTICE", "--use-json-log=false",
                         "--log-file", str(self.log_path),
                         "--retries", "1", "--low-level-retries", "1",
                         "--skip-links", "--links=false", "--copy-links=false",
                         "--exclude-from", str(self.exclude_file)]
            if self.config.rclone_config:
                arguments += ["--config", self.config.rclone_config]
            self.process = subprocess.Popen(arguments, env=environment, stdin=subprocess.DEVNULL,
                                            stdout=subprocess.DEVNULL, stderr=self.log)
            deadline = time.monotonic() + min(self.timeout, 10)
            while True:
                diagnostic = self._diagnostic()
                if self.process.poll() is not None:
                    raise BackupError("rclone de descoberta não iniciou: " + diagnostic)
                address = re.search(r"Serving remote control on http://127\.0\.0\.1:(\d+)", diagnostic)
                if address:
                    self.connection = http.client.HTTPConnection("127.0.0.1", int(address[1]))
                    return self
                if time.monotonic() >= deadline:
                    raise BackupError("Timeout ao iniciar rclone de descoberta")
                time.sleep(0.01)
        except BaseException:
            self.close()
            raise

    def _diagnostic(self):
        with self.log_path.open("rb") as stream:
            stream.seek(max(0, self.log_path.stat().st_size - 4000))
            return self.runner.safe_error(stream.read().decode("utf-8", errors="replace"))

    def _call(self, operation, values, deadline):
        self._remaining(deadline)
        self.connection.request("POST", "/" + operation, body=json.dumps(values), headers={
            "Authorization": self.authorization, "Content-Type": "application/json"})
        self._remaining(deadline)
        response = self.connection.getresponse()
        chunks = []
        while True:
            # Refresh the remaining budget on every read, rather than granting
            # a fresh full timeout each time the server sends another chunk.
            self._remaining(deadline)
            chunk = response.read1(64 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        data = json.loads(b"".join(chunks))
        self._remaining(deadline)
        if response.status != 200 or not isinstance(data, dict):
            raise BackupError("rclone de descoberta falhou: " + self.runner.safe_error(str(data)))
        return data

    def _remaining(self, deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError()
        self.connection.timeout = remaining
        if self.connection.sock:
            self.connection.sock.settimeout(remaining)

    def listing(self, relative):
        if relative:
            safe_relative(relative)
        deadline = time.monotonic() + self.timeout
        try:
            # A fixed fs keeps anchored rules relative to the original source.
            records = self._call("operations/list", {
                "fs": str(self.root), "remote": relative, "_async": False,
                "opt": {"recurse": False, "noModTime": True, "noMimeType": True},
            }, deadline)["list"]
            self._remaining(deadline)
            if not isinstance(records, list):
                raise ValueError("lista esperada")
            for item in records:
                path = safe_relative(item["Path"])
                parent = PurePosixPath(path).parent.as_posix()
                if parent != (relative or ".") or type(item["IsDir"]) is not bool:
                    raise ValueError("caminho fora do diretório consultado")
            self._remaining(deadline)
            return records
        except TimeoutError:
            self.close()
            raise BackupError(f"Timeout ao listar diretório da origem: {self.root / relative}") from None
        except (OSError, http.client.HTTPException, ValueError, KeyError, TypeError) as exc:
            self.close()
            raise BackupError(f"Inventário filtrado inválido em {self.root / relative}: "
                              f"{type(exc).__name__}") from None
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.connection:
            self.connection.close()
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        if self.log:
            self.log.close()
        if self.directory:
            self.directory.cleanup()

    def __exit__(self, *_):
        self.close()
