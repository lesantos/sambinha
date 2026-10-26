"""Read-only filesystem capabilities from the current mount namespace."""
import os
import re
import subprocess
import sys
from pathlib import Path

# Request ownership/mode metadata only for filesystems with native POSIX
# attributes. FAT/exFAT, mapped Windows/network mounts and unknown backends
# keep their mounted ownership/modes. Detection never changes a user's file
# or creates a probe, including during simulation.
_POSIX_FILESYSTEMS = frozenset({
    "ext2", "ext3", "ext4", "btrfs", "xfs", "f2fs", "jfs", "reiserfs",
    "zfs", "tmpfs", "ramfs", "overlay", "aufs", "ubifs", "nfs", "nfs4",
    # macOS native volumes; the kernel reports HFS+ as "hfs".
    "apfs", "hfs",
})
_MOUNTINFO = Path("/proc/self/mountinfo")
# Linux reports FAT as vfat/msdos and exFAT as exfat; macOS reports every FAT
# variant as msdos and exFAT as exfat.
_NO_SYMLINK_FILESYSTEMS = frozenset({"exfat", "vfat", "msdos"})
_PLATFORM = sys.platform
_MOUNT_COMMAND = ("/sbin/mount",)
_MOUNT_LINE = re.compile(r"^(?P<device>.*?) on (?P<mount>/.*) \((?P<options>[^()]*)\)$")


def _unescape(field):
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m[1], 8)), field)


def _linux_mounts():
    """Yield (mount, filesystem, device) from /proc/self/mountinfo in table order.

    Rows that cannot be parsed are skipped individually; an unreadable table
    raises so callers keep their conservative defaults.
    """
    table = _MOUNTINFO.read_text(encoding="utf-8")
    for line in table.splitlines():
        fields = line.split()
        try:
            separator = fields.index("-")
            if separator < 6:
                continue
            mount = Path(_unescape(fields[4]))
            kind = fields[separator + 1]
        except (ValueError, IndexError):
            continue
        try:
            major, minor = map(int, fields[2].split(":"))
            device = os.makedev(major, minor)
        except (ValueError, OverflowError):
            device = None
        yield mount, kind, device


def _darwin_mounts():
    """Yield (mount, filesystem, device) from the BSD `mount` listing.

    macOS has no /proc; `mount` prints one `device on /path (type, options)`
    row per volume in mount order, with mount paths printed verbatim even when
    they contain spaces. Device identity comes from the mount's own st_dev,
    which is what the inventory compares against.
    """
    completed = subprocess.run(_MOUNT_COMMAND, capture_output=True, text=True, timeout=30, check=True)
    for line in completed.stdout.splitlines():
        match = _MOUNT_LINE.match(line.strip())
        if not match:
            continue
        mount = Path(match["mount"])
        kind = match["options"].split(",", 1)[0].strip()
        if not kind:
            continue
        try:
            device = os.lstat(mount).st_dev
        except OSError:
            device = None
        yield mount, kind, device


def mounted_filesystems():
    """List (mount, filesystem, device) for the current platform; [] when unknown."""
    try:
        if _PLATFORM == "darwin":
            return list(_darwin_mounts())
        return list(_linux_mounts())
    except (OSError, UnicodeError, subprocess.SubprocessError, ValueError):
        return []


def volatile_inode_devices():
    """Native FAT/exFAT synthesize inode numbers that can change after eviction.

    Identify devices rather than source roots so nested mounts keep their own
    policy. Missing, unknown or ambiguous information retains strict checks.
    This is consulted only when an old observation differs solely by inode.
    """
    devices = {}
    for _mount, kind, device in mounted_filesystems():
        if device is None:
            continue
        devices.setdefault(device, set()).add(kind)
    return frozenset(device for device, kinds in devices.items() if kinds <= _NO_SYMLINK_FILESYSTEMS)


def _mount_filesystem(destination, mounts):
    """Filesystem of the deepest mount containing destination; later rows win ties."""
    filesystem = None
    depth = -1
    for mount, kind, _device in mounts:
        if mount != destination and mount not in destination.parents:
            continue
        # Later stacked mounts at the same path supersede earlier ones.
        if len(mount.parts) >= depth:
            depth, filesystem = len(mount.parts), kind
    return filesystem


def supports_posix_metadata(destination):
    """Use the deepest mounted filesystem, even for an uncreated target.

    Unknown/unavailable mount information is conservative: ordinary copies
    still preserve file contents and modification times through rclone, but
    don't request extended metadata that can invoke chown/chmod.
    """
    try:
        destination = Path(destination).resolve()
    except (OSError, RuntimeError):
        return False
    return _mount_filesystem(destination, mounted_filesystems()) in _POSIX_FILESYSTEMS


def metadata_flags(destination, *, remote=False):
    """Remote metadata is stored by rclone; local attributes need support."""
    return ["--metadata"] if remote or supports_posix_metadata(destination) else []


def needs_destination_link_scan(destination):
    """Skip data traversal only on native FAT/exFAT without link-capable submounts."""
    try:
        destination = Path(destination).resolve()
    except (OSError, RuntimeError):
        return True
    mounts = mounted_filesystems()
    filesystem = _mount_filesystem(destination, mounts)
    if filesystem is None or filesystem not in _NO_SYMLINK_FILESYSTEMS:
        return True
    return any(kind not in _NO_SYMLINK_FILESYSTEMS for mount, kind, _device in mounts
               if mount == destination or destination in mount.parents)
