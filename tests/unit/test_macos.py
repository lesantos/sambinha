"""macOS support: platform-specific backends and their Linux-side simulations.

The first classes run everywhere by simulating the macOS environment, so the
Linux CI exercises the Darwin code paths. The last class runs only on a real
Mac and checks the same behavior against the live system.
"""


import os
import subprocess
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from sambinha import filesystems, inventory, mounts
from sambinha.errors import BackupError
from sambinha.filesystems import (
    mounted_filesystems,
    needs_destination_link_scan,
    supports_posix_metadata,
    volatile_inode_devices,
)
from sambinha.mounts import check_mounts, is_mount_point
from tests.helpers import Sandbox

MOUNT_OUTPUT = """\
/dev/disk3s1s1 on / (apfs, sealed, local, read-only, journaled)
devfs on /dev (devfs, local, nobrowse)
/dev/disk3s6 on /System/Volumes/VM (apfs, local, noexec, journaled, noatime, nobrowse)
/dev/disk3s5 on /System/Volumes/Data (apfs, local, journaled, nobrowse, protect)
map auto_home on /System/Volumes/Data/home (autofs, automounted, nobrowse)
/dev/disk4s1 on /Volumes/Backup Disk (exfat, local, nodev, nosuid, noowners)
/dev/disk5s2 on /Volumes/Time Machine (hfs, local, nodev, nosuid, journaled)
/dev/disk6s1 on /Volumes/USB (msdos, local, nodev, nosuid, noowners)
//user@server/share on /Volumes/share (smbfs, nodev, nosuid, mounted by user)
"""


def fake_mount(output=MOUNT_OUTPUT):
    def run(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, stdout=output, stderr="")
    return run


class DarwinMountTableTests(unittest.TestCase):
    """Simulated macOS: `mount` output drives filesystem detection."""

    def setUp(self):
        self.devices = {}
        patches = [patch.object(filesystems, "_PLATFORM", "darwin"),
                   patch.object(filesystems.subprocess, "run", fake_mount()),
                   patch.object(filesystems.os, "lstat", self.lstat),
                   patch.object(filesystems.Path, "resolve", lambda path: path)]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def lstat(self, path):
        result = Mock()
        result.st_dev = self.devices.setdefault(str(path), 100 + len(self.devices))
        return result

    def test_parses_mount_rows_including_paths_with_spaces(self):
        table = {str(mount): kind for mount, kind, _device in mounted_filesystems()}
        self.assertEqual(table["/"], "apfs")
        self.assertEqual(table["/Volumes/Backup Disk"], "exfat")
        self.assertEqual(table["/Volumes/Time Machine"], "hfs")
        self.assertEqual(table["/Volumes/USB"], "msdos")
        self.assertEqual(table["/Volumes/share"], "smbfs")
        self.assertEqual(table["/System/Volumes/Data/home"], "autofs")

    def test_apfs_and_hfs_support_posix_metadata_but_fat_and_smb_do_not(self):
        self.assertTrue(supports_posix_metadata("/Users/leandro/Documents"))
        self.assertTrue(supports_posix_metadata("/Volumes/Time Machine/backup"))
        self.assertFalse(supports_posix_metadata("/Volumes/Backup Disk/backup"))
        self.assertFalse(supports_posix_metadata("/Volumes/USB"))
        self.assertFalse(supports_posix_metadata("/Volumes/share/backup"))

    def test_native_fat_volumes_skip_the_destination_link_scan(self):
        self.assertFalse(needs_destination_link_scan("/Volumes/Backup Disk/sambinha"))
        self.assertFalse(needs_destination_link_scan("/Volumes/USB"))
        self.assertTrue(needs_destination_link_scan("/Volumes/Time Machine/sambinha"))
        self.assertTrue(needs_destination_link_scan("/Users/leandro"))
        self.assertTrue(needs_destination_link_scan("/Volumes/share"))

    def test_fat_devices_tolerate_inode_renumbering_using_their_own_st_dev(self):
        volatile = volatile_inode_devices()
        self.assertIn(self.devices["/Volumes/Backup Disk"], volatile)
        self.assertIn(self.devices["/Volumes/USB"], volatile)
        self.assertNotIn(self.devices["/"], volatile)
        self.assertNotIn(self.devices["/Volumes/Time Machine"], volatile)

    def test_unreadable_or_malformed_mount_listing_keeps_conservative_defaults(self):
        for failure in (subprocess.CalledProcessError(1, "mount"), OSError("missing"),
                        subprocess.TimeoutExpired("mount", 30)):
            with self.subTest(failure=failure), patch.object(filesystems.subprocess, "run", side_effect=failure):
                self.assertEqual(mounted_filesystems(), [])
                self.assertFalse(supports_posix_metadata("/Users/leandro"))
                self.assertTrue(needs_destination_link_scan("/Volumes/Backup Disk"))
                self.assertEqual(volatile_inode_devices(), frozenset())
        with patch.object(filesystems.subprocess, "run", fake_mount("garbage\n/dev/x on (apfs)\n on /nope ()\n")):
            self.assertEqual(mounted_filesystems(), [])
            self.assertFalse(supports_posix_metadata("/Users/leandro"))

    def test_mount_row_whose_path_vanished_has_no_device(self):
        def lstat(path):
            if str(path) == "/Volumes/USB":
                raise OSError("gone")
            return self.lstat(path)
        with patch.object(filesystems.os, "lstat", lstat):
            rows = {str(mount): device for mount, _kind, device in mounted_filesystems()}
        self.assertIsNone(rows["/Volumes/USB"])
        self.assertIsNotNone(rows["/"])

    def test_linux_table_is_not_consulted_on_darwin(self):
        with patch.object(filesystems, "_MOUNTINFO") as info:
            mounted_filesystems()
            info.read_text.assert_not_called()


