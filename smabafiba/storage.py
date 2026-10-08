"""rclone storage adapter shared by local paths and configured Amazon S3 remotes."""
import json
from pathlib import Path

from .config import Config, Job
from .errors import BackupError
from .inventory import safe_relative


class Storage:
    def __init__(self, job: Job, config: Config, runner):
        self.job, self.config, self.runner = job, config, runner

    def path(self, key):
        return self.job.destination.rstrip("/") + "/" + safe_relative(key)

    def command(self, operation, *args, retry=False, standard=False, input=None):
        flags = ["--retries", "1", "--low-level-retries", "1"]
        if self.config.rclone_config:
            flags += ["--config", self.config.rclone_config]
        if self.job.type == "s3":
            # GLACIER_IR is cold storage with immediate access, retrieval fees,
            # minimum billed size 128 KiB and minimum duration 90 days. GLACIER
            # and DEEP_ARCHIVE need restore and bill at least 90/180 days.
            # STANDARD costs more per GB, without archival restore constraints.
            flags += ["--s3-storage-class", "STANDARD" if standard else self.job.storage_class]
        return self.runner.run([self.config.rclone, operation, *map(str, args), *flags], retry=retry, input=input)

    def inventory(self):
        if self.job.type == "local" and not Path(self.job.destination).exists():
            return {}
        # Unknown/missing S3 bucket errors are fatal, never interpreted as empty.
        raw = self.command("lsjson", self.job.destination, "--recursive", "--files-only", "--hash")
        try:
            items = json.loads(raw)
            return {safe_relative(i["Path"]): i for i in items
                    if not i["Path"].startswith(".smabafiba/")}
        except (ValueError, KeyError, TypeError) as exc:
            raise BackupError("Inventário rclone inválido") from exc

    def put(self, source, key, *, standard=False):
        self.command("copyto", source, self.path(key), "--checksum", "-v", "--use-json-log",
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
        return False

    def preserve(self, key, history_key):
        self.command("copyto", self.path(key), self.path(history_key), "--checksum", retry=True)

    def delete(self, key):
        # An exact raw file list avoids glob interpretation and makes cleanup
        # idempotent if the object was deleted before a crash/state commit.
        self.command("delete", self.job.destination, "--files-from-raw", "-",
                     retry=True, input=safe_relative(key) + "\n")
