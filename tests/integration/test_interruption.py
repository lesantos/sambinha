"""Ctrl+C and SIGTERM stop children promptly and unwind every cleanup block."""


import json
import os
import signal
import stat
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest import skipUnless

from tests.helpers import REAL_TOOLS, Sandbox

ROOT = Path(__file__).resolve().parents[2]


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def wait_until(condition, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.02)
    return condition()


@skipUnless(REAL_TOOLS, 'requires rclone')
class CommandLineTerminationTests(Sandbox):
    def fake_compressor(self, pid_file):
        path = self.root / 'bin' / 'fake7z'
        path.parent.mkdir(exist_ok=True)
        path.write_text('#!/bin/sh\n'
                        'case "$1" in\n'
                        '  -h) echo "7-Zip (z) 25.01 (x64)" ;;\n'
                        f'  a) echo $$ > "{pid_file}"; exec sleep 60 ;;\n'
                        'esac\n')
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        return path

    def test_sigterm_ends_the_run_cleanly(self):
        self.file('a', b'contents')
        pid_file = self.root / 'compressor.pid'
        temp_dir = self.root / 'tmp'
        temp_dir.mkdir()
        self.destination.mkdir()
        config = self.root / 'sambinha.toml'
        config.write_text(f'[backup]\nstate_dir = "state"\nlog_dir = "logs"\ntemp_dir = "tmp"\n'
                          f'[[jobs]]\nname = "test"\nsource = "source"\ndestination = "destination"\n'
                          f'logic = "batch"\ncompressor = "{self.fake_compressor(pid_file)}"\n'
                          f'log_file_level = "summary"\n')
        process = subprocess.Popen([sys.executable, '-m', 'sambinha', 'backup', '--config', str(config)],
                                   cwd=ROOT, env={**os.environ, 'PYTHONPATH': str(ROOT)},
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertTrue(wait_until(lambda: pid_file.is_file() and pid_file.read_text().strip(), timeout=30),
                            'compression never started')
            compressor = int(pid_file.read_text())
            self.assertTrue(any(p.name.startswith('sambinha-') for p in temp_dir.iterdir()))
            process.send_signal(signal.SIGTERM)
            _stdout, stderr = process.communicate(timeout=20)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
        self.assertEqual(process.returncode, 143, stderr)
        self.assertIn('SIGTERM', stderr)
        logs = list((self.root / 'logs').glob('*.jsonl'))
        self.assertEqual(len(logs), 1)
        events = [json.loads(line) for line in logs[0].read_text().splitlines()]
        self.assertEqual([e['success'] for e in events if e['event'] == 'end'], [False])
        self.assertEqual([e['success'] for e in events if e['event'] == 'summary'], [False])
        self.assertEqual(list(temp_dir.iterdir()), [], 'temporary staging left behind')
        self.assertTrue(wait_until(lambda: not alive(compressor)), 'fake compressor survived')
        self.assertFalse((self.root / 'state' / 'test' / 'state.json').exists())


if __name__ == '__main__':
    unittest.main()
