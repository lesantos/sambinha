"""Stable source inventories with strong fingerprints and race detection."""
from dataclasses import asdict, dataclass
import hashlib
import os
from pathlib import Path, PurePosixPath
import stat

from .errors import BackupError


@dataclass(frozen=True)
class File:
    path: str
    size: int
    sha256: str
    md5: str
    mtime_ns: int
    mode: int

    def metadata(self):
        return asdict(self)


def safe_relative(value: str) -> str:
    if not isinstance(value, str):
        raise BackupError("Caminho relativo deve ser uma string")
    p = PurePosixPath(value)
    if not p.parts or p.is_absolute() or ".." in p.parts or value != p.as_posix() or any(c in value for c in "\n\r\x00"):
        raise BackupError(f"Caminho relativo inválido: {value!r}")
    return value


def _source_info(root, relative, dir_fd, before):
    safe_relative(relative)
    if dir_fd is None and path_has_symlink(root, relative):
        raise BackupError(f"Origem mudou para link simbólico: {relative}")
    path = root / relative if dir_fd is None else relative.rsplit("/", 1)[-1]
    before = before if before is not None else os.stat(path, dir_fd=dir_fd, follow_symlinks=False)
    if not stat.S_ISREG(before.st_mode):
        raise BackupError(f"Apenas arquivos regulares são aceitos: {relative}")
    return path, before


def fingerprint(root: Path, relative: str, *, progress=None, dir_fd=None, before=None) -> File:
    path, before = _source_info(root, relative, dir_fd, before)
    sha, md5 = hashlib.sha256(), hashlib.md5(usedforsecurity=False)
    # O_NOFOLLOW stops a symlink replacement between lstat and open.
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dir_fd)
    with os.fdopen(fd, "rb") as stream:
        opened = os.fstat(stream.fileno())
        if (before.st_ino, before.st_dev) != (opened.st_ino, opened.st_dev):
            raise BackupError(f"Arquivo mudou durante leitura: {relative}")
        while chunk := stream.read(64 * 1024):
            sha.update(chunk)
            md5.update(chunk)
            if progress:
                progress(len(chunk))
        after = os.fstat(stream.fileno())
    if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
        raise BackupError(f"Arquivo mudou durante leitura: {relative}")
    return File(relative, before.st_size, sha.hexdigest(), md5.hexdigest(),
                before.st_mtime_ns, stat.S_IMODE(before.st_mode))


def metadata(root: Path, relative: str, *, dir_fd=None, before=None) -> File:
    _, info = _source_info(root, relative, dir_fd, before)
    return File(relative, info.st_size, "", "", info.st_mtime_ns, stat.S_IMODE(info.st_mode))


def same_metadata(a, b):
    return a.size == b.size and a.mtime_ns == b.mtime_ns


def signature(info):
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns,
            stat.S_IMODE(info.st_mode)]


def path_has_symlink(root, relative, cache=None):
    """Inspect each component without descending through a symbolic link."""
    cache = {} if cache is None else cache
    path = Path(root)
    for part in ("", *PurePosixPath(safe_relative(relative)).parts):
        path = path / part
        if path not in cache:
            cache[path] = path.is_symlink()
        if cache[path]:
            return True
    return False