class DarwinMountCheckTests(Sandbox):
    """Simulated macOS: expected mounts are checked without util-linux."""

    def setUp(self):
        super().setUp()
        platform = patch.object(mounts, "_PLATFORM", "darwin")
        platform.start()
        self.addCleanup(platform.stop)

    def test_mountpoint_command_is_not_required_or_executed(self):
        volume = self.root / "volume"
        volume.mkdir()
        (volume / "entry").touch()
        runner = Mock()
        job = replace(self.config.jobs[0], source=volume, source_mount=volume)
        with patch.object(mounts, "is_mount_point", return_value=True):
            check_mounts(job, runner)
        runner.run.assert_not_called()

    def test_ordinary_directory_is_refused_as_mount(self):
        volume = self.root / "volume"
        volume.mkdir()
        (volume / "entry").touch()
        job = replace(self.config.jobs[0], source=volume, source_mount=volume)
        with self.assertRaises(BackupError) as caught:
            check_mounts(job, Mock())
        self.assertIn("Montagem de origem", str(caught.exception))

    def test_empty_or_missing_volume_is_still_refused(self):
        volume = self.root / "volume"
        volume.mkdir()
        job = replace(self.config.jobs[0], source=volume, source_mount=volume)
        with patch.object(mounts, "is_mount_point", return_value=True), self.assertRaises(BackupError):
            check_mounts(job, Mock())
        with self.assertRaises(BackupError):
            check_mounts(replace(job, source_mount=self.root / "missing"), Mock())

    def test_preflight_requires_only_the_compressor(self):
        from sambinha.preflight import preflight
        self.file("file")
        runner = Mock()
        runner.run.return_value = ""
        config = replace(self.config, jobs=(replace(self.config.jobs[0], source_mount=self.source),))
        with patch.object(mounts, "is_mount_point", return_value=True), \
             patch("sambinha.preflight.check_disks"):
            preflight(config, runner)
        for call in runner.require.call_args_list:
            self.assertNotIn("mountpoint", call.args[0])


class SymlinkedTemporaryDirectoryTests(Sandbox):
    """macOS /var -> /private/var: default work directories must resolve."""

    def test_default_temporary_directory_is_resolved_and_metadata_dirs_accept_it(self):
        import tempfile

        from sambinha.disks import use_real_temporary_directory
        from sambinha.state import private_directory
        real = self.root / "real-tmp"
        real.mkdir()
        link = self.root / "var"
        link.symlink_to(real, target_is_directory=True)
        with patch.object(tempfile, "tempdir", str(link)):
            with self.assertRaises(BackupError):
                private_directory(Path(tempfile.gettempdir()) / "sambinha-work" / "state")
            use_real_temporary_directory(self.config)
            self.assertEqual(Path(tempfile.gettempdir()), real)
            private_directory(Path(tempfile.gettempdir()) / "sambinha-work" / "state")
        self.assertTrue((real / "sambinha-work" / "state").is_dir())

    def test_configured_temp_dir_is_left_alone(self):
        import tempfile

        from sambinha.disks import use_real_temporary_directory
        link = self.root / "var"
        link.symlink_to(self.root / "source", target_is_directory=True)
        with patch.object(tempfile, "tempdir", str(link)):
            use_real_temporary_directory(replace(self.config, temp_dir=self.root))
            self.assertEqual(tempfile.gettempdir(), str(link))


class PortableMountPointTests(unittest.TestCase):
    def test_root_and_proc_style_mounts_are_detected_and_plain_directories_are_not(self):
        self.assertTrue(is_mount_point(Path("/")))
        home = Path.home()
        self.assertEqual(is_mount_point(home),
                         os.lstat(home).st_dev != os.lstat(home.parent).st_dev)
        self.assertFalse(is_mount_point(Path("/definitely/missing/path")))

    def test_subdirectory_of_a_single_volume_is_not_a_mount(self):
        import tempfile
        with tempfile.TemporaryDirectory() as temp:
            inner = Path(temp) / "inner"
            inner.mkdir()
            self.assertFalse(is_mount_point(inner))


class MetadataOnlyDescriptorTests(Sandbox):
    """Inventory without O_PATH behaves like macOS and still detects races."""

    def test_metadata_only_scan_works_with_read_only_fallback(self):
        self.file("a.txt", b"alpha")
        self.file("dir/b.txt", b"beta")
        with patch.object(inventory, "_METADATA_FLAGS", os.O_RDONLY | os.O_NONBLOCK):
            files = inventory.scan(self.source, mode="simple")
        self.assertEqual(sorted(f.path for f in files), ["a.txt", "dir/b.txt"])
        self.assertEqual({f.path: f.size for f in files}, {"a.txt": 5, "dir/b.txt": 4})

    def test_fallback_flags_are_chosen_when_o_path_is_missing(self):
        self.assertEqual(getattr(os, "O_PATH", os.O_RDONLY | os.O_NONBLOCK), inventory._METADATA_FLAGS)
        if not hasattr(os, "O_PATH"):
            self.assertEqual(inventory._METADATA_FLAGS, os.O_RDONLY | os.O_NONBLOCK)
