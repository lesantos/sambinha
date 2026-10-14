from dataclasses import replace
import json
import os
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from sambinha.filesystems import supports_posix_metadata, needs_destination_link_scan
from sambinha.errors import BackupError
from sambinha.process import Runner
from sambinha.recovery import recover
from sambinha.storage import Storage
from tests.helpers import REAL_TOOLS, Sandbox, contents


def mount_table(*mounts):
    lines = []
    for index, (path, filesystem) in enumerate(mounts, 1):
        encoded = str(path).replace("\\", "\\134").replace(" ", "\\040").replace("\t", "\\011")
        lines.append(f"{index} 0 0:{index} / {encoded} rw - {filesystem} device rw")
    return "\n".join(lines)


class FilesystemDetectionTests(unittest.TestCase):
    def test_native_fat_skips_link_scan_unless_a_submount_can_contain_links(self):
        for filesystem in ('exfat', 'vfat', 'msdos'):
            table = mount_table(("/", "ext4"), ("/mnt/backup", filesystem))
            with self.subTest(filesystem=filesystem), patch("sambinha.filesystems._MOUNTINFO") as info:
                info.read_text.return_value = table
                self.assertFalse(needs_destination_link_scan("/mnt/backup/tree"))
                info.read_text.return_value = table + '\n' + mount_table(("/mnt/backup/tree/nested", "tmpfs"))
                self.assertTrue(needs_destination_link_scan("/mnt/backup/tree"))
                self.assertFalse(needs_destination_link_scan("/mnt/backup/other"))

    def test_link_scan_handles_stacked_mounts_and_unknown_information_conservatively(self):
        with patch("sambinha.filesystems._MOUNTINFO") as info:
            info.read_text.return_value = mount_table(("/", "ext4"), ("/mnt/with spaces", "tmpfs"),
                                                      ("/mnt/with spaces", "exfat"))
            self.assertFalse(needs_destination_link_scan("/mnt/with spaces/new"))
            for table in ('invalid', '1 0 0:1 / / rw -', mount_table(("/", "fuseblk"))):
                info.read_text.return_value = table
                self.assertTrue(needs_destination_link_scan("/mnt/new"))
            info.read_text.side_effect = OSError('unavailable')
            self.assertTrue(needs_destination_link_scan("/mnt/new"))

    def test_nested_nonposix_mount_overrides_posix_parent_for_new_target(self):
        table = mount_table(("/", "btrfs"), ("/mnt/backup", "exfat"))
        with patch("sambinha.filesystems._MOUNTINFO") as info:
            info.read_text.return_value = table
            self.assertFalse(supports_posix_metadata("/mnt/backup/not-created/file"))
            self.assertTrue(supports_posix_metadata("/mnt/backup-other/file"))

    def test_escaped_paths_and_stacked_mounts_use_visible_filesystem(self):
        table = mount_table(("/", "ext4"), ("/mnt/with spaces", "ext4"),
                            ("/mnt/with spaces", "vfat"))
        with patch("sambinha.filesystems._MOUNTINFO") as info:
            info.read_text.return_value = table
            self.assertFalse(supports_posix_metadata("/mnt/with spaces/new"))
            self.assertTrue(supports_posix_metadata("/mnt/other"))

    def test_unavailable_malformed_and_unknown_mounts_skip_metadata(self):
        with patch("sambinha.filesystems._MOUNTINFO") as info:
            info.read_text.side_effect = OSError("unavailable")
            self.assertFalse(supports_posix_metadata("/tmp/new"))
            info.read_text.side_effect = None
            for table in ("bad row", "1 0 0:1 / / rw -", mount_table(("/", "fuse.mapped"))):
                with self.subTest(table=table):
                    info.read_text.return_value = table
                    self.assertFalse(supports_posix_metadata("/tmp/new"))


