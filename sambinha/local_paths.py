"""Replace conflicting destination links without touching their targets."""
from contextlib import contextmanager
import os
from pathlib import Path, PurePosixPath
import stat

from .errors import BackupError
from .inventory import safe_relative
from .names import NAMESPACE

DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def child_directory(parent, name, *, create=False):
    try:
        return os.open(name, DIRECTORY_FLAGS, dir_fd=parent)
    except FileNotFoundError:
        if not create:
            raise
        try:
            os.mkdir(name, dir_fd=parent)
        except FileExistsError:
            pass
        return os.open(name, DIRECTORY_FLAGS, dir_fd=parent)


@contextmanager
def directory(path, *, create=False, missing_ok=False):
    """Open every root component without following links, including ancestors."""
    fd = os.open("/", DIRECTORY_FLAGS)
    try:
        for part in Path(path).absolute().parts[1:]:
            try:
                next_fd = child_directory(fd, part, create=create)
            except FileNotFoundError:
                if not missing_ok:
                    raise
                yield None
                return
            os.close(fd)
            fd = next_fd
        yield fd
    except OSError as exc:
        raise BackupError(f"Diretório local inseguro ou inacessível: {path}: {exc}") from exc
    finally:
        os.close(fd)


def prepare_write(root, relative, *, is_directory=False):
    """Unlink conflicting links below the job root and create real parents."""
    parts = PurePosixPath(safe_relative(relative)).parts
    replaced = []
    with directory(root, create=True) as root_fd:
        fd = os.dup(root_fd)
        try:
            for index, name in enumerate(parts):
                parent = index < len(parts) - 1 or is_directory
                try:
                    info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                except FileNotFoundError:
                    info = None
                if info and stat.S_ISLNK(info.st_mode):
                    if parts[0] == NAMESPACE:
                        raise BackupError(f"Metadados do destino são link simbólico: {relative}")
                    os.unlink(name, dir_fd=fd)
                    replaced.append("/".join(parts[:index + 1]))
                if parent:
                    next_fd = child_directory(fd, name, create=True)
                    os.close(fd)
                    fd = next_fd
        except OSError as exc:
            raise BackupError(f"Não foi possível preparar o destino: {relative}: {exc}") from exc
        finally:
            os.close(fd)
    return replaced


def conflicting_links(source, destination):
    """Inspect destination entries only in real directories shared with source."""
    files, directories = set(), set()

    def visit(source_fd, destination_fd, prefix):
        with os.scandir(destination_fd) as entries:
            for entry in entries:
                if not prefix and entry.name == NAMESPACE:
                    continue
                linked = entry.is_symlink()
                if not linked and not entry.is_dir(follow_symlinks=False):
                    continue
                try:
                    info = os.stat(entry.name, dir_fd=source_fd, follow_symlinks=False)
                except FileNotFoundError:
                    continue
                relative = safe_relative(prefix + entry.name)
                if linked:
                    if stat.S_ISREG(info.st_mode):
                        files.add(relative)
                    elif stat.S_ISDIR(info.st_mode):
                        directories.add(relative)
                elif stat.S_ISDIR(info.st_mode):
                    src = child_directory(source_fd, entry.name)
                    try:
                        dst = child_directory(destination_fd, entry.name)
                        try:
                            visit(src, dst, relative + "/")
                        finally:
                            os.close(dst)
                    finally:
                        os.close(src)

    with directory(source) as source_fd, directory(destination, missing_ok=True) as destination_fd:
        if destination_fd is not None:
            visit(source_fd, destination_fd, "")
    return files, directories
