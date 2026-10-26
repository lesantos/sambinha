"""Process-tagged temporary directories and removal of those a dead process left behind.

Every staging directory is named ``sambinha-<stage>-<pid>-<random>``. A killed
run (SIGKILL, power loss) skips its cleanup; the next run, already holding the
writer lock, removes such directories when their pid is gone and they are old.
"""
import os
import re
import shutil
import stat
import tempfile
import time
from pathlib import Path

MIN_AGE = 3600
_NAME = re.compile(r"sambinha-(?:[a-z]+-)+?([0-9]+)-")


def prefix(stage):
    """``tempfile`` prefix tagging a directory with this process's pid."""
    return f"sambinha-{stage}-{os.getpid()}-"


def alive(pid):
    """A pid we may not signal still exists; only ProcessLookupError proves it gone."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OverflowError:
        return False
    return True


def _size(directory):
    total = 0
    for root, _, names in os.walk(directory, followlinks=False):
        for name in names:
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                pass
    return total


def sweep(directory=None, *, now=None, min_age=MIN_AGE):
    """Remove real directories named for a dead pid and untouched for ``min_age``; returns (count, bytes).

    Symlinks (even to directories), other users' entries and anything not matching
    the naming scheme are left alone; ``shutil.rmtree`` never follows links inside.
    """
    directory = Path(tempfile.gettempdir() if directory is None else directory)
    now = time.time() if now is None else now
    count = freed = 0
    try:
        entries = list(os.scandir(directory))
    except OSError:
        return 0, 0
    for entry in entries:
        match = _NAME.match(entry.name)
        if not match:
            continue
        try:
            info = entry.stat(follow_symlinks=False)
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
                    or now - info.st_mtime <= min_age or alive(int(match[1]))):
                continue
            size = _size(entry.path)
            shutil.rmtree(entry.path)
        except OSError:
            continue
        count += 1
        freed += size
    return count, freed


def sweep_and_log(config, log, *, min_age=MIN_AGE):
    """Sweep the effective temp dir and log ``temp_cleanup`` when something was removed."""
    count, freed = sweep(config.temp_dir, min_age=min_age)
    if count:
        log.event("temp_cleanup", removed=count, bytes=freed, directory=str(config.temp_dir or tempfile.gettempdir()))
    return count
