"""rclone storage adapter shared by local paths and configured Amazon S3 remotes."""
import json
from pathlib import Path

from .config import Config, Job
from .errors import BackupError
from .inventory import safe_relative
from .protection import timestamp_ns
from .mounts import check_mounts
from .disks import check_disks


class Storage:
    def __init__(self, job: Job, config: Config, runner):
        self.job, self.config, self.runner = job, config, runner

    def path(self, key):
        return self.job.destination.rstrip("/") + "/" + safe_relative(key)

    def command(self, operation, *args, retry=False, standard=False, input=None, progress_directory=None):
        if operation in {"copyto", "copy", "delete"}:
            check_mounts(self.job, self.runner)
            check_disks(self.job)
        flags = ["--retries", "1", "--low-level-retries", "1"]
        if self.config.rclone_config:
            flags += ["--config", self.config.rclone_config]
        if self.job.type == "s3":
            # GLACIER_IR is cold storage with immediate access, retrieval fees,
            # minimum billed size 128 KiB and minimum duration 90 days. GLACIER
            # and DEEP_ARCHIVE need restore and bill at least 90/180 days.
            # STANDARD costs more per GB, without archival restore constraints.
            flags += ["--s3-storage-class", "STANDARD" if standard else self.job.storage_class]
        progress = {"progress_directory": progress_directory} if progress_directory is not None else {}
        return self.runner.run([self.config.rclone, operation, *map(str, args), *flags],
                               retry=retry, input=input, **progress)

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
            return {safe_relative(i["Path"]): i for i in items
                    if not i["Path"].startswith(".sambinha/")}
        except (ValueError, KeyError, TypeError) as exc:
            raise BackupError("Inventário rclone inválido") from exc

    def put(self, source, key, *, standard=False, protected_mtime_ns=None):
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

    def preserve(self, key, history_key):
        self.command("copyto", self.path(key), self.path(history_key), *self.comparison_flags(), retry=True)

    def comparison_flags(self):
        if self.config.verification_mode == "simple":
            return ["--ignore-checksum", "--modify-window", "1ns"]
        return ["--checksum"]

    def get(self, key, target):
        """Download an exact object to an application-owned staging directory."""
        target = Path(target)
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.command("copyto", self.path(key), target, "--checksum", retry=True, standard=True)

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

    def put_many(self, root, files):
        """One rclone process per bounded group, after histories are durable."""
        self.command("copy", root, self.job.destination, "--files-from-raw", "-",
                     *self.comparison_flags(), "--update", "--transfers", str(self.config.transfer_workers),
                     "-v", "--use-json-log", retry=True,
                     input="".join(safe_relative(f.path) + "\n" for f in files),
                     progress_directory=str(Path(root) / Path(files[0].path).parent))
        copied = set()
        report = getattr(self.runner, "last_stderr", "")
        for line in report.splitlines() if isinstance(report, str) else ():
            try:
                record = json.loads(line)
                if isinstance(record, dict) and isinstance(record.get("msg"), str) and record["msg"].startswith("Copied"):
                    copied.add(record["object"])
            except (ValueError, KeyError, TypeError):
                continue
        for item in files:
            if item.path not in copied:
                modified = timestamp_ns(self.stat(item.path, hashes=self.config.verification_mode == "hash").get("ModTime"))
                if modified > item.mtime_ns:
                    raise BackupError(f"Arquivo mais novo preservado no destino durante a cópia: {item.path}")
        return copied

    def delete(self, key):
        # An exact raw file list avoids glob interpretation and makes cleanup
        # idempotent if the object was deleted before a crash/state commit.
        self.command("delete", self.job.destination, "--files-from-raw", "-",
                     retry=True, input=safe_relative(key) + "\n")
