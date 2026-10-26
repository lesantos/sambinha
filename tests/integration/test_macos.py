"""macOS support: platform-specific backends and their Linux-side simulations.

The first classes run everywhere by simulating the macOS environment, so the
Linux CI exercises the Darwin code paths. The last class runs only on a real
Mac and checks the same behavior against the live system.
"""


import os
import shutil
import sys
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

from sambinha import filesystems, inventory, mounts
from sambinha.errors import BackupError
from sambinha.filesystems import (
    mounted_filesystems,
    needs_destination_link_scan,
    supports_posix_metadata,
    volatile_inode_devices,
)
from sambinha.mounts import check_mounts, is_mount_point
from sambinha.process import Runner
from tests.helpers import Sandbox

ON_MACOS = sys.platform == "darwin"


# Homebrew's sevenzip formula installs only `7zz`; p7zip-style `7z` is legacy.
MAC_COMPRESSOR = shutil.which("7zz") or shutil.which("7z")


MAC_TOOLS = bool(shutil.which("rclone") and MAC_COMPRESSOR)


@unittest.skipUnless(ON_MACOS, "runs only on macOS")
class RealMacOSTests(Sandbox):
    """Live checks on a Mac: no mocks, real mount table and filesystem."""

    def test_platform_switches_are_active(self):
        self.assertEqual(filesystems._PLATFORM, "darwin")
        self.assertFalse(mounts.uses_mountpoint_command())
        self.assertFalse(hasattr(os, "O_PATH"))

    def test_live_mount_table_lists_root_as_apfs_or_hfs(self):
        table = {str(mount): kind for mount, kind, _device in mounted_filesystems()}
        self.assertIn("/", table)
        self.assertIn(table["/"], {"apfs", "hfs"})
        for mount, _kind, device in mounted_filesystems():
            if mount.exists():
                self.assertEqual(device, os.lstat(mount).st_dev)

    def test_temporary_directory_is_posix_and_not_a_mount(self):
        self.assertTrue(supports_posix_metadata(self.root))
        self.assertTrue(is_mount_point(Path("/")))
        self.assertFalse(is_mount_point(self.root / "source"))
        self.assertTrue(needs_destination_link_scan(self.root))

    def test_external_fat_volumes_if_present_are_classified(self):
        fat = [mount for mount, kind, _device in mounted_filesystems() if kind in {"msdos", "exfat"}]
        for mount in fat:
            with self.subTest(mount=mount):
                self.assertFalse(supports_posix_metadata(mount))
                self.assertFalse(needs_destination_link_scan(mount))
                self.assertIn(os.lstat(mount).st_dev, volatile_inode_devices())

    def test_metadata_only_and_hash_inventories_read_real_files(self):
        self.file("acentuação.txt", b"abc")
        self.file("sub/nested.bin", bytes(range(256)))
        simple = inventory.scan(self.source, mode="simple")
        hashed = inventory.scan(self.source, mode="hash")
        self.assertEqual(sorted(f.path for f in simple), sorted(f.path for f in hashed))
        self.assertEqual({f.path: f.size for f in hashed}, {"acentuação.txt": 3, "sub/nested.bin": 256})

    def test_expected_mount_check_accepts_root_volume_and_refuses_sandbox(self):
        job = replace(self.config.jobs[0], source=Path("/Users"), source_mount=Path("/"))
        check_mounts(job, Mock())
        job = replace(self.config.jobs[0], source=self.source, source_mount=self.root)
        (self.root / "entry").touch()
        with self.assertRaises(BackupError):
            check_mounts(job, Mock())

    @unittest.skipUnless(MAC_TOOLS, "rclone and 7zz required")
    def test_simple_and_batch_backups_run_end_to_end(self):
        self.file("file.txt", b"hello")
        self.file("Icon\r", b"finder")
        self.file("acentuação/relatório.txt", b"nfc")
        self.assertEqual(self.execute(), 0, self.output.getvalue())
        self.assertEqual((self.destination / "file.txt").read_bytes(), b"hello")
        self.assertEqual((self.destination / "acentuação/relatório.txt").read_bytes(), b"nfc")
        # Batch inventories reject control characters in names, so Finder's
        # Icon\r files must be excluded (docs: `Icon[\x0d␍]`) or removed.
        (self.source / "Icon\r").unlink()
        batch = replace(self.config, jobs=(replace(self.config.jobs[0], name="batch", logic="batch",
                                                   compressor=Path(MAC_COMPRESSOR).name,
                                                   destination=str(self.root / "batches")),))
        self.assertEqual(self.execute(batch), 0, self.output.getvalue())
        self.assertTrue(any(path.suffix == ".json" for path in (self.root / "batches").iterdir()))

    @unittest.skipUnless(MAC_TOOLS, "rclone and 7zz required")
    def test_runner_finds_homebrew_tools(self):
        runner = Runner()
        runner.require(["rclone", Path(MAC_COMPRESSOR).name])
