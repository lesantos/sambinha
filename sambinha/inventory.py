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


def fingerprint(root: Path, relative: str, *, progress=None) -> File:
    path = root / safe_relative(relative)
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise BackupError(f"Apenas arquivos regulares são aceitos: {relative}")
    sha, md5 = hashlib.sha256(), hashlib.md5(usedforsecurity=False)
    # O_NOFOLLOW stops a symlink replacement between lstat and open.
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
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


def metadata(root: Path, relative: str) -> File:
    path = root / safe_relative(relative)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise BackupError(f"Apenas arquivos regulares são aceitos: {relative}")
    return File(relative, info.st_size, "", "", info.st_mtime_ns, stat.S_IMODE(info.st_mode))


def same_metadata(a, b):
    return a.size == b.size and a.mtime_ns == b.mtime_ns


def signature(info):
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns,
            stat.S_IMODE(info.st_mode)]


def scan(root: Path, included_files=None, included_dirs=None, *, cache=None, cache_out=None,
         mode="hash", progress=None) -> list[File]:
    if not root.is_dir() or root.is_symlink():
        raise BackupError(f"Origem não é diretório regular: {root}")
    result, candidates = [], []
    visited, pending = 0, 1
    def fail(error):
        raise error
    for directory, dirs, files in os.walk(root, followlinks=False, onerror=fail):
        for name in dirs:
            if (Path(directory) / name).is_symlink():
                raise BackupError("Links simbólicos não são aceitos; origem permanece intacta")
        if included_dirs is not None:
            dirs[:] = [name for name in dirs if (Path(directory) / name).relative_to(root).as_posix() in included_dirs]
        visited += 1
        pending += len(dirs) - 1
        if progress:
            progress.update("Listando origem", directory, visited, visited + pending + 1)
        for name in files:
            relative = (Path(directory) / name).relative_to(root).as_posix()
            if (Path(directory) / name).is_symlink():
                raise BackupError("Links simbólicos não são aceitos; origem permanece intacta")
            if relative.split("/")[0] == ".sambinha":
                raise BackupError(".sambinha é reservado para metadados")
            if included_files is not None and relative not in included_files:
                continue
            before = (Path(directory) / name).lstat()
            if not stat.S_ISREG(before.st_mode):
                raise BackupError(f"Apenas arquivos regulares são aceitos: {relative}")
            candidates.append((relative, before))
    if progress:
        progress.update("Listando origem", root, 1, 1)
    stage = "Inventário simples" if mode == "simple" else "Calculando hashes"
    total = len(candidates) if mode == "simple" else sum(max(1, info.st_size) for _, info in candidates)
    completed = 0
    for relative, before in candidates:
        directory = (root / relative).parent
        if progress:
            progress.update(stage, directory, completed, total)
        if mode == "simple":
            item = metadata(root, relative)
        else:
            cached = (cache or {}).get(relative)
            if (cached and cached["signature"] == signature(before)
                    and cached["file"].get("sha256") and cached["file"].get("md5")):
                safe_relative(relative)
                item = File(**cached["file"])
            elif progress and progress.enabled:
                read = 0
                def advance(size):
                    nonlocal read
                    read += size
                    progress.update(stage, directory, completed + read, total)
                item = fingerprint(root, relative, progress=advance)
            else:
                item = fingerprint(root, relative)
        after = (root / relative).lstat()
        if signature(before) != signature(after):
            raise BackupError(f"Arquivo mudou durante leitura: {relative}")
        result.append(item)
        if cache_out is not None:
            cache_out[relative] = {"signature": signature(after), "file": item.metadata()}
        completed += 1 if mode == "simple" else max(1, item.size)
        if progress:
            progress.update(stage, directory, completed, total)
    if progress:
        progress.update(stage, root, 1, 1)
    return sorted(result, key=lambda f: f.path)


def verify(root: Path, files: list[File], *, mode="hash", progress=None):
    for index, item in enumerate(files):
        if progress:
            progress.update("Conferindo origem", (root / item.path).parent, index, len(files))
        actual = metadata(root, item.path) if mode == "simple" else fingerprint(root, item.path)
        if (not same_metadata(actual, item) if mode == "simple" else actual != item):
            raise BackupError(f"Origem alterada durante backup: {item.path}; execute novamente")
    if progress:
        progress.update("Conferindo origem", root, 1, 1)
