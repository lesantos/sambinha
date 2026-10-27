import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from sambinha.errors import BackupError
from sambinha.strategies.batch import _stored_md5

MD5 = 'a' * 32


class Storage:
    def __init__(self, reply):
        self.reply, self.calls = reply, 0

    def stat(self, key, *, hashes=True):
        self.calls += 1
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


class StoredMd5Tests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'f'
        self.path.write_bytes(b'12345')

    def ask(self, reply, kind='s3'):
        storage = Storage(reply)
        worker = SimpleNamespace(job=SimpleNamespace(type=kind), storage=storage)
        return _stored_md5(worker, 'k', self.path), storage.calls

    def test_matching_size_and_md5_are_taken_from_the_object(self):
        found, calls = self.ask({'Size': 5, 'Hashes': {'md5': MD5}})
        self.assertEqual((found.size, found.md5, calls), (5, MD5, 1))

    def test_anything_unusable_falls_back_to_reading_the_file(self):
        for reply in ({'Size': 5}, {'Size': 5, 'Hashes': {}}, {'Size': 5, 'Hashes': None},
                      {'Size': 5, 'Hashes': {'md5': 'ABC'}}, {'Size': 5, 'Hashes': {'md5': MD5.upper()}},
                      {'Size': 6, 'Hashes': {'md5': MD5}}, BackupError('rede')):
            with self.subTest(reply=reply):
                self.assertIsNone(self.ask(reply)[0])

    def test_local_destinations_are_never_asked(self):
        self.assertEqual(self.ask({'Size': 5, 'Hashes': {'md5': MD5}}, kind='local'), (None, 0))
