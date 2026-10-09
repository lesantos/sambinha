"""Use rclone's own exclude-from matcher for source and historical paths."""
import json
from pathlib import Path, PurePosixPath
import tempfile
from contextlib import contextmanager
from dataclasses import replace

from .errors import BackupError
from .inventory import safe_relative


@contextmanager
def frozen_rules(config):
    """Keep the same patterns for source, destination and retention decisions."""
    if not any(job.exclude_files for job in config.jobs):
        yield config
        return
    with tempfile.TemporaryDirectory(prefix="sambinha-rules-") as directory:
        jobs = []
        for index, job in enumerate(config.jobs):
            if not job.exclude_files:
                jobs.append(job)
                continue
            try:
                content = job.exclude_files.read_bytes()
                content.decode("utf-8")
            except (OSError, UnicodeError):
                raise BackupError(f"Arquivo exclude_files ausente ou ilegível: {job.name}") from None
            snapshot = Path(directory) / f"{index}.txt"
            snapshot.write_bytes(content)
            jobs.append(replace(job, exclude_files=snapshot))
        yield replace(config, jobs=tuple(jobs))


def retired_paths(entry, directory):
    if "files" in entry:
        return [safe_relative(p) for p in entry["files"]]
    if entry.get("control"):
        control = json.loads((directory / "controls" / safe_relative(entry["control"])).read_text())
        return [safe_relative(item["path"]) for item in control["files"]]
    parts = entry["key"].split("/", 4)
    return [safe_relative(parts[4] if len(parts) == 5 and parts[0] == ".sambinha" and parts[1] == "history" else entry["key"])]


def path_groups(paths):
    """Separate old file/directory replacements so both can be matched exactly."""
    groups = []
    for value in sorted({safe_relative(p) for p in paths}, key=lambda p: (len(PurePosixPath(p).parts), p)):
        parts = PurePosixPath(value).parts
        ancestors = {"/".join(parts[:i]) for i in range(1, len(parts))}
        for group in groups:
            if not ancestors.intersection(group):
                group.add(value)
                break
        else:
            groups.append({value})
    return groups


class Selection:
    def __init__(self, exclude_file, config, runner):
        self.exclude_file, self.config, self.runner = exclude_file, config, runner
        self._included = {}

    def listing(self, root, files_only=False):
        arguments = [self.config.rclone, "lsjson", str(root), "--recursive",
                     "--retries", "1", "--low-level-retries", "1"]
        if files_only:
            arguments.append("--files-only")
        if self.exclude_file:
            arguments += ["--exclude-from", str(self.exclude_file)]
        if self.config.rclone_config:
            arguments += ["--config", self.config.rclone_config]
        try:
            records = json.loads(self.runner.run(arguments))
            for item in records:
                safe_relative(item["Path"])
            return records
        except (ValueError, KeyError, TypeError) as exc:
            raise BackupError("Inventário filtrado inválido") from exc

    def source(self, root):
        if not self.exclude_file:
            return None, None
        records = self.listing(root)
        return ({item["Path"] for item in records if not item.get("IsDir", False)},
                {item["Path"] for item in records if item.get("IsDir", False)})

    def include_paths(self, paths):
        paths = set(paths)
        if not self.exclude_file or not paths:
            return paths
        cache_key = frozenset(paths)
        if cache_key in self._included:
            return set(self._included[cache_key])
        included = set()
        # Zero-byte local names let rclone classify paths no longer present in
        # the source, using identical glob semantics and no remote downloads.
        for group in path_groups(paths):
            with tempfile.TemporaryDirectory(prefix="sambinha-filter-") as directory:
                root = Path(directory)
                for name in group:
                    target = root / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.touch()
                included.update(item["Path"] for item in self.listing(root, files_only=True))
        self._included[cache_key] = frozenset(included)
        return included
