"""Restore selected files and verify local contents or S3 object metadata."""
from contextlib import nullcontext
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tarfile
import tempfile

from .config import overlap
from .disks import check_disk, check_disks
from .errors import BackupError, JobError
from .inventory import fingerprint, metadata as file_metadata, safe_relative, scan
from .logging import RunLog
from .process import Runner
from .state import atomic_json, load_state, lock, private_directory
from .storage import Storage
from .progress import Progress
from .protection import timestamp_ns
from .filesystems import supports_posix_metadata


def identity(job):
    return {"source": str(job.source), "destination": job.destination,
            "type": job.type, "logic": job.logic, "namespace": ".sambinha"}


def read_index(config, job, storage, work, *, metadata_only=False):
    path = config.state_dir / job.name / "state.json"
    if path.exists() or path.is_symlink():
        return load_state(path, identity(job))
    if metadata_only:
        raise BackupError("Verificação S3 exige o índice local; nenhum objeto será baixado")
    if job.logic != "batch":
        raise BackupError("Restauração e verificação da cópia simples exigem o índice local")
    archive = work / "control.tar.gz"
    storage.get(".sambinha/control.tar.gz", archive)
    # Read only this bounded regular member; never extract arbitrary tar paths.
    with tarfile.open(archive) as tar:
        entries = [m for m in tar.getmembers() if m.name == "state.json"]
        if len(entries) != 1 or not entries[0].isreg() or entries[0].size > 64 * 1024 * 1024:
            raise BackupError("Índice de recuperação inválido")
        state = json.load(tar.extractfile(entries[0]))
    recovered = work / "state.json"
    atomic_json(recovered, state)
    return load_state(recovered, identity(job))


def indexed_files(state, logic):
    if logic == "simple":
        return dict(state["active"])
    return {f["path"]: f for control in state["active"].values() for f in control["files"]}


def matches(path, selectors):
    return not selectors or any(path == p or path.startswith(p + "/") for p in selectors)


def check_content(root, metadata, *, mode=None, check_mtime=False):
    mode = mode or ("hash" if metadata["sha256"] and metadata["md5"] else "simple")
    if mode == "hash" and not (metadata["sha256"] and metadata["md5"]):
        raise BackupError("Índice sem hashes; execute um backup com verification_mode=hash antes de verificar por hash")
    actual = fingerprint(root, metadata["path"]) if mode == "hash" else file_metadata(root, metadata["path"])
    keys = ("size", "sha256", "md5") if mode == "hash" else (("size", "mtime_ns") if check_mtime else ("size",))
    if any(getattr(actual, key) != metadata[key] for key in keys):
        raise BackupError(f"Integridade divergente: {metadata['path']}")


def unpack(storage, runner, key, control, work):
    archive = work / "archive"
    storage.get(key, archive)
    if "archive_md5" in control:
        actual = fingerprint(work, "archive")
        if actual.size != control["archive_size"] or actual.md5 != control["archive_md5"]:
            raise BackupError(f"Checksum do lote divergente: {key}")
    listing = runner.run([storage.job.compressor, "l", "-slt", "-ba", str(archive)])
    members = {}
    for record in listing.strip().split("\n\n"):
        fields = dict(line.split(" = ", 1) for line in record.splitlines() if " = " in line)
        if "Path" not in fields:
            continue
        name = fields["Path"].removeprefix("./")
        safe_relative(name)
        if ("\\" in name or any(k in fields for k in ("Symbolic Link", "Hard Link"))
                or re.search(r"(?:^|\s)[lbcp]", fields.get("Attributes", ""))):
            raise BackupError("Lote contém link ou arquivo especial")
        if fields.get("Folder") == "+" or fields.get("Attributes", "").startswith("D"):
            continue
        if name in members:
            raise BackupError("Arquivo duplicado no lote")
        members[name] = int(fields["Size"])
    if members != {f["path"]: f["size"] for f in control["files"]}:
        raise BackupError("Lote incompleto ou caminhos diferentes do índice")
    runner.run([storage.job.compressor, "t", str(archive)])
    extracted = work / "files"
    extracted.mkdir(mode=0o700)
    check_disk(extracted, "temporários de recuperação")
    runner.run([storage.job.compressor, "x", "-y", "-spd", "-o" + str(extracted), str(archive)])
    actual = scan(extracted, mode="simple")
    if {f.path for f in actual} != set(members):
        raise BackupError("Conteúdo extraído diferente do índice")
    for metadata in control["files"]:
        check_content(extracted, metadata)
    return extracted


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


def validate_output(config, output, *, check_space=True):
    output = Path(output).absolute()
    if any(p.is_symlink() for p in (output, *output.parents)):
        raise BackupError("Destino de restauração contém link simbólico")
    output = output.resolve()
    protected = [config.state_dir, config.log_dir, *(j.source for j in config.jobs)]
    protected += [Path(j.destination).resolve() for j in config.jobs if j.type == "local"]
    if any(overlap(output, p) for p in protected):
        raise BackupError("Restauração deve usar uma pasta separada da origem, backup, estado e logs")
    if output.exists() and (not output.is_dir() or next(output.iterdir(), None) is not None):
        raise BackupError("Pasta de restauração deve ser nova ou vazia")
    if check_space:
        check_disk(output, "restauração")
    return output


