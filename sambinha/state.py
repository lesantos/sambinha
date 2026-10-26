"""Atomic state persistence and an advisory single-writer lock on Linux."""
import fcntl
import json
import math
import os
import re
import socket
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

from .errors import BackupError, ForeignStateError, UnsupportedStateError
from .inventory import safe_relative
from .local_paths import reject_symlinks
from .names import NAMESPACE

# State-file schema: 1 (pre-0.13) and 2 (0.13, confirmed generations) carry a
# legacy path-based identity; 3 carries the name-based identity below. Older
# executables refuse schema 3 as unsupported instead of misreading the identity.
STATE_SCHEMA = 3
IDENTITY_SCHEMA = 2
MAX_ORIGINS = 8
IDENTITY_KEYS = ("schema", "name", "type", "logic", "namespace")


def identity(job):
    """Describe the job that owns an index: the job NAME, not the mount points.

    Source and local destination paths differ between machines sharing one
    backup, so they are recorded informationally in ``state["origins"]``. An S3
    destination is the same string everywhere and distinguishes two jobs that
    share a bucket.
    """
    result = {"schema": IDENTITY_SCHEMA, "name": job.name, "type": job.type,
              "logic": job.logic, "namespace": NAMESPACE}
    if job.type == "s3":
        result["destination"] = job.destination
    return result


def legacy_identity(job):
    """The pre-0.16 identity this job would have written on this machine."""
    return {"source": str(job.source), "destination": job.destination,
            "type": job.type, "logic": job.logic, "namespace": NAMESPACE}


def identity_matches(stored, expected, *, adopt=False, legacy=None):
    """Decide whether persisted metadata with ``stored`` identity belongs to ``expected``.

    - schema-2 vs schema-2: the dicts must be equal; with ``adopt`` a different
      ``name`` alone (renamed job) is accepted.
    - legacy (no ``name``) vs schema-2: accepted when it equals ``legacy``, the
      identity the current job writes with the old layout (same machine, same
      paths); with ``adopt`` also when only the paths differ, never when
      type/logic/namespace or an S3 destination differ.
    """
    if not isinstance(stored, dict) or not isinstance(expected, dict):
        return False
    if "name" not in stored:
        if legacy is not None and stored == legacy:
            return True
        return adopt and all(stored.get(k) == expected[k] for k in ("type", "logic", "namespace")
                             if k in expected) and ("destination" not in expected
                                                    or stored.get("destination") == expected["destination"])
    if stored == expected:
        return True
    return adopt and {k: v for k, v in stored.items() if k != "name"} == \
        {k: v for k, v in expected.items() if k != "name"}


def identity_options(job):
    """Keyword arguments for load_state/identity_matches derived from a job."""
    return {"adopt": job.adopt_identity, "legacy": legacy_identity(job)}


def migrate_identity(state, job, log=None):
    """Rewrite an accepted legacy/renamed identity as the current one and flag republication.

    Returns the previous identity when something changed, else None. The caller
    already validated the match; the rewrite reaches the destination through
    ``index_pending`` so the remote index carries the new identity too.
    """
    expected = identity(job)
    previous = state["identity"]
    if previous == expected:
        return None
    state["identity"] = expected
    state["schema"] = STATE_SCHEMA
    state["index_pending"] = True
    if log is not None:
        event = "identity_migrated" if previous == legacy_identity(job) else "identity_adopted"
        log.event(event, job=job.name, previous_identity=previous, identity=expected)
    return previous


def origin(job, now=None):
    """Where this machine reads and writes the job; informational only."""
    return {"host": socket.gethostname(), "platform": sys.platform, "source": str(job.source),
            "destination": job.destination, "last_seen": time.time() if now is None else now}


def record_origin(state, job, now=None):
    """Upsert the current host in state['origins'] (newest last, bounded)."""
    current = origin(job, now)
    others = [o for o in state.get("origins", []) if o.get("host") != current["host"]]
    state["origins"] = (others + [current])[-MAX_ORIGINS:]


def validate_identity(data):
    """Structural check of a stored identity; ownership is decided by identity_matches."""
    if not isinstance(data, dict) or not all(isinstance(k, str) for k in data):
        raise ValueError("identidade")
    if "name" in data:
        if (data.get("schema") != IDENTITY_SCHEMA or not isinstance(data["name"], str) or not data["name"]
                or set(data) - set(IDENTITY_KEYS) - {"destination"}):
            raise ValueError("identidade")


def validate_origins(origins):
    if not isinstance(origins, list) or len(origins) > MAX_ORIGINS:
        raise ValueError("origens")
    for item in origins:
        if (not isinstance(item, dict)
                or any(not isinstance(item.get(k), str) for k in ("host", "platform", "source", "destination"))
                or type(item.get("last_seen")) not in (int, float) or not math.isfinite(item["last_seen"])):
            raise ValueError("origens")


def indexed_files(state, logic):
    """Expose the active file metadata independently of the strategy layout."""
    if logic == "simple":
        return dict(state["active"])
    return {f["path"]: f for control in state["active"].values() for f in control["files"]}

def private_directory(path: Path, *, preserve_permissions=True):
    # Metadata roots are private, and must not redirect writes through links.
    reject_symlinks(path, label="Diretório de metadados")
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
                                            (control['individual'] and len(members) != 1)):
                raise ValueError('lote individual inválido')
            if 'passthrough' in control and (type(control['passthrough']) is not bool or
                                             (control['passthrough'] and
                                             (len(members) != 1 or not control.get('individual', True)))):
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


def load_state(path: Path, identity: dict, *, adopt=False, legacy=None):
    """Read and validate a state file owned by ``identity`` (see identity_matches).

    A missing file yields an empty schema-3 state. Schema 1/2 files (legacy
    path-based identity) are still readable; the identity itself is migrated by
    the caller (migrate_identity), never silently here.
    """
    reject_symlinks(path, label="Estado", include_parents=False)
    if not path.exists():
        return {"schema": STATE_SCHEMA, "identity": identity, "active": {}, "retired": []}
    try:
        data = json.loads(path.read_text())
        if type(data["schema"]) is not int or data["schema"] not in {1, 2, STATE_SCHEMA}:
            raise UnsupportedStateError("Versão de estado não suportada")
        validate_identity(data["identity"])
        if data["schema"] == STATE_SCHEMA and "name" not in data["identity"]:
            raise ValueError("identidade sem nome")
        if not identity_matches(data["identity"], identity, adopt=adopt, legacy=legacy):
            raise ForeignStateError("Estado pertence a outro job (nome/tipo/lógica); "
                                    "use --adopt-identity para assumir um backup existente")
        if not isinstance(data["active"], dict) or not isinstance(data["retired"], list):
            raise ValueError("estrutura")
        validate_origins(data.get("origins", []))
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
