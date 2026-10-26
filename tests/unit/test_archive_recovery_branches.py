"""archive_recovery: 7z listing parsing, date precision, integrity checks and unpacking, with a scripted runner."""
import hashlib
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from sambinha.archive_recovery import archive_date_matches, archive_members, check_archive, check_content, unpack
from sambinha.errors import BackupError
from sambinha.storage import Storage
from tests.helpers import FakeRunner, Sandbox

MTIME = 1_700_000_000 * 10 ** 9 + 123_456_700  # fraction survives at 100 ns


def record(path, size=1, **fields):
    return '\n'.join(f'{k} = {v}' for k, v in {'Path': path, 'Size': size, **fields}.items())


def listing(*records):
    return '\n\n'.join(records) + '\n'


def item(path='a.txt', payload=b'x', mtime_ns=MTIME):
    return {'path': path, 'size': len(payload), 'sha256': hashlib.sha256(payload).hexdigest(),
            'md5': hashlib.md5(payload).hexdigest(), 'mtime_ns': mtime_ns, 'mode': 0o640}


class ArchiveRecoveryTests(Sandbox):
    def storage(self, runner):
        job = replace(self.config.jobs[0], type='s3', destination='aws:bucket', logic='batch')
        return Storage(job, self.config, runner)

    # archive_members
    def test_members_skip_folders_and_strip_dot_slash(self):
        text = listing(record('./dir', 0, Folder='+'), record('dir/a.txt', 3, Attributes='A -rw-r--r--'),
                       record('d2', 0, Attributes='D drwx'), 'Type = 7z', record('b\\c', 2))
        runner = FakeRunner({'7z l': text})
        members = archive_members(self.storage(runner), runner, Path('x.7z'))
        self.assertEqual(set(members), {'dir/a.txt', 'b\\c'})  # a backslash is an ordinary name character
        self.assertEqual(runner.options[0]['env'], {'TZ': 'UTC'})

    def test_members_reject_links_special_files_duplicates_and_unsafe_paths(self):
        for name, text in (('symlink', listing(record('a', **{'Symbolic Link': 'b'}))),
                           ('hard link', listing(record('a', **{'Hard Link': 'b'}))),
                           ('link attribute', listing(record('a', Attributes='lrwxrwxrwx'))),
                           ('device', listing(record('a', Attributes='A c--------'))),
                           ('duplicate', listing(record('a'), record('a'))),
                           ('unsafe', listing(record('../a')))):
            with self.subTest(name), self.assertRaises(BackupError):
                runner = FakeRunner({'7z l': text})
                archive_members(self.storage(runner), runner, Path('x.7z'))

    # archive_date_matches
    def fields(self, modified, characteristics=''):
        return {'Modified': modified, 'Characteristics': characteristics}

    def test_date_precision_follows_the_archive_format(self):
        meta = item(mtime_ns=1_700_000_001 * 10 ** 9 + 500_000_000)
        stamp = datetime.fromtimestamp(1_700_000_001, UTC).strftime('%Y-%m-%d %H:%M:%S')
        self.assertTrue(archive_date_matches(self.fields(stamp + '.5000000'), meta, '7z'))
        self.assertFalse(archive_date_matches(self.fields(stamp + '.6000000'), meta, '7z'))
        whole = item(mtime_ns=1_700_000_001 * 10 ** 9)
        self.assertTrue(archive_date_matches(self.fields(stamp, 'NTFS'), whole, 'zip'))
        self.assertTrue(archive_date_matches(self.fields(stamp, 'UT'), whole, 'zip'))
        even = datetime.fromtimestamp(1_700_000_001 // 2 * 2, UTC).strftime('%Y-%m-%d %H:%M:%S')
        self.assertTrue(archive_date_matches(self.fields(even, 'Zip: Dos'), whole, 'zip'))  # DOS: 2 s granularity
        self.assertFalse(archive_date_matches(self.fields(even + '.1', 'UT'), whole, 'zip'))  # fraction on a UT entry

    def test_invalid_or_absent_date_is_an_error(self):
        for text in ('', 'yesterday', '2026-01-01', '2026-01-01 00:00:00.1234567890'):
            with self.subTest(text=text), self.assertRaisesRegex(BackupError, 'Data de modificação'):
                archive_date_matches({'Modified': text}, item(), '7z')
        with self.assertRaises(BackupError):
            archive_date_matches({}, item(), '7z')

    # check_content
    def test_content_checks_by_mode(self):
        self.file('a.txt', b'x')
        meta = item(mtime_ns=self.source.joinpath('a.txt').stat().st_mtime_ns)
        check_content(self.source, meta)
        check_content(self.source, meta, mode='simple', check_mtime=True)
        check_content(self.source, {**meta, 'sha256': '', 'md5': ''})  # no hashes: defaults to simple
        for name, change in (('size', {'size': 9}), ('hash', {'sha256': 'f' * 64}), ('md5', {'md5': 'f' * 32})):
            with self.subTest(name), self.assertRaisesRegex(BackupError, 'Integridade divergente'):
                check_content(self.source, {**meta, **change})
        with self.assertRaisesRegex(BackupError, 'Integridade divergente'):
            check_content(self.source, {**meta, 'mtime_ns': 1}, mode='simple', check_mtime=True)
        check_content(self.source, {**meta, 'mtime_ns': 1}, mode='simple')  # mtime is optional
        with self.assertRaisesRegex(BackupError, 'sem hashes'):
            check_content(self.source, {**meta, 'sha256': '', 'md5': ''}, mode='hash')

    # check_archive
    def stamp(self, mtime_ns=MTIME):
        seconds, nanos = divmod(mtime_ns, 10 ** 9)
        return datetime.fromtimestamp(seconds, UTC).strftime('%Y-%m-%d %H:%M:%S') + f'.{nanos:09d}'[:8] + '00'

    def test_check_archive_accepts_a_matching_listing_and_tests_integrity(self):
        runner = FakeRunner({'7z l': listing(record('a.txt', 1, Modified=self.stamp()))})
        control = {'files': [item()]}
        check_archive(self.storage(runner), runner, 'k.7z', control, archive=self.root / 'k.7z')
        self.assertEqual(runner.commands[-1], ['7z', 't', str(self.root / 'k.7z')])

    def test_check_archive_defaults_to_the_stored_object_path(self):
        runner = FakeRunner({'7z l': listing(record('a.txt', 1, Modified=self.stamp()))})
        check_archive(self.storage(runner), runner, 'k.7z', {'files': [item()]})
        self.assertEqual(runner.commands[0][-1], 'aws:bucket/k.7z')

    def test_check_archive_rejects_wrong_members_sizes_and_dates(self):
        control = {'files': [item()]}
        for name, text, message in (
                ('missing member', listing(), 'incompleto'),
                ('extra member', listing(record('a.txt', 1, Modified=self.stamp()), record('b', 1)), 'incompleto'),
                ('wrong size', listing(record('a.txt', 2, Modified=self.stamp())), 'incompleto'),
                ('wrong date', listing(record('a.txt', 1, Modified=self.stamp(MTIME + 10 ** 9))), 'Data de modificação')):
            with self.subTest(name), self.assertRaisesRegex(BackupError, message):
                runner = FakeRunner({'7z l': text})
                check_archive(self.storage(runner), runner, 'k.7z', control, archive=self.root / 'k.7z')
        self.assertEqual(runner.calls('7z', 't'), [])  # integrity test only after the listing agrees

    def test_passthrough_archives_compare_size_and_md5(self):
        payload = b'plain bytes'
        (self.root / 'k.bin').write_bytes(payload)
        meta = item('a.bin', payload)
        control = {'passthrough': True, 'files': [meta]}
        runner = FakeRunner()
        storage = self.storage(runner)
        check_archive(storage, runner, 'k.bin', control, archive=self.root / 'k.bin')
        check_archive(storage, runner, 'k.bin', {**control, 'archive_size': len(payload),
                                                 'archive_md5': meta['md5']}, archive=self.root / 'k.bin')
        for name, bad in (('size', {**control, 'files': [{**meta, 'size': 1}]}),
                          ('item md5', {**control, 'files': [{**meta, 'md5': 'f' * 32}]}),
                          ('archive md5', {**control, 'archive_size': len(payload), 'archive_md5': 'f' * 32}),
                          ('archive size', {**control, 'archive_size': 1, 'archive_md5': meta['md5']}),
                          ('two files', {**control, 'files': [meta, meta]})):
            with self.subTest(name), self.assertRaisesRegex(BackupError, 'individual divergente'):
                check_archive(storage, runner, 'k.bin', bad, archive=self.root / 'k.bin')
        self.assertEqual(runner.commands, [])  # never listed or tested with 7z

    # unpack
    def archive_runner(self, payload=b'archive bytes', listing_text='', extract=None):
        def copyto(command, **options):
            Path(command[3]).write_bytes(payload)
            return ''

        def extract_files(command, **options):
            target = Path(command[4][2:])
            for name, data in (extract or {}).items():
                (target / name).parent.mkdir(parents=True, exist_ok=True)
                (target / name).write_bytes(data)
            return ''
        return FakeRunner({'rclone copyto': copyto, '7z l': listing_text, '7z x': extract_files})

    def work(self):
        work = self.root / 'work'
        work.mkdir(exist_ok=True)
        return work

    def test_unpack_extracts_and_verifies_every_member(self):
        runner = self.archive_runner(listing_text=listing(record('a.txt', 1, Modified='x')), extract={'a.txt': b'x'})
        control = {'files': [item()]}
        extracted = unpack(self.storage(runner), runner, 'k.7z', control, self.work())
        self.assertEqual((extracted / 'a.txt').read_bytes(), b'x')
        self.assertEqual([c[1] for c in runner.commands if c[0] == '7z'], ['l', 't', 'x'])

    def test_unpack_detects_archive_checksum_listing_and_content_divergence(self):
        control = {'files': [item()]}
        payload = b'archive bytes'
        with self.assertRaisesRegex(BackupError, 'Checksum do lote'):
            runner = self.archive_runner(payload)
            unpack(self.storage(runner), runner, 'k.7z',
                   {**control, 'archive_size': len(payload), 'archive_md5': 'f' * 32}, self.work())
        text = listing(record('a.txt', 1))
        with self.assertRaisesRegex(BackupError, 'Lote incompleto'):
            runner = self.archive_runner(listing_text=listing(record('b.txt', 1)))
            unpack(self.storage(runner), runner, 'k.7z', control, self.work() / 'w1')
        with self.assertRaisesRegex(BackupError, 'Conteúdo extraído diferente'):
            runner = self.archive_runner(listing_text=text, extract={'other.txt': b'x'})
            unpack(self.storage(runner), runner, 'k.7z', control, self.work() / 'w2')
        with self.assertRaisesRegex(BackupError, 'Integridade divergente'):
            runner = self.archive_runner(listing_text=text, extract={'a.txt': b'y'})
            unpack(self.storage(runner), runner, 'k.7z', control, self.work() / 'w3')

    def test_unpack_passthrough_copies_the_object_and_verifies_it(self):
        payload = b'plain bytes'
        meta = item('dir/a.bin', payload)
        runner = self.archive_runner(payload)
        extracted = unpack(self.storage(runner), runner, 'k.bin', {'passthrough': True, 'files': [meta]}, self.work())
        self.assertEqual((extracted / 'dir' / 'a.bin').read_bytes(), payload)
        self.assertEqual([c for c in runner.commands if c[0] == '7z'], [])
        with self.assertRaisesRegex(BackupError, 'Lote individual inválido'):
            runner = self.archive_runner(payload)
            unpack(self.storage(runner), runner, 'k.bin', {'passthrough': True, 'files': [meta, meta]}, self.work() / 'w2')
        with self.assertRaisesRegex(BackupError, 'Integridade divergente'):
            runner = self.archive_runner(b'tampered!!!')
            unpack(self.storage(runner), runner, 'k.bin', {'passthrough': True, 'files': [meta]}, self.work() / 'w3')
        metadata_only = {**meta, 'sha256': '', 'md5': ''}  # simple-mode index: size is all there is
        runner = self.archive_runner(payload)
        unpack(self.storage(runner), runner, 'k.bin', {'passthrough': True, 'files': [metadata_only]}, self.work() / 'w4')
