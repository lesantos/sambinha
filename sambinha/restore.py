"""Restore selected content only after validating every staged member."""
import os
from pathlib import Path
import shutil
import tempfile

from .archive_recovery import check_content, unpack
from .disks import check_disk
from .errors import BackupError
from .filesystems import supports_posix_metadata
from .inventory import safe_relative
from .paths import overlap
from .recovery_index import read_index
from .state import indexed_files, private_directory
from .storage import Storage


def matches(path, selectors):
    return not selectors or any(path == p or path.startswith(p + "/") for p in selectors)


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
