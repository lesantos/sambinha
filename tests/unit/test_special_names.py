import io
import json
import os
from contextlib import redirect_stdout
from dataclasses import replace
from unittest.mock import Mock, patch

from sambinha import archives
from sambinha.batches import plan
from sambinha.context import Context
from sambinha.errors import BackupError
from sambinha.inventory import scan, verify
from sambinha.logging import RunLog
from sambinha.process import Runner
from tests.helpers import Sandbox


class InventoryNameTests(Sandbox):
    def test_scan_rejects_non_utf8_names_early(self):
        fd = os.open(os.fsencode(self.source) + b'/bad\xff.txt', os.O_WRONLY | os.O_CREAT, 0o640)
        os.close(fd)
        with self.assertRaisesRegex(BackupError, r"UTF-8.*'bad\\udcff\.txt'"):
            scan(self.source, mode='simple')


class LogEncodingTests(Sandbox):
    def test_error_events_with_surrogates_never_raise_and_stay_valid_json(self):
        output = io.StringIO()
        with redirect_stdout(output):
            log = RunLog(self.config.log_dir, file_level='detailed', terminal_level='detailed')
            try:
                log.event('error', error='Arquivo: bad\udcff.txt', details={'paths': ['x\udcff']})
            finally:
                log.close()
        for text in (output.getvalue(), log.path.read_text(encoding='utf-8')):
            record = json.loads(text.splitlines()[-1])
            self.assertEqual(record['error'], 'Arquivo: bad\udcff.txt')
            self.assertEqual(record['details'], {'paths': ['x\udcff']})


class PassthroughVerificationTests(Sandbox):
    def test_passthrough_batch_verifies_the_source_once(self):
        self.file('big.zip', b'z' * 4096)
        job = replace(self.config.jobs[0], logic='batch', max_megabytes=1 / 1024,
                      check_source_disk=False, check_destination_disk=False)
        config = replace(self.config, jobs=(job,))
        files = scan(self.source)
        batch = plan(files, 1024)[0]
        self.assertTrue(batch.passthrough)
        ctx = Context(config, job, Runner(), Mock(), files, 1000)
        with patch.object(archives, 'verify', wraps=verify) as verified:
            self.assertEqual(archives.compress(ctx, batch, self.root), self.source / 'big.zip')
        self.assertEqual(verified.call_count, 1)