def scan(root: Path, included_files=None, included_dirs=None, *, cache=None, cache_out=None,
         mode="hash", progress=None, listing=None, selected_dirs_out=None) -> list[File]:
    """Discover and inspect each directory once, in mtime/name DFS order.

    A filtered listing supplies only direct child names from rclone. Metadata
    and content are read relative to open real directories, never via source
    symlinks or cached assumptions about ancestor paths.
    """
    # Lazy import: local_paths shares safe_relative with this module.
    from .local_paths import DIRECTORY_FLAGS, directory as open_directory

    result = []
    visited = 0
    directory = root
    stage = "Inventário simples" if mode == "simple" else "Calculando hashes"

    def report(current_stage, current_directory, *, read=None, size=None):
        if progress:
            activity = f"diretórios: {visited} | arquivos: {len(result)}"
            if read is not None:
                activity += f" | bytes: {read}/{size}"
            # The total is unknown until discovery ends. Do not manufacture a
            # percentage or perform an extra walk just to obtain a denominator.
            progress.update(current_stage, current_directory, activity=activity, indeterminate=True)

    def inspect(fd, relative, before):
        report(stage, directory)
        if mode == "simple":
            item = metadata(root, relative, dir_fd=fd, before=before)
        else:
            cached = (cache or {}).get(relative)
            if (cached and cached["signature"] == signature(before)
                    and cached["file"].get("sha256") and cached["file"].get("md5")):
                item = File(**cached["file"])
            else:
                read = 0
                def advance(size):
                    nonlocal read
                    read += size
                    report(stage, directory, read=read, size=before.st_size)
                item = fingerprint(root, relative, dir_fd=fd, before=before,
                                   progress=advance if progress and progress.enabled else None)
        after = os.stat(relative.rsplit("/", 1)[-1], dir_fd=fd, follow_symlinks=False)
        if signature(before) != signature(after):
            raise BackupError(f"Arquivo mudou durante leitura: {relative}")
        result.append(item)
        if cache_out is not None:
            cache_out[relative] = {"signature": signature(after), "file": item.metadata()}
        report(stage, directory)

    def walk(fd, prefix):
        nonlocal visited, directory
        directory = root / prefix
        report("Listando origem", directory)
        if listing is None:
            with os.scandir(fd) as stream:
                entries = [(entry.name, entry.stat(follow_symlinks=False)) for entry in stream]
        else:
            entries = []
            for record in listing(prefix):
                relative = safe_relative(record["Path"])
                if PurePosixPath(relative).parent.as_posix() != (prefix or "."):
                    raise BackupError(f"Caminho fora do diretório consultado: {relative}")
                name = relative.rsplit("/", 1)[-1]
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if not stat.S_ISLNK(info.st_mode) and stat.S_ISDIR(info.st_mode) != record["IsDir"]:
                    raise BackupError(f"Origem mudou durante listagem: {relative}")
                entries.append((name, info))
        entries.sort(key=lambda pair: (pair[1].st_mtime_ns, pair[0]))
        visited += 1
        initial_count = len(result)
        for name, before in entries:
            if stat.S_ISLNK(before.st_mode):
                continue
            relative = prefix + "/" + name if prefix else name
            if stat.S_ISDIR(before.st_mode):
                if included_dirs is not None and relative not in included_dirs:
                    continue
                safe_relative(relative)
                child = os.open(name, DIRECTORY_FLAGS, dir_fd=fd)
                try:
                    opened = os.fstat(child)
                    if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
                        raise BackupError(f"Diretório mudou durante leitura: {relative}")
                    walk(child, relative)
                    after = os.stat(name, dir_fd=fd, follow_symlinks=False)
                    if (before.st_dev, before.st_ino, stat.S_IFMT(before.st_mode)) != (
                            after.st_dev, after.st_ino, stat.S_IFMT(after.st_mode)):
                        raise BackupError(f"Diretório mudou durante leitura: {relative}")
                finally:
                    os.close(child)
            else:
                if relative.split("/")[0] == ".sambinha":
                    raise BackupError(".sambinha é reservado para metadados")
                if included_files is not None and relative not in included_files:
                    continue
                safe_relative(relative)
                directory = root / prefix
                inspect(fd, relative, before)
        if prefix and selected_dirs_out is not None and len(result) > initial_count:
            selected_dirs_out.add(prefix)

    with open_directory(root) as fd:
        walk(fd, "")
    if progress:
        progress.update(stage, directory, 1, 1)
    return result


def verify(root: Path, files: list[File], *, mode="hash", progress=None):
    for index, item in enumerate(files):
        if progress:
            progress.update("Conferindo origem", (root / item.path).parent, index, len(files))
        actual = metadata(root, item.path) if mode == "simple" else fingerprint(root, item.path)
        if (not same_metadata(actual, item) if mode == "simple" else actual != item):
            raise BackupError(f"Origem alterada durante backup: {item.path}; execute novamente")
    if progress:
        progress.update("Conferindo origem", (root / files[-1].path).parent if files else root, 1, 1)
