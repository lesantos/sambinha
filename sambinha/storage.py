"""rclone storage adapter shared by local paths and configured Amazon S3 remotes."""
import json
from pathlib import Path
from typing import Any, Protocol

from . import rclone
from .config import Config, Job
from .disks import check_disks
from .errors import BackupError
from .inventory import path_has_symlink, safe_relative
from .local_paths import prepare_write
from .mounts import check_mounts
from .names import NAMESPACE, NAMESPACE_EXCLUDE
from .protection import timestamp_ns
from .simple_storage import SimpleOperations


class StorageBackend(Protocol):
    """What strategies, recovery, verification, restore, rebalance and orphans call on a destination.

    ``Storage`` (rclone: local paths and S3 remotes) is the reference implementation; an alternate
    backend only needs these members. Keys are destination-relative, ``/``-separated strings.
    """

    job: Job
    config: Config
    runner: Any
    #: Staging directory counted by the disk-space guard; callers set it while they hold one.
    temporary_directory: Path | None

    def path(self, key: str) -> str:
        """Full destination address (rclone-style string) of ``key``; rejects unsafe keys."""
        ...

    def command(self, operation: str, *args: Any, retry: bool = False, standard: bool | str = False,
                input: str | None = None, progress_directory: str | None = None,
                on_output: Any = None, source_required: bool | None = None) -> str:
        """Run a raw rclone-style operation (listings, marker/index reads) and return its output."""
        ...

    def inventory(self) -> dict[str, dict[str, Any]]:
        """Recursive listing: safe relative path -> rclone metadata (Size, Hashes...)."""
        ...

    def put(self, source: Any, key: str, *, standard: bool | str = False,
            protected_mtime_ns: int | None = None) -> bool:
        """Upload one object; True when bytes were actually transferred, False when identical."""
        ...

    def get(self, key: str, target: Any) -> None:
        """Download an exact object into an application-owned staging path."""
        ...

    def stat(self, key: str, *, hashes: bool = True) -> dict[str, Any]:
        """Metadata of one object (``Size`` and optional ``Hashes``); never downloads the body."""
        ...

    def delete(self, key: str) -> None:
        """Idempotently delete one exact object."""
        ...

    # Simple-job operations (strategies/simple.py, verification, restore).
    def sync_simple(self, root: Any, backup_dir: str, *, max_delete: int, dry_run: bool = False) -> list[str]: ...

    def expire_simple_history(self, now: float, retention_days: int, *, dry_run: bool = False) -> list[str]: ...

    def check_simple(self) -> None: ...

    def restore_simple(self, output: Any, selectors: Any = (), *, dry_run: bool = False) -> None: ...


class StorageFactory(Protocol):
    """Callable that builds a backend for one job; ``Storage`` itself is the default."""

    def __call__(self, job: Job, config: Config, runner: Any, log: Any = None, *,
                 temporary_directory: Path | None = None, source_required: bool = True) -> StorageBackend: ...


