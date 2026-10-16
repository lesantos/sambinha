"""Atomic state persistence and an advisory single-writer lock on Linux."""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import tempfile
import math
import re

from .errors import BackupError
from .inventory import safe_relative


def identity(job):
    """Describe the job that owns an index without depending on recovery."""
    return {"source": str(job.source), "destination": job.destination,
            "type": job.type, "logic": job.logic, "namespace": ".sambinha"}


def indexed_files(state, logic):
    """Expose the active file metadata independently of the strategy layout."""
    if logic == "simple":
        return dict(state["active"])
    return {f["path"]: f for control in state["active"].values() for f in control["files"]}

def private_directory(path: Path, *, preserve_permissions=True):
    # Metadata roots are private, and must not redirect writes through links.
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise BackupError("Diretório de metadados contém link simbólico")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if preserve_permissions:
        path.chmod(0o700)


def validate_file(item):
    if not isinstance(item, dict) or set(item) != {"path", "size", "sha256", "md5", "mtime_ns", "mode"}:
        raise ValueError("metadados de arquivo")
    safe_relative(item["path"])
    if (type(item["size"]) is not int or item["size"] < 0
            or type(item["mtime_ns"]) is not int
            or type(item["mode"]) is not int or not 0 <= item["mode"] <= 0o7777):
        raise ValueError("atributos de arquivo")
    for name, length in (("sha256", 64), ("md5", 32)):
        if item["sha256"] == item["md5"] == "":
            continue  # Metadata-only inventories deliberately have no hashes.
        if not isinstance(item[name], str) or not re.fullmatch(f"[0-9a-f]{{{length}}}", item[name]):
            raise ValueError("checksum")


def validate_controls(controls, batch):
    paths = set()
    for key, control in controls.items():
        safe_relative(key)
        if batch:
            if (not isinstance(control, dict) or control.get("schema") != 1
                    or control.get("archive") != key or not isinstance(control.get("files"), list)
                    or not control["files"]):
                raise ValueError("controle de lote")
            members = control["files"]
            if 'individual' in control and (type(control['individual']) is not bool or
                                            control['individual'] and len(members) != 1):
                raise ValueError('lote individual inválido')
            if 'passthrough' in control and (type(control['passthrough']) is not bool or
                                             control['passthrough'] and
                                             (len(members) != 1 or not control.get('individual', True))):
                raise ValueError('arquivo compactado individual inválido')
            if 'batch_id' in control and (not isinstance(control['batch_id'], str) or
                                          not re.fullmatch('[0-9a-f]{24}', control['batch_id'])):
                raise ValueError('identidade de lote inválida')
            if "archive_size" in control or "archive_md5" in control:
                if (type(control.get("archive_size")) is not int or control["archive_size"] < 0
                        or not isinstance(control.get("archive_md5"), str)
                        or not re.fullmatch("[0-9a-f]{32}", control["archive_md5"])):
                    raise ValueError("checksum do lote")
        else:
            members = [control]
        for item in members:
            validate_file(item)
            if item["path"] in paths or (not batch and item["path"] != key):
                raise ValueError("caminho duplicado ou incompatível")
            paths.add(item["path"])
    # A file cannot also be the ancestor directory of another indexed file.
    for name in paths:
        if any(parent.as_posix() in paths for parent in Path(name).parents if parent.as_posix() != "."):
            raise ValueError("arquivo e diretório sobrepostos")


def atomic_json(path: Path, data, *, preserve_permissions=True):
    private_directory(path.parent, preserve_permissions=preserve_permissions)
    fd, temporary = tempfile.mkstemp(prefix=".state-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(data, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        Path(temporary).unlink(missing_ok=True)


def load_state(path: Path, identity: dict):
    if path.is_symlink():
        raise BackupError("Estado contém link simbólico")
    if not path.exists():
        return {"schema": 1, "identity": identity, "active": {}, "retired": []}
    try:
        data = json.loads(path.read_text())
        if type(data["schema"]) is not int or data["schema"] not in {1, 2}:
            raise BackupError("Versão de estado não suportada")
        if data["identity"] != identity:
            raise BackupError("Estado pertence a outra origem/destino/lógica; use outro nome de job")
        if not isinstance(data["active"], dict) or not isinstance(data["retired"], list):
            raise ValueError("estrutura")
        if 'generation' in data and (not isinstance(data['generation'], str) or
                                     not re.fullmatch('[0-9]{20}-[0-9a-f]{32}', data['generation'])):
            raise ValueError('geração inválida')
        batch = identity.get("logic") == "batch"
        validate_controls(data["active"], batch)
        if not isinstance(data.get("excluded", {}), dict):
            raise ValueError("lotes excluídos")
        # Excluded lots may overlap each other and the active selection, but
        # every individual control must still have safe, unique members.
        for key, control in data.get("excluded", {}).items():
            validate_controls({key: control}, True)
        retired = set()
        for entry in data["retired"]:
            safe_relative(entry["key"])
            if entry["key"] in data["active"] or entry["key"] in retired:
                raise ValueError("histórico duplicado ou ativo")
            retired.add(entry["key"])
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
                if entry["control"] != entry["key"] + ".json":
                    raise ValueError("controle de histórico incompatível")
        if "last_verified" in data and (type(data["last_verified"]) not in (int, float)
                                        or not math.isfinite(data["last_verified"])):
            raise ValueError("data de verificação")
        if "last_full_scan" in data and (type(data["last_full_scan"]) not in (int, float)
                                         or not math.isfinite(data["last_full_scan"])):
            raise ValueError("data de inventário")
        if not isinstance(data.get("fingerprints", {}), dict):
            raise ValueError("cache de inventário")
        for key, cached in data.get("fingerprints", {}).items():
            validate_file(cached["file"])
            if (cached["file"]["path"] != key or not isinstance(cached["signature"], list)
                    or len(cached["signature"]) != 6
                    or any(type(value) is not int for value in cached["signature"])):
                raise ValueError("cache de arquivo")
        return data
    except (ValueError, KeyError, TypeError) as exc:
        raise BackupError("Estado inválido; recupere state.json antes de continuar") from exc


@contextmanager
def lock(directory: Path):
    private_directory(directory)
    fd = os.open(directory / "run.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "a") as stream:
        os.fchmod(stream.fileno(), 0o600)
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise BackupError("Outra execução já usa este diretório de estado") from exc
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)
