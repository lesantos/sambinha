"""Use rclone's own exclude-from matcher for source and historical paths."""
import json
import tempfile
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path, PurePosixPath

from .discovery import Discovery
from .errors import BackupError, job_errors
from .inventory import path_has_symlink, safe_relative
from .names import NAMESPACE

SKIP_LINK_FLAGS = ("--skip-links", "--links=false", "--copy-links=false")


@contextmanager
def frozen_rules(config, *, on_job_error=None):
    """Keep the same patterns for source, destination and retention decisions."""
    if not any(job.exclude_files for job in config.jobs):
        yield config
        return
    with tempfile.TemporaryDirectory(prefix="sambinha-rules-", dir=config.temp_dir) as directory:
        jobs = []
        for index, job in enumerate(config.jobs):
            if not job.exclude_files:
                jobs.append(job)
                continue
            with job_errors(job.name, on_job_error if job.continue_on_job_error else None):
                try:
                    content = job.exclude_files.read_bytes()
                    content.decode("utf-8")
                except (OSError, UnicodeError):
                    raise BackupError(f"Arquivo exclude_files ausente ou ilegível: {job.name}") from None
                snapshot = Path(directory) / f"{index}.txt"
                snapshot.write_bytes(content)
                jobs.append(replace(job, exclude_files=snapshot))
        yield replace(config, jobs=tuple(jobs))


def retired_paths(entry, directory, *, controls_directory=None):
    if "files" in entry:
        return [safe_relative(p) for p in entry["files"]]
    if entry.get("control"):
        root = directory / "controls" if controls_directory is None else controls_directory
        try:
            control = json.loads((root / safe_relative(entry["control"])).read_text())
        except FileNotFoundError:
            # Legacy entry whose mirror is gone: fall back to the key-derived path
            # rather than blocking every later run on a missing local file.
            control = None
        if control is not None:
            return [safe_relative(item["path"]) for item in control["files"]]
    parts = entry["key"].split("/", 4)
    return [safe_relative(parts[4] if len(parts) == 5 and parts[0] == NAMESPACE and parts[1] == "history" else entry["key"])]


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
    def __init__(self, exclude_file, config, runner, *, ignored_roots=()):
        self.exclude_file, self.config, self.runner = exclude_file, config, runner
        self.ignored_roots = tuple(ignored_roots)
        self._included = {}

    @contextmanager
    def source_listing(self, root):
        """Supply native filtered child names to the single inventory walk."""
        if not self.exclude_file:
            yield None
        else:
            with Discovery(self.config, self.runner, root, self.exclude_file) as discovery:
                yield discovery.listing

    def listing(self, root, files_only=False, *, recursive=True, apply_exclusions=True):
        arguments = [self.config.rclone, "lsjson", str(root),
                     "--retries", "1", "--low-level-retries", "1", *SKIP_LINK_FLAGS]
        if recursive:
            arguments.append("--recursive")
        if files_only:
            arguments.append("--files-only")
        if apply_exclusions and self.exclude_file:
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
        # One server, one bounded operation per directory. Rclone applies its
        # native rules directly with a stable source root, without dummy trees.
        queue = [""]
        included_files = set()
        visited = 0
        with Discovery(self.config, self.runner, root, self.exclude_file) as discovery:
            while queue:
                relative = queue.pop()
                if relative and path_has_symlink(root, relative):
                    continue
                progress = getattr(self.runner, "progress", None)
                if progress:
                    progress.update("Listando origem", Path(root) / relative,
                                    activity=f"diretórios: {visited} | arquivos: {len(included_files)}",
                                    indeterminate=True)
                for item in discovery.listing(relative):
                    if item["IsDir"]:
                        queue.append(item["Path"])
                    else:
                        included_files.add(item["Path"])
                visited += 1

        included_dirs = set()
        for path in included_files:
            parts = PurePosixPath(path).parts
            included_dirs.update("/".join(parts[:index]) for index in range(1, len(parts)))
        return included_files, included_dirs

    def include_paths(self, paths):
        paths = set(paths)
        cache = {}
        paths = {p for p in paths if not any(path_has_symlink(root, p, cache)
                                            for root in self.ignored_roots)}
        if not self.exclude_file or not paths:
            return paths
        cache_key = frozenset(paths)
        if cache_key in self._included:
            return set(self._included[cache_key])
        included = set()
        # Zero-byte local names let rclone classify paths no longer present in
        # the source, using identical glob semantics and no remote downloads.
        for group in path_groups(paths):
            with tempfile.TemporaryDirectory(prefix="sambinha-filter-", dir=self.config.temp_dir) as directory:
                root = Path(directory)
                for name in group:
                    target = root / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.touch()
                included.update(item["Path"] for item in self.listing(root, files_only=True))
        self._included[cache_key] = frozenset(included)
        return included

    def include_directories(self, paths):
        """Match empty source directories using rclone's native directory rules."""
        paths = {safe_relative(path) for path in paths}
        if not self.exclude_file or not paths:
            return paths
        with tempfile.TemporaryDirectory(prefix="sambinha-filter-dirs-", dir=self.config.temp_dir) as directory:
            root = Path(directory)
            for name in paths:
                (root / name).mkdir(parents=True, exist_ok=True)
            return paths.intersection(item["Path"] for item in self.listing(root) if item.get("IsDir"))
