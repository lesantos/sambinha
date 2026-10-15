import fcntl
import os
import re
import select
import struct
import termios
import unittest
from unittest.mock import Mock, patch

from sambinha.progress import Progress
from sambinha.terminal import cell_width, progress_line, terminal_columns
from tests.test_progress import Terminal


def last_frame(text):
    return re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text.split("\r")[-1])


class TerminalLayoutTests(unittest.TestCase):
    def test_counters_precede_path_and_keep_their_position_when_path_changes(self):
        activity = "conferidos: 40 | copiados: 2 | tempo: 3s"
        for columns in (80, 240):
            for indeterminate in (True, False):
                frames = []
                for path in ("/dados/a", "/dados/" + "diretorio/" * 4 + "arquivo.txt"):
                    with self.subTest(columns=columns, indeterminate=indeterminate, path=path):
                        frame = progress_line(25, "Sincronizando arquivos", path, activity,
                                              columns, indeterminate=indeterminate)
                        fields = frame.split(" | ")
                        self.assertTrue(path.endswith(fields[-1].lstrip("…")), frame)
                        self.assertIn("40", " | ".join(fields[:-1]))
                        self.assertLess(cell_width(frame), columns)
                        frames.append(frame)
                self.assertEqual(frames[0].index("40"), frames[1].index("40"))

    def test_real_terminal_width_prevents_wrapping_and_update_newlines(self):
        master, slave = os.openpty()
        try:
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))
            with os.fdopen(os.dup(slave), "w", encoding="utf-8") as stream:
                progress = Progress(stream)
                self.assertTrue(progress.enabled)
                self.assertEqual(terminal_columns(stream), 80)
                for count in (1, 20, 30000):
                    progress.update("Sincronizando arquivos", "/mnt/capsule/LLLDBKP/long/" + "directory/" * 15,
                                    count, 50000, force=True,
                                    activity=f"/ conferidos: {count}/1000000 | copiados: 0 | remoções: {count} | movidos: {count} | tempo: 320s")
            os.set_blocking(master, False)
            chunks = []
            while select.select([master], [], [], 0.2)[0]:
                chunks.append(os.read(master, 65536))
            output = b"".join(chunks).decode("utf-8")
            frames = [last_frame(frame) for frame in output.split("\r") if frame]
            self.assertEqual(len(frames), 3)
            self.assertNotIn("\n", output)
            self.assertTrue(all(len(frame) < 80 for frame in frames), frames)
            self.assertIn("rem:", frames[-1])
            self.assertIn("mov:", frames[-1])
        finally:
            os.close(slave)
            os.close(master)

    def test_narrow_wide_and_unicode_paths_fit_terminal_cells(self):
        self.assertEqual(cell_width("A界e\u0301"), 4)
        for columns in (1, 20, 40, 80, 160):
            for indeterminate in (True, False):
                with self.subTest(columns=columns, indeterminate=indeterminate):
                    rendered = progress_line(69.7, "Sincronizando arquivos", "/dados/" + "界e\u0301/" * 30,
                                             "- conferidos: 1234567/2000000 | copiados: 0 | remoções: 34567 | movidos: 34567 | tempo: 123s",
                                             columns, indeterminate=indeterminate)
                    # Only these characters in this fixture change cell count.
                    width = len(rendered) + rendered.count("界") - rendered.count("\u0301")
                    self.assertLess(width, columns)
                    self.assertNotIn("\n", rendered)
                    if indeterminate:
                        self.assertNotIn("%", rendered)

    def test_resize_erases_previous_line_if_reflowed_into_two_rows(self):
        terminal = Terminal()
        progress = Progress(terminal)
        with patch("sambinha.progress.terminal_columns", side_effect=(80, 40, 40)):
            progress.update("Sincronizando arquivos", "/dados/" + "very-long-directory/" * 8,
                            activity="conf:1000 cóp:0 rem:1000 mov:1000 t:10s", force=True)
            terminal.seek(0)
            terminal.truncate(0)
            progress.update("Sincronizando arquivos", "/dados", force=True, activity="conf:2000")
            self.assertIn("\x1b[1A", terminal.getvalue())
            self.assertLess(len(last_frame(terminal.getvalue())), 40)
            progress.clear()
            self.assertFalse(progress.visible)

    def test_indeterminate_phase_hides_percentage_until_transfer_total_is_known(self):
        terminal = Terminal()
        clock = Mock(return_value=0)
        progress = Progress(terminal, clock=clock)
        with progress.scope(0.2, 0.8).task("Sincronizando arquivos", "/dados", measured=True) as report:
            self.assertNotIn("%", last_frame(terminal.getvalue()))
            clock.return_value = 1
            report(None, activity="conferidos: 40 | remoções: 3")
            self.assertNotIn("%", last_frame(terminal.getvalue()))
            self.assertEqual(progress.percent, 20)
            clock.return_value = 2
            report(50, activity="copiados: 1/2")
            self.assertIn("50.0%", last_frame(terminal.getvalue()))
            clock.return_value = 3
            report(None, activity="conferidos: 60")
            self.assertNotIn("%", last_frame(terminal.getvalue()))
            self.assertEqual(progress.percent, 50)

    def test_long_activity_is_redacted_and_control_characters_cannot_wrap(self):
        terminal = Terminal()
        progress = Progress(terminal, redact=lambda text: text.replace("secret", "[REDACTED]"))
        with patch("sambinha.progress.terminal_columns", return_value=80):
            progress.update("Sincronizando", "/secret/" + "nested/" * 40,
                            activity="secret\n\t\x1b" + "data/" * 40)
        frame = last_frame(terminal.getvalue())
        self.assertLess(len(frame), 80)
        self.assertNotIn("secret", frame)
        self.assertNotIn("\n", frame)
