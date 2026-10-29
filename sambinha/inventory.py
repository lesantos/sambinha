"""Stable source inventories with strong fingerprints and race detection."""
import datetime
import errno
import hashlib
import json
import os
import queue
import re
import stat
import threading
import zlib
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath

from .errors import BackupError, SourceChangedError
from .filesystems import volatile_inode_devices
from .names import NAMESPACE

# O_PATH (Linux) pins a file's identity without opening it for reading, so
# metadata-only inventories never block on FIFOs or trigger reads. macOS has
# no O_PATH; a non-blocking read-only descriptor is the closest equivalent.
_METADATA_FLAGS = getattr(os, "O_PATH", os.O_RDONLY | os.O_NONBLOCK)


@dataclass(frozen=True)
class File:
    path: str
    size: int
    sha256: str
    md5: str
    mtime_ns: int
    mode: int
    # In-memory only (never persisted, never compared): CRC32 of the bytes the inventory hashed,
    # cross-checked against the one 7-Zip reports for the same member.
    crc32: int | None = field(default=None, compare=False, repr=False)

    def metadata(self):
        data = asdict(self)
        del data["crc32"]
        return data


def safe_relative(value: str) -> str:
    if not isinstance(value, str):
        raise BackupError("Caminho relativo deve ser uma string")
    p = PurePosixPath(value)
    if not p.parts or p.is_absolute() or ".." in p.parts or value != p.as_posix() or any(c in value for c in "\n\r\x00"):
        raise BackupError(f"Caminho relativo inválido: {value!r}")
    return value


def _observation(info):
    return {"dev": info.st_dev, "ino": info.st_ino, "type": stat.S_IFMT(info.st_mode),
            "size": info.st_size, "mtime_ns": info.st_mtime_ns,
            "ctime_ns": info.st_ctime_ns, "mode": stat.S_IMODE(info.st_mode)}


def _identity(info):
    return info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode)


def _same_observation(before, after, *, historical=False):
    old, new = _observation(before), _observation(after)
    if old == new:
        return True
    if not historical or old["ino"] == new["ino"]:
        return False
    # Never relax device, type, size, timestamps or mode; persistent hash-cache
    # signatures also remain strict. Only unpinned historical inode IDs vary.
    old.pop("ino")
    new.pop("ino")
    return old == new and before.st_dev in volatile_inode_devices()


def _changed(kind, relative, stage, before, after):
    details = json.dumps({"stage": stage, "before": _observation(before),
                          "after": _observation(after)}, sort_keys=True)
    raise SourceChangedError(f"{kind} mudou durante leitura: {relative}; {details}")


@contextmanager
def _source_file(root, relative, dir_fd=None, before=None, *, metadata_only=False, check_changes=True):
    """Pin a no-follow file until descriptor and pathname validation finish."""
    safe_relative(relative)
    if dir_fd is None and path_has_symlink(root, relative):
        raise BackupError(f"Origem mudou para link simbólico: {relative}")
    path = root / relative if dir_fd is None else relative.rsplit("/", 1)[-1]
    before = before if before is not None else os.stat(path, dir_fd=dir_fd, follow_symlinks=False)
    if not stat.S_ISREG(before.st_mode):
        raise BackupError(f"Apenas arquivos regulares são aceitos: {relative}")
    flags = _METADATA_FLAGS if metadata_only else os.O_RDONLY | os.O_NONBLOCK
    fd = os.open(path, flags | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=dir_fd)
    try:
        opened = os.fstat(fd)
        if check_changes and not _same_observation(before, opened, historical=True):
            _changed("Arquivo", relative, "open", before, opened)
        yield fd, opened
        after = os.fstat(fd)
        if check_changes and not _same_observation(opened, after):
            _changed("Arquivo", relative, "descriptor", opened, after)
        current = os.stat(path, dir_fd=dir_fd, follow_symlinks=False)
        if check_changes and not _same_observation(after, current):
            _changed("Arquivo", relative, "path", after, current)
    finally:
        os.close(fd)


ALL_DIGESTS = frozenset({"sha256", "md5", "crc32"})
BLOCK_SIZE = 1024 * 1024
# Below this size a helper thread costs more than the overlapped md5 saves.
THREADED_MD5_MIN = 4 * BLOCK_SIZE


