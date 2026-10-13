"""Read-only detection of destinations with native POSIX file attributes."""
from pathlib import Path
import re


# Request ownership/mode metadata only for filesystems with native POSIX
# attributes. FAT/exFAT, mapped Windows/network mounts and unknown backends
# keep their mounted ownership/modes. Detection never changes a user's file
# or creates a probe, including during simulation.
_POSIX_FILESYSTEMS = frozenset({
    "ext2", "ext3", "ext4", "btrfs", "xfs", "f2fs", "jfs", "reiserfs",
    "zfs", "tmpfs", "ramfs", "overlay", "aufs", "ubifs", "nfs", "nfs4",
})
_MOUNTINFO = Path("/proc/self/mountinfo")


def supports_posix_metadata(destination):
    """Use the deepest mounted filesystem, even for an uncreated target.

    Unknown/unavailable mount information is conservative: ordinary copies
    still preserve file contents and modification times through rclone, but
    don't request extended metadata that can invoke chown/chmod.
    """
    try:
        destination = Path(destination).resolve()
        table = _MOUNTINFO.read_text(encoding="utf-8")
    except (OSError, RuntimeError, UnicodeError):
        return False
    filesystem = None
    depth = -1
    for line in table.splitlines():
        fields = line.split()
        try:
            separator = fields.index("-")
            if separator < 6:
                continue
            mount = Path(re.sub(r"\\([0-7]{3})", lambda m: chr(int(m[1], 8)), fields[4]))
            destination.relative_to(mount)
            kind = fields[separator + 1]
        except (ValueError, IndexError):
            continue
        # Later stacked mounts at the same path supersede earlier ones.
        if len(mount.parts) >= depth:
            depth, filesystem = len(mount.parts), kind
    return filesystem in _POSIX_FILESYSTEMS


def metadata_flags(destination, *, remote=False):
    """Remote metadata is stored by rclone; local attributes need support."""
    return ["--metadata"] if remote or supports_posix_metadata(destination) else []