class TransferMetadataTests(Sandbox):
    def test_native_exfat_sync_does_not_scan_destination_entries(self):
        table = mount_table(("/", "ext4"), (self.destination, "exfat"))
        with patch("sambinha.filesystems._MOUNTINFO") as info, \
             patch("sambinha.storage.conflicting_links", side_effect=AssertionError('extra traversal')):
            info.read_text.return_value = table
            Storage(self.config.jobs[0], self.config, Mock()).sync_simple(
                self.source, ".sambinha/history/run", max_delete=100)

    def test_native_exfat_optimization_still_rejects_root_links(self):
        outside = self.root / 'outside'
        outside.mkdir()
        self.destination.symlink_to(outside, target_is_directory=True)
        with patch("sambinha.storage.needs_destination_link_scan", return_value=False), \
             self.assertRaises(BackupError):
            Storage(self.config.jobs[0], self.config, Mock()).sync_simple(
                self.source, ".sambinha/history/run", max_delete=100)
        self.assertEqual(list(outside.iterdir()), [])

    def test_sync_uses_destination_and_retains_deletion_guard_without_retry(self):
        runner = Mock()
        table = mount_table(("/", "btrfs"), (self.destination, "exfat"))
        with patch("sambinha.filesystems._MOUNTINFO") as info:
            info.read_text.return_value = table
            Storage(self.config.jobs[0], self.config, runner).sync_simple(
                self.source, ".sambinha/history/run", max_delete=123, dry_run=True)
        args = runner.run.call_args.args[0]
        self.assertNotIn("--metadata", args)
        self.assertIn("--dry-run", args)
        self.assertEqual(args[args.index("--max-delete") + 1], "123")
        self.assertIn("--backup-dir", args)
        self.assertFalse(runner.run.call_args.kwargs["retry"])
        runner.run.assert_called_once()

    def test_remote_sync_keeps_metadata_without_local_detection(self):
        job = replace(self.config.jobs[0], type="s3", destination="aws:bucket/prefix")
        runner = Mock()
        with patch("sambinha.filesystems.supports_posix_metadata", side_effect=AssertionError("local probe")):
            Storage(job, self.config, runner).sync_simple(
                self.source, ".sambinha/history/run", max_delete=100)
        self.assertIn("--metadata", runner.run.call_args.args[0])

    def test_restore_checks_output_instead_of_backup_for_all_and_selected_files(self):
        output = self.root / "restored"
        table = mount_table(("/", "ext4"), (output, "exfat"))
        for selectors, is_dir in (((), False), (("dir",), True), (("file",), False)):
            with self.subTest(selectors=selectors):
                runner = Mock()
                runner.run.return_value = json.dumps({"IsDir": is_dir})
                with patch("sambinha.filesystems._MOUNTINFO") as info:
                    info.read_text.return_value = table
                    Storage(self.config.jobs[0], self.config, runner).restore_simple(output, selectors)
                copies = [c.args[0] for c in runner.run.call_args_list if c.args[0][1] in ("copy", "copyto")]
                self.assertEqual(len(copies), 1)
                self.assertNotIn("--metadata", copies[0])


@unittest.skipUnless(REAL_TOOLS, "rclone and 7z required")
class FilesystemIntegrationTests(Sandbox):
    def test_nonposix_sync_and_restore_keep_contents_and_mtime_without_metadata(self):
        source = self.file("dir/private", b"payload", mode=0o750)
        os.utime(source, ns=(1700000000000000000, 1700000000000000000))
        output = self.root / "restored"
        table = mount_table(("/", "btrfs"), (self.destination, "exfat"), (output, "exfat"))
        calls = []
        original = Runner.run

        def record(runner, args, **kwargs):
            if args[1] in ("sync", "copy", "copyto"):
                calls.append(args)
                self.assertNotIn("--metadata", args)
            return original(runner, args, **kwargs)

        with patch("sambinha.filesystems._MOUNTINFO") as info, patch.object(Runner, "run", record):
            info.read_text.return_value = table
            self.assertEqual(self.execute(), 0, self.output.getvalue())
            Storage(self.config.jobs[0], self.config, Runner()).restore_simple(output)
        self.assertEqual(contents(output), contents(self.source))
        self.assertEqual((self.destination / "dir/private").stat().st_mtime_ns, source.stat().st_mtime_ns)
        self.assertEqual((output / "dir/private").stat().st_mtime_ns, source.stat().st_mtime_ns)
        self.assertTrue(any(args[1] == "sync" for args in calls))

    def test_batch_restore_skips_rejected_output_chmod_and_preserves_contents(self):
        source = self.file("dir/private", b"payload", mode=0o750)
        self.config = replace(self.config, jobs=(replace(self.config.jobs[0], logic="batch"),))
        self.assertEqual(self.execute(), 0, self.output.getvalue())
        output = self.root / "restored"
        table = mount_table(("/", "btrfs"), (output, "exfat"))
        original = Path.chmod

        def guarded_chmod(path, *args, **kwargs):
            if path == output or output in path.parents:
                raise PermissionError("destination does not support chmod")
            return original(path, *args, **kwargs)

        with patch("sambinha.filesystems._MOUNTINFO") as info, \
             patch.object(Path, "chmod", guarded_chmod), \
             patch("sambinha.recovery.os.fchmod", side_effect=AssertionError("unsupported chmod")):
            info.read_text.return_value = table
            self.assertEqual(recover(self.config, "restore", output=output, notifier=lambda *args: None), 0)
        self.assertEqual(contents(output), contents(self.source))
        self.assertEqual((output / "dir/private").stat().st_mtime_ns, source.stat().st_mtime_ns)