def fingerprint(root: Path, relative: str, *, progress=None, dir_fd=None, before=None, file_fd=None,
                digests=ALL_DIGESTS) -> File:
    """Hash a file; ``digests`` selects among sha256, md5 and crc32 (unrequested ones stay empty)."""
    if file_fd is None:
        with _source_file(root, relative, dir_fd, before) as (fd, opened):
            return _fingerprint(relative, fd, opened, progress, digests)
    return _fingerprint(relative, file_fd, before, progress, digests)


def _md5_consumer(blocks, md5, failure):
    """Feed queued blocks to md5 until the None sentinel; keep draining after an error."""
    while (block := blocks.get()) is not None:
        if not failure:
            try:
                md5.update(block)
            except BaseException as exc:
                failure.append(exc)


def _fingerprint(relative, file_fd, before, progress, digests=ALL_DIGESTS):
    sha = hashlib.sha256() if "sha256" in digests else None
    md5 = hashlib.md5(usedforsecurity=False) if "md5" in digests else None
    crc = 0 if "crc32" in digests else None
    # md5 runs in a second thread (hashlib drops the GIL for large updates) only when
    # another digest shares the pass and the file is big enough to repay the thread.
    threaded = md5 is not None and (sha is not None or crc is not None) and before.st_size >= THREADED_MD5_MIN
    blocks, failure, consumer = queue.Queue(maxsize=4), [], None
    if threaded:
        consumer = threading.Thread(target=_md5_consumer, args=(blocks, md5, failure),
                                    name="sambinha-md5", daemon=True)
        consumer.start()
    # Small files never need a full block; a growing file is caught by the race checks.
    size = BLOCK_SIZE if before.st_size >= BLOCK_SIZE else before.st_size + 1
    try:
        while chunk := os.read(file_fd, size):
            if threaded:
                blocks.put(chunk)
            elif md5 is not None:
                md5.update(chunk)
            if sha is not None:
                sha.update(chunk)
            if crc is not None:
                crc = zlib.crc32(chunk, crc)
            if progress:
                progress(len(chunk))
            size = BLOCK_SIZE
    finally:
        if consumer is not None:
            # The consumer always drains, so the sentinel and the join cannot block for long.
            blocks.put(None)
            consumer.join()
    if failure:
        raise failure[0]
    return File(relative, before.st_size, sha.hexdigest() if sha else "", md5.hexdigest() if md5 else "",
                before.st_mtime_ns, stat.S_IMODE(before.st_mode), crc)


def metadata(root: Path, relative: str, *, dir_fd=None, before=None, file_fd=None) -> File:
    if file_fd is None:
        with _source_file(root, relative, dir_fd, before, metadata_only=True) as (_fd, opened):
            return _metadata(relative, opened)
    return _metadata(relative, before)


def _metadata(relative, info):
    return File(relative, info.st_size, "", "", info.st_mtime_ns, stat.S_IMODE(info.st_mode))


def same_metadata(a, b):
    return a.size == b.size and a.mtime_ns == b.mtime_ns


def signature(info):
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns,
            stat.S_IMODE(info.st_mode)]


_MODTIME = re.compile(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d{1,9}))?(Z|[+-]\d\d:\d\d)")


def listing_mtime_ns(text) -> int:
    """Nanoseconds since the epoch from an rclone ``ModTime`` (RFC 3339, up to 9 fractional digits)."""
    match = _MODTIME.fullmatch(text) if isinstance(text, str) else None
    if not match:
        raise ValueError("ModTime inválido")
    base = datetime.datetime.fromisoformat(match[1] + ("+00:00" if match[3] == "Z" else match[3]))
    return int(base.timestamp()) * 1_000_000_000 + int((match[2] or "0").ljust(9, "0"))


# Mode recorded for files inventoried from the listing, which carries none: it is only used to
# restore permissions, and a job can override it at restore time with ``file_mode``.
LISTING_FILE_MODE = 0o755


def _estimate(done, expected):
    """Files counted so far against the last inventory's count, held below 100% until the walk ends."""
    return min(done, int(expected * 0.99)) if expected else 0


