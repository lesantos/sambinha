"""rclone storage adapter shared by local paths and configured Amazon S3 remotes."""
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from .config import Config, Job
from .errors import BackupError
from .inventory import safe_relative, path_has_symlink
from .filtering import SKIP_LINK_FLAGS
from .protection import timestamp_ns
from .mounts import check_mounts
from .disks import check_disks


class Storage:
    def __init__(self, job: Job, config: Config, runner):
        self.job, self.config, self.runner = job, config, runner

    def path(self, key):
        if self.job.type == "local" and path_has_symlink(self.job.destination, key):
            raise BackupError(f"Caminho do destino mudou para link simbólico: {key}")
        return self.job.destination.rstrip("/") + "/" + safe_relative(key)

    def command(self, operation, *args, retry=False, standard=False, input=None, progress_directory=None):
        if operation in {"copyto", "copy", "sync", "delete", "purge"}:
            check_mounts(self.job, self.runner)
            check_disks(self.job)
        flags = ["--retries", "1", "--low-level-retries", "1", *SKIP_LINK_FLAGS]
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
            cache = {}
            return {safe_relative(i["Path"]): i for i in items
                    if not i["Path"].startswith(".sambinha/") and
                    (self.job.type != "local" or not path_has_symlink(self.job.destination, i["Path"], cache))}
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

    def sync_simple(self, root, backup_dir, *, max_delete, dry_run=False):
        """Delegate synchronization, conflict replacement and history to rclone."""
        filters = ["--exclude", "/.sambinha/**"]
        if self.job.exclude_files:
            filters += ["--exclude-from", str(self.job.exclude_files)]
        if dry_run:
            filters.append("--dry-run")
        self.command("sync", root, self.job.destination, "--backup-dir", self.path(backup_dir),
                     *self.comparison_flags(), "--update", "--transfers", str(self.config.transfer_workers),
                     "--max-delete", str(max_delete), "--delete-before",
                     "--create-empty-src-dirs", "--metadata", *filters,
                     # A whole-sync retry would apply --max-delete again and
                     # could multiply the per-run deletion cap.
                     "-vv", "--use-json-log",
                     progress_directory=str(root))
        newer = []
        report = getattr(self.runner, "last_stderr", None)
        if isinstance(report, str):
            for line in report.splitlines():
                try:
                    record = json.loads(line)
                except (ValueError, TypeError):
                    continue
                message = record.get("msg", "") if isinstance(record, dict) else ""
                if ("Skipped copy as --update is in use" in message or
                        ("Destination is newer than source" in message and "skipping" in message)):
                    path = record.get("object")
                    if isinstance(path, str):
                        newer.append(safe_relative(path))
        return sorted(set(newer))

    def expire_simple_history(self, now, retention_days, *, dry_run=False):
        """Purge run-scoped --backup-dir trees after the conservative retention window."""
        history = ".sambinha/history"
        if self.job.type == "local" and not Path(self.path(history)).is_dir():
            return []
        raw = self.command("lsjson", self.path(history), "--dirs-only")
        try:
            entries = json.loads(raw)
            if not isinstance(entries, list):
                raise ValueError
        except (ValueError, TypeError):
            raise BackupError("Histórico rclone inválido") from None
        expired = []
        for entry in entries:
            name = entry.get("Path") if isinstance(entry, dict) else None
            if not isinstance(name, str) or not re.fullmatch(r"\d{8}T\d{6}-[0-9a-f]{32}", name):
                continue
            started = datetime.strptime(name[:15], "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc).timestamp()
            if started + retention_days * 86400 > now:
                continue
            expired.append(name)
            if not dry_run:
                self.command("purge", self.path(f"{history}/{name}"))
        return expired

    def check_simple(self):
        filters = ["--exclude", "/.sambinha/**"]
        if self.job.exclude_files:
            filters += ["--exclude-from", str(self.job.exclude_files)]
        self.command("check", self.job.source, self.job.destination, *filters)

    def restore_simple(self, output, selectors=(), *, dry_run=False):
        filters = ["--exclude", "/.sambinha/**"]
        if self.job.exclude_files:
            filters += ["--exclude-from", str(self.job.exclude_files)]
        if dry_run:
            filters.append("--dry-run")
        if not selectors:
            self.command("copy", self.job.destination, output, "--create-empty-src-dirs", "--metadata",
                         *filters, retry=True, progress_directory=str(self.job.destination))
            return
        chosen = []
        for selector in sorted({safe_relative(value.rstrip("/")) for value in selectors},
                               key=lambda value: (value.count("/"), value)):
            if selector == ".sambinha" or selector.startswith(".sambinha/"):
                raise BackupError("O caminho .sambinha é reservado e não pode ser restaurado como arquivo")
            if any(selector == parent or selector.startswith(parent + "/") for parent in chosen):
                continue
            try:
                item = json.loads(self.command("lsjson", self.path(selector), "--stat"))
            except BackupError:
                raise BackupError(f"Caminho solicitado não existe no backup: {selector}") from None
            if not isinstance(item, dict) or type(item.get("IsDir")) is not bool:
                raise BackupError(f"Metadados inválidos para caminho de restauração: {selector}")
            chosen.append(selector)
            target = str(Path(output) / selector)
            if item["IsDir"]:
                self.command("copy", self.path(selector), target, "--create-empty-src-dirs", "--metadata",
                             *filters, retry=True, progress_directory=self.path(selector))
            else:
                # copyto addresses one exact file; rclone rejects directory
                # filters on that command. Validate exclusions before calling it.
                if self.job.exclude_files:
                    from .filtering import Selection
                    selected = Selection(self.job.exclude_files, self.config, self.runner)
                    if selector not in selected.include_paths({selector}):
                        raise BackupError(f"Caminho solicitado está excluído: {selector}")
                self.command("copyto", self.path(selector), target, "--metadata",
                             retry=True, progress_directory=str(Path(output) / Path(selector).parent))

    def delete(self, key):
        # An exact raw file list avoids glob interpretation and makes cleanup
        # idempotent if the object was deleted before a crash/state commit.
        self.path(key)
        self.command("delete", self.job.destination, "--files-from-raw", "-",
                     retry=True, input=safe_relative(key) + "\n")
