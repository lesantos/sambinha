"""Native tree sync, history, verification and restoration for Storage."""
from datetime import datetime, timezone
import json
from pathlib import Path
import re

from .disks import check_disks
from .errors import BackupError
from .filesystems import metadata_flags, needs_destination_link_scan
from .filtering import Selection
from .inventory import safe_relative
from .local_paths import conflicting_links, directory, prepare_write
from .mounts import check_mounts


class SimpleStorageMixin:
    """Use the adapter's job/config/runner/log and path/command contract."""

    def sync_simple(self, root, backup_dir, *, max_delete, dry_run=False):
        """Delegate synchronization, conflict replacement and history to rclone."""
        filters = ["--exclude", "/.sambinha/**"]
        if self.job.exclude_files:
            filters += ["--exclude-from", str(self.job.exclude_files)]
        if dry_run:
            filters.append("--dry-run")
        if self.job.type == "local":
            check_mounts(self.job, self.runner)
            check_disks(self.job)
            with directory(root), directory(self.job.destination, create=not dry_run, missing_ok=dry_run):
                pass
            # Rclone can follow destination directory links when creating
            # parents. Check shared destination directories before sync;
            # inspect source metadata only for directories and found links.
            files, directories = (conflicting_links(root, self.job.destination)
                                  if needs_destination_link_scan(self.job.destination) else (set(), set()))
            selection = Selection(self.job.exclude_files, self.config, self.runner)
            files = selection.include_paths(files)
            directories = selection.include_directories(directories)
            for path in sorted(files | directories):
                if not dry_run:
                    prepare_write(self.job.destination, path, is_directory=path in directories)
                if self.log:
                    self.log.event("would_replace_link" if dry_run else "replace_link",
                                   job=self.job.name, path=path)
        detailed = (self.config.log_file_level == "detailed" or
                    self.config.log_terminal_level == "detailed")
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
            if self.log:
                details = {"job": self.job.name, "level": record.get("level", ""),
                           "message": self.runner.safe_error(message)}
                if isinstance(record.get("object"), str):
                    details["object"] = self.runner.safe_error(record["object"])
                self.log.event("rclone", **details)

        self.command("sync", root, self.job.destination, "--backup-dir", self.path(backup_dir),
                     *self.comparison_flags(), "--update", "--transfers", str(self.config.transfer_workers),
                     "--max-delete", str(max_delete), "--delete-before",
                     "--create-empty-src-dirs",
                     *metadata_flags(self.job.destination, remote=self.job.type == "s3"), *filters,
                     # A whole-sync retry would apply --max-delete again and
                     # could multiply the per-run deletion cap.
                     "--log-level", "DEBUG" if detailed else "ERROR", "--use-json-log",
                     progress_directory=str(root), on_output=on_output if detailed else None)
        return sorted(newer)


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
            self.command("copy", self.job.destination, output, "--create-empty-src-dirs",
                         *metadata_flags(output),
                         *filters, retry=True, source_required=False, progress_directory=str(self.job.destination))
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
                self.command("copy", self.path(selector), target, "--create-empty-src-dirs",
                             *metadata_flags(target),
                             *filters, retry=True, source_required=False, progress_directory=self.path(selector))
            else:
                # copyto addresses one exact file; rclone rejects directory
                # filters on that command. Validate exclusions before calling it.
                if self.job.exclude_files:
                    selected = Selection(self.job.exclude_files, self.config, self.runner)
                    if selector not in selected.include_paths({selector}):
                        raise BackupError(f"Caminho solicitado está excluído: {selector}")
                self.command("copyto", self.path(selector), target, *metadata_flags(target),
                             retry=True, source_required=False, progress_directory=str(Path(output) / Path(selector).parent))
