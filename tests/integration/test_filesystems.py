import os
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from sambinha.process import Runner
from sambinha.recovery import recover
from sambinha.storage import Storage
from tests.helpers import REAL_TOOLS, Sandbox, contents


def setUpModule():
    # These tables are Linux mountinfo rows; force the Linux backend so the
    # same expectations hold when the suite runs on macOS.
    global _platform
    _platform = patch("sambinha.filesystems._PLATFORM", "linux")
    _platform.start()


def tearDownModule():
    _platform.stop()


def mount_table(*mounts):
    lines = []
    for index, (path, filesystem) in enumerate(mounts, 1):
        encoded = str(path).replace("\\", "\\134").replace(" ", "\\040").replace("\t", "\\011")
        lines.append(f"{index} 0 0:{index} / {encoded} rw - {filesystem} device rw")
    return "\n".join(lines)


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

    def test_batch_restore_recovers_index_on_filesystem_without_chmod_or_hard_links(self):
        import errno
        import shutil
        source = self.file('dir/private', b'payload', mode=0o750)
        self.config = replace(self.config, jobs=(replace(self.config.jobs[0], logic='batch'),))
        self.assertEqual(self.execute(), 0)
        shutil.rmtree(self.config.state_dir)
        output = self.root / 'restored'
        table = mount_table(('/', 'btrfs'), (output, 'exfat'))
        original = Path.chmod
        def guarded_chmod(path, *args, **kwargs):
            if path == output or output in path.parents:
                raise PermissionError('destination does not support chmod')
            return original(path, *args, **kwargs)
        with patch('sambinha.filesystems._MOUNTINFO') as info, \
             patch.object(Path, 'chmod', guarded_chmod), \
             patch('sambinha.restore.os.link', side_effect=OSError(errno.EOPNOTSUPP, 'no hard links')), \
             patch('sambinha.restore.os.fchmod', side_effect=AssertionError('unsupported chmod')):
            info.read_text.return_value = table
            self.assertEqual(recover(self.config, 'restore', output=output, notifier=lambda *args: None), 0)
        self.assertEqual(contents(output), contents(self.source))
        self.assertEqual((output / 'dir/private').stat().st_mtime_ns, source.stat().st_mtime_ns)

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
             patch("sambinha.restore.os.fchmod", side_effect=AssertionError("unsupported chmod")):
            info.read_text.return_value = table
            self.assertEqual(recover(self.config, "restore", output=output, notifier=lambda *args: None), 0)
        self.assertEqual(contents(output), contents(self.source))
        self.assertEqual((output / "dir/private").stat().st_mtime_ns, source.stat().st_mtime_ns)
