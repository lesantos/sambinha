"""rclone storage adapter shared by local paths and configured Amazon S3 remotes."""
import json
from pathlib import Path

from .config import Config, Job
from .errors import BackupError
from .inventory import safe_relative, path_has_symlink
from .protection import timestamp_ns
from .mounts import check_mounts
from .disks import check_disks
from .local_paths import prepare_write
from .simple_storage import SimpleStorageMixin
from . import rclone


class Storage(SimpleStorageMixin):
    def __init__(self, job: Job, config: Config, runner, log=None, *, temporary_directory=None, source_required=True):
        self.job, self.config, self.runner = job, config, runner
        self.log = log
        self.temporary_directory = temporary_directory
        self.source_required = source_required

    def path(self, key):
        if self.job.type == "local" and path_has_symlink(self.job.destination, key):
            raise BackupError(f"Caminho do destino mudou para link simbólico: {key}")
        return self.job.destination.rstrip("/") + "/" + safe_relative(key)

    def command(self, operation, *args, retry=False, standard=False, input=None, progress_directory=None,
                on_output=None, source_required=None):
        source_required = self.source_required if source_required is None else source_required
        if operation in {"copyto", "copy", "sync", "delete", "purge"}:
            check_mounts(self.job, self.runner, check_source=source_required)
            check_disks(self.job, temporary_path=self.temporary_directory, check_source=source_required)
        return rclone.command(self.config, self.job, self.runner, operation, *args,
                              retry=retry, standard=standard, input=input,
                              progress_directory=progress_directory, on_output=on_output)

    def inventory(self):
        if self.job.type == "local" and not Path(self.job.destination).exists():
            return {}
        # Unknown/missing S3 bucket errors are fatal, never interpreted as empty.
        filters = ["--exclude-from", str(self.job.exclude_files)] if self.job.exclude_files else []
        hashes = ["--hash"] if self.config.verification_mode == "hash" else []
        raw = self.command("lsjson", self.job.destination, "--recursive", "--files-only", *hashes,
                           "--exclude", "/.sambinha/**", *filters)
        try:
            items = json.loads(raw)
            if not isinstance(items, list) or any(not isinstance(i, dict)
                    or type(i.get("Size")) is not int or i["Size"] < 0
                    or not isinstance(i.get("Hashes", {}), dict) for i in items):
                raise ValueError("metadados")
            cache = {}
            return {safe_relative(i["Path"]): i for i in items
                    if not i["Path"].startswith(".sambinha/") and
                    (self.job.type != "local" or not path_has_symlink(self.job.destination, i["Path"], cache))}
        except (ValueError, KeyError, TypeError) as exc:
            raise BackupError("Inventário rclone inválido") from exc

    def put(self, source, key, *, standard=False, protected_mtime_ns=None):
        if self.job.type == "local":
            check_mounts(self.job, self.runner, check_source=self.source_required)
            check_disks(self.job, temporary_path=self.temporary_directory, check_source=self.source_required)
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
        if self.config.verification_mode == "simple":
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