def restore_job(config, job, runner, log, output, selectors=()):
    output = validate_output(config, output, check_space=job.check_destination_disk)
    selectors = tuple(safe_relative(p.rstrip("/")) for p in selectors)
    storage = Storage(job, config, runner)
    if job.logic == "simple":
        storage.restore_simple(output, selectors, dry_run=config.dry_run)
        log.event("restore_summary", job=job.name, dry_run=config.dry_run,
                  strategy="rclone_copy", selectors=list(selectors))
        return
    with tempfile.TemporaryDirectory(prefix="sambinha-restore-") as directory:
        work = Path(directory)
        state = read_index(config, job, storage, work)
        files = indexed_files(state, job.logic)
        selected = {p: f for p, f in files.items() if matches(p, selectors)}
        if selectors and any(not any(matches(p, (s,)) for p in files) for s in selectors):
            raise BackupError("Caminho solicitado não está no índice ativo")
        if config.dry_run:
            log.event("restore_summary", job=job.name, files=len(selected),
                      bytes=sum(f["size"] for f in selected.values()), dry_run=True)
            return
        check_disk(work, "temporários de restauração")
        staged = work / "staged"
        staged.mkdir(mode=0o700)
        if job.logic == "simple":
            for path, metadata in selected.items():
                storage.get(path, staged / path)
                check_content(staged, metadata)
        else:
            for index, (key, control) in enumerate(state["active"].items()):
                members = [f for f in control["files"] if f["path"] in selected]
                if not members:
                    continue
                lot = work / str(index)
                lot.mkdir(mode=0o700)
                extracted = unpack(storage, runner, key, control, lot)
                for metadata in members:
                    target = staged / metadata["path"]
                    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                    shutil.move(extracted / metadata["path"], target)
        # Publish only after all requested content has passed verification.
        validate_output(config, output, check_space=job.check_destination_disk)
        preserve_permissions = supports_posix_metadata(output)
        if preserve_permissions:
            private_directory(output)
        else:
            # validate_output already rejected symlinks and nonempty output.
            output.mkdir(mode=0o700, parents=True, exist_ok=True)
        for path, metadata in selected.items():
            if job.check_destination_disk:
                check_disk(output, "restauração")
            target = output / path
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            if any(p.is_symlink() for p in (target.parent, *target.parent.parents)):
                raise BackupError("Destino de restauração mudou para link simbólico")
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "wb") as stream, (staged / path).open("rb") as source:
                shutil.copyfileobj(source, stream)
                if preserve_permissions:
                    os.fchmod(stream.fileno(), metadata["mode"] & 0o777)
                stream.flush()
                os.utime(stream.fileno(), ns=(metadata["mtime_ns"], metadata["mtime_ns"]))
            log.event("restored", job=job.name, path=path)
        if job.check_destination_disk:
            check_disk(output, "restauração")
        log.event("restore_summary", job=job.name, files=len(selected),
                  bytes=sum(f["size"] for f in selected.values()), dry_run=False)


def recover(config, action, *, output=None, selectors=(), runner=None, notifier=None):
    runner = runner or Runner(config.timeout, env=config.environment)
    redactor = Runner(env=config.environment, secrets=(config.cronitor_url,)).safe_error
    progress = Progress(redact=redactor)
    if isinstance(runner, Runner):
        runner.progress = progress
    log = RunLog(config.log_dir, redact=redactor, progress=progress,
                 file_level=config.log_file_level, terminal_level=config.log_terminal_level)
    failed = False
    log.event("start", action=action, dry_run=config.dry_run)
    try:
        runner.load_config_secrets(config.rclone_config)
        runner.require([config.rclone])
        if action == "restore" and (len(config.jobs) != 1 or output is None):
            raise BackupError("Restauração exige --job com uma tarefa e --output")
        with lock(config.state_dir) if action == "verify" and not config.dry_run else nullcontext():
            if not config.dry_run:
                removed_logs = log.prune(config.log_retention_days)
                if removed_logs:
                    log.event("log_cleanup", removed_logs=removed_logs, retention_days=config.log_retention_days)
            for job in config.jobs:
                try:
                    runner.require([job.compressor] if job.logic == "batch"
                                   and (action == "restore" or job.type == "local") else [])
                    check_disks(job, lambda event, **values: log.event(event, job=job.name, **values))
                    if action == "restore":
                        restore_job(config, job, runner, log, output, selectors)
                    elif config.dry_run:
                        log.event("would_verify", job=job.name, mode="s3_metadata" if job.type == "s3" else "contents")
                    else:
                        verify_job(config, job, runner, log, progress=progress)
                except (BackupError, OSError, ValueError, KeyError, TypeError, tarfile.TarError) as exc:
                    failed = True
                    message = str(JobError(job.name, exc))
                    log.event("error", job=job.name, error=message)
                    if notifier and not config.dry_run:
                        notifier(config.cronitor_url, "fail", log.id, redactor(message)[:2000])
                    if not job.continue_on_job_error:
                        break
    except (BackupError, OSError, ValueError) as exc:
        failed = True
        log.event("error", error=str(exc))
    finally:
        log.event("end", action=action, success=not failed)
        if not failed:
            progress.finish()
        progress.close()
        log.close()
    return int(failed)