def scan_from_listing(root: Path, listing, *, selected_dirs_out=None, progress=None, expected=None) -> list[File]:
    """Inventory built only from discovery records (path, size, date): no ``stat`` per file.

    Same traversal and order as ``scan`` (entries of a directory sorted by date and name, in
    place), so batches are planned identically. Links and special files are absent from the
    listing, which rclone produces with its link-skipping flags.
    """
    from .local_paths import directory as open_directory

    result = []
    visited = 0

    def report(current):
        if progress:
            progress.update("Inventário simples", root / current, _estimate(len(result), expected),
                            expected, activity=f"diretórios: {visited} | arquivos: {len(result)}",
                            indeterminate=not expected)

    def walk(prefix):
        nonlocal visited
        report(prefix)
        entries = []
        for record in listing(prefix):
            relative = safe_relative(record["Path"])
            if PurePosixPath(relative).parent.as_posix() != (prefix or "."):
                raise BackupError(f"Caminho fora do diretório consultado: {relative}")
            entries.append((record["ModTimeNs"], relative.rsplit("/", 1)[-1], record["IsDir"], record["Size"], relative))
        entries.sort(key=lambda entry: (entry[0], entry[1]))
        visited += 1
        initial_count = len(result)
        for mtime_ns, name, is_dir, size, relative in entries:
            try:
                name.encode("utf-8")
            except UnicodeEncodeError:
                raise BackupError(f"Nome de arquivo não é UTF-8 válido: {relative!r}") from None
            if is_dir:
                walk(relative)
            else:
                if relative.split("/")[0] == NAMESPACE:
                    raise BackupError(".sambinha é reservado para metadados")
                result.append(File(relative, size, "", "", mtime_ns, LISTING_FILE_MODE))
        if prefix and selected_dirs_out is not None and len(result) > initial_count:
            selected_dirs_out.add(prefix)

    with open_directory(root):
        walk("")
    if progress:
        progress.update("Inventário simples", root, 1, 1)
    return result


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
         mode="hash", progress=None, listing=None, selected_dirs_out=None,
         verify_source_changes=True, expected=None) -> list[File]:
    """Discover and inspect each directory once, in mtime/name DFS order.

    A filtered listing supplies only direct child names from rclone. Metadata
    and content are read relative to open real directories, never via source
    symlinks or cached assumptions about ancestor paths.
    """
    # Lazy import: local_paths shares safe_relative with this module.
    from .local_paths import DIRECTORY_FLAGS
    from .local_paths import directory as open_directory

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
            progress.update(current_stage, current_directory, _estimate(len(result), expected), expected,
                            activity=activity, indeterminate=not expected)

    def inspect(fd, relative, before):
        report(stage, directory)
        if mode == "simple" and not verify_source_changes:
            # Nothing is read and nothing is compared, so opening the file only to stat it
            # again (open, two fstat, stat, close) would cost several round trips per file
            # on a network mount for the same answer the listing already gave.
            if not stat.S_ISREG(before.st_mode):
                raise BackupError(f"Apenas arquivos regulares são aceitos: {relative}")
            item, opened = _metadata(relative, before), before
            attempts = 0
        else:
            attempts = 4 if verify_source_changes else 1
        for attempt in range(attempts):
            try:
                if verify_source_changes:
                    before = os.stat(relative.rsplit("/", 1)[-1], dir_fd=fd, follow_symlinks=False)
                    if not stat.S_ISREG(before.st_mode):
                        raise BackupError(f"Apenas arquivos regulares são aceitos: {relative}")
                with _source_file(root, relative, fd, before, metadata_only=mode == "simple",
                                  check_changes=verify_source_changes) as (file_fd, opened):
                    if mode == "simple":
                        item = metadata(root, relative, dir_fd=fd, before=opened, file_fd=file_fd)
                    else:
                        cached = (cache or {}).get(relative)
                        if (cached and cached["signature"] == signature(opened)
                                and cached["file"].get("sha256") and cached["file"].get("md5")):
                            item = File(**cached["file"])
                        else:
                            read = 0
                            def advance(size):
                                nonlocal read
                                read += size
                                report(stage, directory, read=read, size=opened.st_size)
                            item = fingerprint(root, relative, dir_fd=fd, before=opened, file_fd=file_fd,
                                               progress=advance if progress and progress.enabled else None)
                break
            except SourceChangedError:
                if not verify_source_changes or attempt + 1 == attempts:
                    raise
        result.append(item)
        if cache_out is not None:
            cache_out[relative] = {"signature": signature(opened), "file": item.metadata()}
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
            # Names that are not valid UTF-8 arrive as surrogate escapes, which
            # neither the JSON state nor the listfile can serialize; fail here
            # naming the file instead of crashing deep in the job.
            try:
                name.encode("utf-8")
            except UnicodeEncodeError:
                raise BackupError(f"Nome de arquivo não é UTF-8 válido: {relative!r}") from None
            if stat.S_ISDIR(before.st_mode):
                if included_dirs is not None and relative not in included_dirs:
                    continue
                safe_relative(relative)
                child = os.open(name, DIRECTORY_FLAGS, dir_fd=fd)
                try:
                    opened = os.fstat(child)
                    if verify_source_changes and _identity(before) != _identity(opened) and not _same_observation(
                            before, opened, historical=True):
                        _changed("Diretório", relative, "open", before, opened)
                    walk(child, relative)
                    after = os.stat(name, dir_fd=fd, follow_symlinks=False)
                    if verify_source_changes and _identity(opened) != _identity(after):
                        _changed("Diretório", relative, "path", opened, after)
                finally:
                    os.close(child)
            else:
                if relative.split("/")[0] == NAMESPACE:
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


