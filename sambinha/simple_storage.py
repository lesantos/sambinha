"""Native tree sync, history, verification and restoration, composed over a storage adapter."""
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from .config import Config, Job
from .disks import check_disks
from .errors import BackupError
from .filesystems import metadata_flags, needs_destination_link_scan
from .filtering import Selection
from .inventory import safe_relative
from .local_paths import conflicting_links, directory, prepare_write
from .mounts import check_mounts
from .names import HISTORY, NAMESPACE, NAMESPACE_EXCLUDE
from .retention import DAY


class SimpleHost(Protocol):
    """The minimal adapter surface SimpleOperations needs (satisfied by Storage and test doubles)."""

    job: Job
    config: Config
    runner: Any
    log: Any

    def path(self, key: str) -> str: ...

    def command(self, operation: str, *args: Any, **options: Any) -> str: ...

    def comparison_flags(self) -> list[str]: ...


class SimpleOperations:
    """Tree sync, run history, native check and selective restore for ``logic = "simple"`` jobs.

    Composition instead of inheritance: the adapter is passed in, so every attribute it reads is
    declared by SimpleHost and validated by mypy rather than assumed by convention.
    """

    def __init__(self, host: SimpleHost):
        self.host = host

    def sync_simple(self, root, backup_dir, *, max_delete, dry_run=False):
        """Delegate synchronization, conflict replacement and history to rclone."""
        filters = [*NAMESPACE_EXCLUDE]
        if self.host.job.exclude_files:
            filters += ["--exclude-from", str(self.host.job.exclude_files)]
        if dry_run:
            filters.append("--dry-run")
        if self.host.job.type == "local":
            check_mounts(self.host.job, self.host.runner)
            check_disks(self.host.job, config=self.host.config)
            with directory(root), directory(self.host.job.destination, create=not dry_run, missing_ok=dry_run):
                pass
            # Rclone can follow destination directory links when creating
            # parents. Check shared destination directories before sync;
            # inspect source metadata only for directories and found links.
            files, directories = (conflicting_links(root, self.host.job.destination)
                                  if needs_destination_link_scan(self.host.job.destination) else (set(), set()))
            selection = Selection(self.host.job.exclude_files, self.host.config, self.host.runner)
            files = selection.include_paths(files)
            directories = selection.include_directories(directories)
            for path in sorted(files | directories):
                if not dry_run:
                    prepare_write(self.host.job.destination, path, is_directory=path in directories)
                if self.host.log:
                    self.host.log.event("would_replace_link" if dry_run else "replace_link",
                                   job=self.host.job.name, path=path)
        detailed = (self.host.job.log_file_level == "detailed" or
                    self.host.job.log_terminal_level == "detailed")
        newer = set()

        def on_output(record):
            message = record.get("msg", "")
            if not isinstance(message, str):
                return
            if ("Skipped copy as --update is in use" in message or
                    ("Destination is newer than source" in message and "skipping" in message)):
                path = record.get("object")
                if isinstance(path, str):
                    newer.add(safe_relative(path))
            if self.host.log:
                details = {"job": self.host.job.name, "level": record.get("level", ""),
                           "message": self.host.runner.safe_error(message)}
                if isinstance(record.get("object"), str):
                    details["object"] = self.host.runner.safe_error(record["object"])
                self.host.log.event("rclone", **details)

        self.host.command("sync", root, self.host.job.destination, "--backup-dir", self.host.path(backup_dir),
                     *self.host.comparison_flags(), "--update", "--transfers", str(self.host.job.transfer_workers),
                     "--max-delete", str(max_delete), "--delete-before",
                     "--create-empty-src-dirs",
                     *metadata_flags(self.host.job.destination, remote=self.host.job.type == "s3"), *filters,
                     # A whole-sync retry would apply --max-delete again and
                     # could multiply the per-run deletion cap.
                     "--log-level", "DEBUG" if detailed else "ERROR", "--use-json-log",
                     progress_directory=str(root), on_output=on_output if detailed else None)
        return sorted(newer)


    def expire_simple_history(self, now, retention_days, *, dry_run=False):
        """Purge run-scoped --backup-dir trees after the conservative retention window."""
        history = HISTORY
        if self.host.job.type == "local" and not Path(self.host.path(history)).is_dir():
            return []
        raw = self.host.command("lsjson", self.host.path(history), "--dirs-only")
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
            started = datetime.strptime(name[:15], "%Y%m%dT%H%M%S").replace(tzinfo=UTC).timestamp()
            if started + retention_days * DAY > now:
                continue
            expired.append(name)
            if not dry_run:
                self.host.command("purge", self.host.path(f"{history}/{name}"))
        return expired


    def check_simple(self):
        filters = [*NAMESPACE_EXCLUDE]
        if self.host.job.exclude_files:
            filters += ["--exclude-from", str(self.host.job.exclude_files)]
        self.host.command("check", self.host.job.source, self.host.job.destination, *filters)


    def restore_simple(self, output, selectors=(), *, dry_run=False):
        filters = [*NAMESPACE_EXCLUDE]
        if self.host.job.exclude_files:
            filters += ["--exclude-from", str(self.host.job.exclude_files)]
        if dry_run:
            filters.append("--dry-run")
        if not selectors:
            self.host.command("copy", self.host.job.destination, output, "--create-empty-src-dirs",
                         *metadata_flags(output),
                         *filters, retry=True, source_required=False, progress_directory=str(self.host.job.destination))
            return
        chosen = []
        for selector in sorted({safe_relative(value.rstrip("/")) for value in selectors},
                               key=lambda value: (value.count("/"), value)):
            if selector == NAMESPACE or selector.startswith(NAMESPACE + "/"):
                raise BackupError("O caminho .sambinha é reservado e não pode ser restaurado como arquivo")
            if any(selector == parent or selector.startswith(parent + "/") for parent in chosen):
                continue
            try:
                item = json.loads(self.host.command("lsjson", self.host.path(selector), "--stat"))
            except BackupError:
                raise BackupError(f"Caminho solicitado não existe no backup: {selector}") from None
            if not isinstance(item, dict) or type(item.get("IsDir")) is not bool:
                raise BackupError(f"Metadados inválidos para caminho de restauração: {selector}")
            chosen.append(selector)
            target = str(Path(output) / selector)
            if item["IsDir"]:
                self.host.command("copy", self.host.path(selector), target, "--create-empty-src-dirs",
                             *metadata_flags(target),
                             *filters, retry=True, source_required=False, progress_directory=self.host.path(selector))
            else:
                # copyto addresses one exact file; rclone rejects directory
                # filters on that command. Validate exclusions before calling it.
                if self.host.job.exclude_files:
                    selected = Selection(self.host.job.exclude_files, self.host.config, self.host.runner)
                    if selector not in selected.include_paths({selector}):
                        raise BackupError(f"Caminho solicitado está excluído: {selector}")
                self.host.command("copyto", self.host.path(selector), target, *metadata_flags(target),
                             retry=True, source_required=False, progress_directory=str(Path(output) / Path(selector).parent))
