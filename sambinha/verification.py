"""Verify native trees, local archives or S3 object metadata."""
import hashlib
import json
from pathlib import Path
import tempfile

from .archive_recovery import check_content, unpack
from .errors import BackupError
from .protection import timestamp_ns
from .recovery_index import read_index
from .state import indexed_files
from .storage import Storage


def verify_job(config, job, runner, log, state=None, *, progress=None):
    storage = Storage(job, config, runner)
    if job.logic == "simple":
        storage.check_simple()
        log.event("verification_complete", job=job.name, mode="rclone_check")
        return {"mode": "rclone_check"}
    with tempfile.TemporaryDirectory(prefix="sambinha-verify-") as directory:
        work = Path(directory)
        state = state if state is not None else read_index(config, job, storage, work,
                                                         metadata_only=job.type == "s3")
        files = indexed_files(state, job.logic)
        if progress:
            progress.update("Verificando backup", job.destination, 0, len(files))
        if job.type == "s3":
            if job.logic == "simple" and config.verification_mode == "simple":
                for index, (key, item) in enumerate(state["active"].items()):
                    remote = storage.stat(key, hashes=False)
                    if remote["Size"] != item["size"] or timestamp_ns(remote.get("ModTime")) != item["mtime_ns"]:
                        raise BackupError(f"Integridade divergente no S3: {key}")
                    if progress:
                        progress.update("Verificando backup", job.destination, index + 1, len(files))
                    log.event("verified_object", job=job.name, path=key, mode="s3_metadata")
                result = {"files": len(files), "bytes": sum(f["size"] for f in files.values()), "mode": "s3_metadata"}
                log.event("verification_complete", job=job.name, **result)
                return result
            expected = ([(key, item["size"], item["md5"]) for key, item in state["active"].items()]
                        if job.logic == "simple" else [])
            if job.logic == "batch":
                for key, control in state["active"].items():
                    if "archive_md5" not in control:
                        raise BackupError(f"Lote antigo sem checksum de envio no índice: {key}; verificação indisponível")
                    expected.append((key, control["archive_size"], control["archive_md5"]))
                    encoded = (json.dumps(control, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
                    expected.append((key + ".json", len(encoded), hashlib.md5(encoded, usedforsecurity=False).hexdigest()))
            for key, size, md5 in expected:
                remote = storage.stat(key)
                hashes = remote.get("Hashes", {})
                actual = hashes.get("md5") or hashes.get("MD5")
                if actual is None:
                    raise BackupError(f"Checksum S3 indisponível nos metadados: {key}; nenhum objeto será baixado")
                if remote["Size"] != size or actual != md5:
                    raise BackupError(f"Integridade divergente no S3: {key}")
                log.event("verified_object", job=job.name, path=key, mode="s3_metadata")
        elif job.logic == "simple":
            for index, metadata in enumerate(files.values()):
                check_content(Path(job.destination), metadata, mode=config.verification_mode, check_mtime=True)
                if progress:
                    progress.update("Verificando backup", (Path(job.destination) / metadata["path"]).parent,
                                    index + 1, len(files))
        else:
            for index, (key, control) in enumerate(state["active"].items()):
                lot = work / str(index)
                lot.mkdir(mode=0o700)
                unpack(storage, runner, key, control, lot)
        result = {"files": len(files), "bytes": sum(f["size"] for f in files.values()),
                  "mode": "s3_metadata" if job.type == "s3" else
                          ("simple" if job.logic == "simple" and config.verification_mode == "simple" else "contents")}
        log.event("verification_complete", job=job.name, **result)
        return result
