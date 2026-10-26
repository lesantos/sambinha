"""Directory-bounded native filtering through one private rclone RC process."""
import base64
import http.client
import json
import re
import secrets
import subprocess
import tempfile
import time
from pathlib import Path, PurePosixPath

from .errors import BackupError
from .inventory import safe_relative
from .process import child_environment
from .tempdirs import prefix

MAX_SUBTREE_ENTRIES = 500_000


class _TooLarge(Exception):
    """A recursive listing exceeded the in-memory entry budget."""


class _ListParser:
    """Incrementally extract ``(Path, IsDir)`` from ``{"list": [{...}, ...]}``."""

    def __init__(self, limit):
        self.limit, self.buffer, self.started, self.done = limit, "", False, False
        self.decoder = json.JSONDecoder()
        self.raw = b""

    def feed(self, chunk, entries):
        """Parse what is complete; return True once ``limit`` entries are exceeded."""
        self.raw += chunk
        try:
            # Keep an incomplete multibyte tail for the next chunk.
            self.buffer += self.raw.decode("utf-8")
            self.raw = b""
        except UnicodeDecodeError as exc:
            if exc.start < len(self.raw) - 4:
                raise
            self.buffer += self.raw[:exc.start].decode("utf-8")
            self.raw = self.raw[exc.start:]
        buffer, position = self.buffer, 0
        if not self.started:
            match = re.match(r'\s*\{\s*"list"\s*:\s*\[', buffer)
            if not match:
                if len(buffer) > 64:
                    raise ValueError("lista esperada")
                return False
            self.started, position = True, match.end()
        while not self.done:
            while position < len(buffer) and buffer[position] in " \t\r\n,":
                position += 1
            if position >= len(buffer):
                break
            if buffer[position] == "]":
                self.done = True
                break
            try:
                item, position = self.decoder.raw_decode(buffer, position)
            except json.JSONDecodeError:
                break
            entries.append((item["Path"], item["IsDir"]))
            if len(entries) > self.limit:
                return True
        self.buffer = buffer[position:]
        return False

    def finish(self):
        if not self.done or self.raw:
            raise ValueError("resposta truncada")


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
        self.port = None
        # Memory trade-off: a loaded subtree keeps (path, is_dir) tuples for all
        # its entries until discovery ends; above max_entries per subtree the
        # listing falls back to one flat request per directory.
        self.max_entries = MAX_SUBTREE_ENTRIES
        self.index, self.subtrees, self.oversized = {}, set(), set()

    def __enter__(self):
        self.directory = tempfile.TemporaryDirectory(prefix=prefix("discovery"),
                                                     dir=self.config.temp_dir)
        try:
            self.log_path = Path(self.directory.name) / "rclone.log"
            self.log = self.log_path.open("wb")
            password = secrets.token_urlsafe(32)
            self.authorization = "Basic " + base64.b64encode(
                ("sambinha:" + password).encode()).decode()
            environment = child_environment(self.runner.env)
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
                    self.port = int(address[1])
                    self._reconnect()
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

    def _call(self, operation, values, deadline, *, limit=None):
        """POST one RC operation. With ``limit`` the list is parsed incrementally.

        The streamed result is ``{"list": [(path, is_dir), ...]}``, keeping only
        names and the directory flag; if more than ``limit`` entries arrive the
        connection is dropped and ``_TooLarge`` raised, so memory stays bounded.
        """
        self._remaining(deadline)
        self.connection.request("POST", "/" + operation, body=json.dumps(values), headers={
            "Authorization": self.authorization, "Content-Type": "application/json"})
        self._remaining(deadline)
        response = self.connection.getresponse()
        chunks = []
        entries = []
        parser = _ListParser(limit) if limit is not None and response.status == 200 else None
        while True:
            # Refresh the remaining budget on every read, rather than granting
            # a fresh full timeout each time the server sends another chunk.
            self._remaining(deadline)
            chunk = response.read1(64 * 1024)
            if not chunk:
                break
            if parser is None:
                chunks.append(chunk)
            elif parser.feed(chunk, entries):
                # Oversized subtree: abandon the body and reconnect lazily.
                self.connection.close()
                raise _TooLarge()
        if parser is not None:
            parser.finish()
            self._remaining(deadline)
            return {"list": entries}
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
        """Direct children of ``relative``, served from a per-subtree index.

        The root is listed flat; the first request below a first-level directory
        lists that whole subtree once (``recurse``) and indexes it by parent, so
        later directories need no HTTP call. Oversized subtrees fall back to one
        flat call per directory. Callers see the same records as before.
        """
        if relative:
            safe_relative(relative)
        top = relative.split("/", 1)[0]
        try:
            if top and top not in self.subtrees and top not in self.oversized:
                self._load_subtree(top)
            if not top or top in self.oversized:
                return self._flat(relative)
            return [{"Path": path, "IsDir": is_dir}
                    for path, is_dir in self.index.get(relative, ())]
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

    def _flat(self, relative):
        deadline = time.monotonic() + self.timeout
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

    def _load_subtree(self, top):
        # One recursive request per first-level subtree, with its own deadline.
        deadline = time.monotonic() + self.timeout
        try:
            records = self._call("operations/list", {
                "fs": str(self.root), "remote": top, "_async": False,
                "opt": {"recurse": True, "noModTime": True, "noMimeType": True},
            }, deadline, limit=self.max_entries)["list"]
        except _TooLarge:
            self.oversized.add(top)
            self._reconnect()
            return
        index = {}
        for path, is_dir in records:
            safe_relative(path)
            parent = PurePosixPath(path).parent.as_posix()
            if path.split("/", 1)[0] != top or type(is_dir) is not bool:
                raise ValueError("caminho fora do subdiretório consultado")
            index.setdefault(parent, []).append((path, is_dir))
        self._remaining(deadline)
        self.index.update(index)
        self.subtrees.add(top)

    def _reconnect(self):
        self.connection = http.client.HTTPConnection("127.0.0.1", self.port)

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