class Storage:
    """rclone-backed StorageBackend; simple-job operations are delegated to SimpleOperations."""

    def __init__(self, job: Job, config: Config, runner, log=None, *, temporary_directory=None, source_required=True):
        self.job, self.config, self.runner = job, config, runner
        self.log = log
        self.temporary_directory = temporary_directory
        self.source_required = source_required
        self._simple = SimpleOperations(self)

    def path(self, key):
        if self.job.type == "local" and path_has_symlink(self.job.destination, key):
            raise BackupError(f"Caminho do destino mudou para link simbólico: {key}")
        return self.job.destination.rstrip("/") + "/" + safe_relative(key)

    def command(self, operation, *args, retry=False, standard=False, input=None, progress_directory=None,
                on_output=None, source_required=None):
        source_required = self.source_required if source_required is None else source_required
        if operation in {"copyto", "copy", "sync", "delete", "purge"}:
            check_mounts(self.job, self.runner, check_source=source_required)
            check_disks(self.job, config=self.config, temporary_path=self.temporary_directory,
                        check_source=source_required)
        return rclone.command(self.config, self.job, self.runner, operation, *args,
                              retry=retry, standard=standard, input=input,
                              progress_directory=progress_directory, on_output=on_output)

    def inventory(self):
        if self.job.type == "local" and not Path(self.job.destination).exists():
            return {}
        # Unknown/missing S3 bucket errors are fatal, never interpreted as empty.
        filters = ["--exclude-from", str(self.job.exclude_files)] if self.job.exclude_files else []
        hashes = ["--hash"] if self.job.verification_mode == "hash" else []
        raw = self.command("lsjson", self.job.destination, "--recursive", "--files-only", *hashes,
                           *NAMESPACE_EXCLUDE, *filters)
        try:
            items = json.loads(raw)
            if not isinstance(items, list) or any(not isinstance(i, dict)
                    or type(i.get("Size")) is not int or i["Size"] < 0
                    or not isinstance(i.get("Hashes", {}), dict) for i in items):
                raise ValueError("metadados")
            cache = {}
            return {safe_relative(i["Path"]): i for i in items
                    if not i["Path"].startswith(NAMESPACE + "/") and
                    (self.job.type != "local" or not path_has_symlink(self.job.destination, i["Path"], cache))}
        except (ValueError, KeyError, TypeError) as exc:
            raise BackupError("Inventário rclone inválido") from exc

    def put(self, source, key, *, standard=False, protected_mtime_ns=None):
        if self.job.type == "local":
            check_mounts(self.job, self.runner, check_source=self.source_required)
            check_disks(self.job, config=self.config, temporary_path=self.temporary_directory,
                        check_source=self.source_required)
            prepare_write(self.job.destination, key)
        protection = ["--update"] if protected_mtime_ns is not None else []
        comparison = self.comparison_flags() if protected_mtime_ns is not None else ["--checksum"]
        self.command("copyto", source, self.path(key), *comparison, "-v", "--use-json-log", *protection,
                     retry=True, standard=standard)
        report = getattr(self.runner, "last_stderr", None)
        if not isinstance(report, str):
            return True
        # Successful copyto can also skip an identical orphan from an earlier
        # interrupted run. Audit the tool's actual successful copy events.
        for line in report.splitlines():
            try:
                if json.loads(line).get("msg", "").startswith("Copied"):
                    return True
            except (ValueError, AttributeError):
                continue
        if protected_mtime_ns is not None:
            try:
                destination = json.loads(self.command("lsjson", self.path(key), "--stat"))
                modified = timestamp_ns(destination.get("ModTime"))
            except (ValueError, TypeError, AttributeError):
                raise BackupError("Não foi possível confirmar a data do destino após a cópia") from None
            if modified > protected_mtime_ns:
                raise BackupError(f"Arquivo mais novo preservado no destino durante a cópia: {key}")
        return False

    def comparison_flags(self):
        if self.job.verification_mode == "simple":
            return ["--ignore-checksum", "--modify-window", "1ns"]
        return ["--checksum"]

    def get(self, key, target):
        """Download an exact object to an application-owned staging directory."""
        target = Path(target)
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.command("copyto", self.path(key), target, "--checksum", retry=True, standard=True,
                     source_required=False)

    def stat(self, key, *, hashes=True):
        # S3's hash comes from object metadata/ETag; never download the body.
        try:
            item = json.loads(self.command("lsjson", self.path(key), "--stat", *(["--hash"] if hashes else [])))
            if (not isinstance(item, dict) or type(item.get("Size")) is not int or item["Size"] < 0
                    or not isinstance(item.get("Hashes", {}), dict)):
                raise ValueError("metadados")
            return item
        except (ValueError, KeyError, TypeError) as exc:
            raise BackupError("Metadados do destino inválidos") from exc


    def delete(self, key):
        # An exact raw file list avoids glob interpretation and makes cleanup
        # idempotent if the object was deleted before a crash/state commit.
        if self.job.type == "local" and path_has_symlink(self.job.destination, key):
            # Expired history no longer conflicts with current backup data.
            # Ignore its link rather than deleting it or following its target.
            return
        self.path(key)
        self.command("delete", self.job.destination, "--files-from-raw", "-",
                     retry=True, input=safe_relative(key) + "\n")

    def sync_simple(self, root, backup_dir, *, max_delete, dry_run=False):
        return self._simple.sync_simple(root, backup_dir, max_delete=max_delete, dry_run=dry_run)

    def expire_simple_history(self, now, retention_days, *, dry_run=False):
        return self._simple.expire_simple_history(now, retention_days, dry_run=dry_run)

    def check_simple(self):
        return self._simple.check_simple()

    def restore_simple(self, output, selectors=(), *, dry_run=False):
        return self._simple.restore_simple(output, selectors, dry_run=dry_run)
