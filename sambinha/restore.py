"""Restore selected content only after validating every staged member."""
from contextlib import contextmanager
import errno
import os
from pathlib import Path
import shutil
import tempfile

from .archive_recovery import unpack
from .disks import check_disk
from .errors import BackupError
from .filesystems import supports_posix_metadata
from .inventory import safe_relative
from .local_paths import directory
from .paths import overlap
from .recovery_index import read_index
from .state import indexed_files, private_directory
from .storage import Storage


def matches(path, selectors):
    return not selectors or any(path == p or path.startswith(p + "/") for p in selectors)


def validate_output(config, output, *, check_space=True, workspace=None):
    output = Path(output).absolute()
    if any(p.is_symlink() for p in (output, *output.parents)):
        raise BackupError("Destino de restauração contém link simbólico")
    output = output.resolve()
    protected = [config.state_dir, config.log_dir, *(j.source for j in config.jobs)]
    if config.metadata_dir:
        protected.append(config.metadata_dir)
    protected += [Path(j.destination).resolve() for j in config.jobs if j.type == "local"]
    if any(overlap(output, p) for p in protected):
        raise BackupError("Restauração deve usar uma pasta separada da origem, backup, estado e logs")
    if output.exists() and (not output.is_dir() or
                            any(child != workspace for child in output.iterdir())):
        raise BackupError("Pasta de restauração deve ser nova ou vazia")
    if check_space:
        check_disk(output, "restauração")
    return output


@contextmanager
def restore_workspace(output, *, dry_run=False):
    """Keep downloads and staging on the output filesystem, including mounted roots."""
    created = not output.exists()
    parent = output
    if dry_run:
        while not parent.exists():
            parent = parent.parent
    else:
        private_directory(output, preserve_permissions=supports_posix_metadata(output))
    try:
        with tempfile.TemporaryDirectory(prefix=".sambinha-restore-", dir=parent) as temp:
            yield Path(temp)
    except BaseException:
        if created and not dry_run:
            try:
                output.rmdir()
            except OSError:
                pass  # Never remove files published or created by another process.
        raise


def publish_file(source, output, metadata, *, preserve_permissions):
    """Publish without overwriting, using a hard link when the filesystem permits it."""
    target = output / metadata["path"]
    with directory(target.parent, create=True) as parent:
        source_fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
        target_fd = None
        try:
            try:
                os.link(source, target.name, dst_dir_fd=parent, follow_symlinks=False)
                target_fd = os.open(target.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
            except OSError as exc:
                if exc.errno not in {errno.EPERM, errno.EOPNOTSUPP, errno.ENOSYS, errno.EXDEV, errno.EMLINK}:
                    raise
                target_fd = os.open(target.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                    0o600, dir_fd=parent)
                with os.fdopen(os.dup(target_fd), "wb") as stream, os.fdopen(os.dup(source_fd), "rb") as content:
                    shutil.copyfileobj(content, stream)
            if preserve_permissions:
                os.fchmod(target_fd, metadata["mode"] & 0o777)
            os.utime(target_fd, ns=(metadata["mtime_ns"], metadata["mtime_ns"]))
        finally:
            os.close(source_fd)
            if target_fd is not None:
                os.close(target_fd)
    source.unlink()


def restore_job(config, job, runner, log, output, selectors=()):
    output = validate_output(config, output, check_space=job.check_destination_disk)
    selectors = tuple(safe_relative(p.rstrip("/")) for p in selectors)
    storage = Storage(job, config, runner)
    if job.logic == "simple":
        storage.restore_simple(output, selectors, dry_run=job.dry_run)
        log.event("restore_summary", job=job.name, dry_run=job.dry_run,
                  strategy="rclone_copy", selectors=list(selectors))
        return
    with restore_workspace(output, dry_run=job.dry_run) as work:
        storage.temporary_directory = work
        state = read_index(config, job, storage, work)
        files = indexed_files(state, job.logic)
        selected = {p: f for p, f in files.items() if matches(p, selectors)}
        if selectors and any(not any(matches(p, (s,)) for p in files) for s in selectors):
            raise BackupError("Caminho solicitado não está no índice ativo")
        if job.dry_run:
            log.event("restore_summary", job=job.name, files=len(selected),
                      bytes=sum(f["size"] for f in selected.values()), dry_run=True)
            return
        check_disk(work, "temporários de restauração")
        staged = work / "staged"
        staged.mkdir(mode=0o700)
        for key, control in state["active"].items():
            members = [f for f in control["files"] if f["path"] in selected]
            if not members:
                continue
            with tempfile.TemporaryDirectory(prefix="batch-", dir=work) as temp:
                lot = Path(temp)
                extracted = unpack(storage, runner, key, control, lot)
                for metadata in members:
                    target = staged / metadata["path"]
                    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                    shutil.move(extracted / metadata["path"], target)
        # Publish only after all requested content has passed verification.
        validate_output(config, output, check_space=job.check_destination_disk, workspace=work)
        preserve_permissions = supports_posix_metadata(output)
        for path, metadata in selected.items():
            if job.check_destination_disk:
                check_disk(output, "restauração")
            publish_file(staged / path, output, metadata, preserve_permissions=preserve_permissions)
            log.event("restored", job=job.name, path=path)
        if job.check_destination_disk:
            check_disk(output, "restauração")
        log.event("restore_summary", job=job.name, files=len(selected),
                  bytes=sum(f["size"] for f in selected.values()), dry_run=False)