def _source_tree(files):
    """Group inventory items by directory: node = (items, {child name: node})."""
    tree = ([], {})
    for item in files:
        node = tree
        *parents, _name = PurePosixPath(safe_relative(item.path)).parts
        for part in parents:
            node = node[1].setdefault(part, ([], {}))
        node[0].append(item)
    return tree


def _unchanged(item, info, signatures):
    """True when the scan's recorded stat signature still describes this exact item."""
    entry = signatures.get(item.path) if signatures else None
    return (entry is not None and stat.S_ISREG(info.st_mode) and entry["signature"] == signature(info)
            and entry["file"] == item.metadata())


def verify(root: Path, files: list[File], *, mode="hash", progress=None, verify_source_changes=True,
           signatures=None):
    """Re-check the inventory, walking real directories once through descriptors.

    Every directory is opened with O_NOFOLLOW relative to its parent, so no
    per-file ancestor lstat chain remains and a directory swapped for a symlink
    or another directory is still caught. ``signatures`` is the scan's
    ``cache_out`` (hash mode only): items whose stat signature is unchanged are
    not re-read; callers pass it only when the fingerprint cache is enabled and
    no full scan is due. Without it hash mode re-hashes every file.
    """
    if not verify_source_changes:
        return
    from .local_paths import DIRECTORY_FLAGS
    from .local_paths import directory as open_directory

    done = 0
    last = root

    def check(fd, item):
        nonlocal done, last
        last = (root / item.path).parent
        if progress:
            progress.update("Conferindo origem", last, done, len(files))
        done += 1
        name = item.path.rsplit("/", 1)[-1]
        for attempt in range(4):
            try:
                if mode == "hash" and signatures and _unchanged(
                        item, os.stat(name, dir_fd=fd, follow_symlinks=False), signatures):
                    return
                actual = (metadata(root, item.path, dir_fd=fd) if mode == "simple"
                          else fingerprint(root, item.path, dir_fd=fd))
                if (not same_metadata(actual, item) if mode == "simple" else actual != item):
                    raise SourceChangedError(f"Origem alterada durante backup: {item.path}; execute novamente")
                return
            except SourceChangedError:
                if attempt == 3:
                    raise

    def visit(fd, node, prefix):
        for item in node[0]:
            check(fd, item)
        for name, child_node in node[1].items():
            relative = prefix + "/" + name if prefix else name
            before = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if stat.S_ISLNK(before.st_mode):
                raise BackupError(f"Origem mudou para link simbólico: {relative}")
            try:
                child = os.open(name, DIRECTORY_FLAGS, dir_fd=fd)
            except NotADirectoryError:
                raise BackupError(f"Origem deixou de ser diretório: {relative}") from None
            except OSError as exc:
                if exc.errno != errno.ELOOP:
                    raise
                raise BackupError(f"Origem mudou para link simbólico: {relative}") from None
            try:
                opened = os.fstat(child)
                if _identity(before) != _identity(opened) and not _same_observation(before, opened, historical=True):
                    _changed("Diretório", relative, "open", before, opened)
                visit(child, child_node, relative)
                after = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if _identity(opened) != _identity(after):
                    _changed("Diretório", relative, "path", opened, after)
            finally:
                os.close(child)

    with open_directory(root) as root_fd:
        visit(root_fd, _source_tree(files), "")
    if progress:
        progress.update("Conferindo origem", last, 1, 1)
