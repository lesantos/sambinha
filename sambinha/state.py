"""Atomic state persistence and an advisory single-writer lock on Linux."""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import tempfile
import math

from .errors import BackupError
from .inventory import safe_relative


def atomic_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".state-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(data, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def load_state(path: Path, identity: dict):
    if not path.exists():
        return {"schema": 1, "identity": identity, "active": {}, "retired": []}
    try:
        data = json.loads(path.read_text())
        if data["schema"] != 1 or data["identity"] != identity:
            raise BackupError("Estado pertence a outra origem/destino/lógica; use outro nome de job")
        if not isinstance(data["active"], dict) or not isinstance(data["retired"], list):
            raise ValueError("estrutura")
        for key in data["active"]:
            safe_relative(key)
        if not isinstance(data.get("excluded", {}), dict):
            raise ValueError("lotes excluídos")
        for key in data.get("excluded", {}):
            safe_relative(key)
        for entry in data["retired"]:
            safe_relative(entry["key"])
            if type(entry["expires"]) not in (int, float) or not math.isfinite(entry["expires"]):
                raise ValueError("expiração")
            if "alert_at" in entry and (type(entry["alert_at"]) not in (int, float) or not math.isfinite(entry["alert_at"])):
                raise ValueError("aviso")
            if "files" in entry:
                if not isinstance(entry["files"], list):
                    raise ValueError("arquivos")
                for name in entry["files"]:
                    safe_relative(name)
            if "control" in entry:
                safe_relative(entry["control"])
        return data
    except (ValueError, KeyError, TypeError) as exc:
        raise BackupError("Estado inválido; recupere state.json antes de continuar") from exc


@contextmanager
def lock(directory: Path):
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "run.lock").open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise BackupError("Outra execução já usa este diretório de estado") from exc
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)
