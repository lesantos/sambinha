"""Three-tier compression: extension lists, classification, grouping, commands and validation."""
import tomllib
from dataclasses import fields, replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from sambinha import archives
from sambinha.batches import plan
from sambinha.cli_options import OVERRIDES
from sambinha.config import EXTENSION_OPTIONS, Job, load
from sambinha.config_validation import validate
from sambinha.context import Context
from sambinha.errors import BackupError, ConfigError
from sambinha.extensions import (
    DEFAULT_COMPRESSED_EXTENSIONS,
    DEFAULT_TEXT_EXTENSIONS,
    NONE,
    extension_of,
    normalize_extensions,
)
from sambinha.inventory import scan
from sambinha.profiles import COMPRESSED, OTHER, TEXT, classify, plan_groups, summary
from tests.helpers import FakeRunner, Sandbox

ROOT = Path(__file__).resolve().parents[2]


def job(**values):
    return Job("j", Path("/s"), "/d", logic="batch", **values)


def files(*names, size=10):
    return [SimpleNamespace(path=name, size=size) for name in names]


class ClassificationTests(Sandbox):
    def test_extension_table(self):
        for path, expected in (("a.txt", "txt"), ("A.JPG", "jpg"), ("a.tar.gz", "gz"), (".bashrc", NONE),
                               ("Makefile", NONE), ("dir.d/file", NONE), ("dir.d/.hidden", NONE),
                               ("name.", NONE), ("x/y.z/archive.7Z", "7z"), ("..x", "x"), ("a.b.", NONE)):
            with self.subTest(path=path):
                self.assertEqual(extension_of(path), expected)

    def test_classify_defaults(self):
        j = job()
        for path, kind in (("notes.MD", TEXT), ("README", TEXT), (".bashrc", TEXT), ("photo.JPG", COMPRESSED),
                           ("a.tar.gz", COMPRESSED), ("tool.exe", OTHER), ("disk.iso", OTHER), ("dir.d/file", TEXT),
                           ("x.unknownext", OTHER), ("main.ts", TEXT)):
            with self.subTest(path=path):
                self.assertEqual(classify(path, j), kind)

    def test_none_keyword_can_move_or_disappear(self):
        text = tuple(e for e in DEFAULT_TEXT_EXTENSIONS if e != NONE)
        self.assertEqual(classify("README", job(compression_text_extensions=text)), OTHER)
        self.assertEqual(classify("README", job(compression_text_extensions=text,
                                                compression_compressed_extensions=(*DEFAULT_COMPRESSED_EXTENSIONS, NONE))),
                         COMPRESSED)

    def test_normalization(self):
        self.assertEqual(normalize_extensions([" .TXT ", "Jpg", "<NONE>", "md"], "x"), ("txt", "jpg", NONE, "md"))

    def test_default_lists_are_clean_and_representative(self):
        text, compressed = set(DEFAULT_TEXT_EXTENSIONS), set(DEFAULT_COMPRESSED_EXTENSIONS)
        self.assertFalse(text & compressed)
        self.assertEqual((len(text), len(compressed)), (len(DEFAULT_TEXT_EXTENSIONS), len(DEFAULT_COMPRESSED_EXTENSIONS)))
        self.assertIn(NONE, text)
        self.assertNotIn(NONE, compressed)
        for entry in (text | compressed) - {NONE}:
            with self.subTest(entry=entry):
                self.assertEqual(entry, entry.strip().lower())
                self.assertFalse(set(entry) & set(" ./\\\t"))
                self.assertTrue(entry)
        self.assertGreaterEqual(text, {"txt", "md", "py", "json", "csv", "html", "c", "go", "rs", "sql", "log", "ts", "svg"})
        self.assertGreaterEqual(compressed, {"jpg", "png", "mp4", "mkv", "mp3", "flac", "zip", "7z", "gz", "xz", "docx",
                                             "xlsx", "pdf", "epub", "jar", "deb", "webp", "heic"})
        for other in ("exe", "dll", "so", "bin", "iso", "sqlite", "db", "wav", "bmp", "tiff", "ttf", "tar", "pyc", "vmdk"):
            self.assertNotIn(other, text | compressed)


class PlanGroupsTests(Sandbox):
    def test_order_empty_groups_and_stable_inside(self):
        items = files("z.jpg", "b.txt", "a.exe", "c.png", "a.txt", "d.bin")
        groups = plan_groups(job(compression_level=5, compression_text_level=2, compression_compressed_level=0), items)
        self.assertEqual([(g.kind, g.level, [f.path for f in g.files]) for g in groups],
                         [(TEXT, 2, ["b.txt", "a.txt"]), (OTHER, 5, ["a.exe", "d.bin"]), (COMPRESSED, 0, ["z.jpg", "c.png"])])
        self.assertEqual(summary(groups)[OTHER], {"files": 2, "bytes": 20, "level": 5})

    def test_single_group_and_skipped_empty_groups(self):
        self.assertEqual([g.kind for g in plan_groups(job(), files("a.txt", "b.md"))], [TEXT])
        self.assertEqual([g.kind for g in plan_groups(job(), files("a.zip", "b.jpg"))], [COMPRESSED])
        self.assertEqual([g.kind for g in plan_groups(job(), files("a.jpg", "b.exe"))], [OTHER, COMPRESSED])
        self.assertEqual(plan_groups(job(), []), [])


class CompressCommandTests(Sandbox):
    def setUp(self):
        super().setUp()
        self.job = replace(self.config.jobs[0], logic="batch", compression_level=4, compression_text_level=2,
                           compression_compressed_level=0, compression_threads=3,
                           check_source_disk=False, check_destination_disk=False)

    def run_compress(self, names, job=None, runner=None):
        job = job or self.job
        for name in names:
            self.file(name, b"x" * 7)
        found = scan(self.source)
        batch = plan(found, 10 ** 6)[0]
        listing = "\n\n".join(f"Path = ./{f.path}\nSize = {f.size}\nAttributes = A" for f in batch.files)
        runner = runner or FakeRunner({"7z l": listing})
        ctx = Context(replace(self.config, jobs=(job,)), job, runner, Mock(), found, 1000)
        archive = archives.compress(ctx, batch, self.root)
        return runner, batch, archive

    def test_one_command_per_group_on_the_same_archive(self):
        runner, _batch, archive = self.run_compress(["a.jpg", "b.txt", "c.exe", "d.png", "README"])
        adds = runner.calls("7z", "a")
        self.assertEqual(len(adds), 3)
        self.assertEqual([sorted(c[c.index("--") + 1:]) for c in adds], [["README", "b.txt"], ["c.exe"], ["a.jpg", "d.png"]])
        self.assertEqual([[a for a in c if a.startswith("-mx")] for c in adds], [["-mx=2"], ["-mx=4"], ["-mx=0"]])
        for command in adds:
            self.assertIn("-mmt=3", command)
            self.assertIn(str(archive), command)
            self.assertEqual(command[-len(command) + command.index(str(archive)) + 1], "--")
        self.assertEqual(len(runner.calls("7z", "t")), 1)
        self.assertLess(runner.commands.index(adds[-1]), runner.commands.index(runner.calls("7z", "t")[0]))

    def test_single_group_runs_exactly_one_command(self):
        for names in (["a.txt", "b.md"], ["a.zip", "b.jpg"], ["a.bin"]):
            with self.subTest(names=names):
                runner, _batch, _archive = self.run_compress(names, job=replace(self.job))
                self.assertEqual(len(runner.calls("7z", "a")), 1)
                for name in names:
                    (self.source / name).unlink()

    def test_listfile_fallback_is_per_command_with_distinct_names(self):
        for budget in (1,):
            original = archives.ARGUMENT_BUDGET
            archives.ARGUMENT_BUDGET = budget
            self.addCleanup(setattr, archives, "ARGUMENT_BUDGET", original)
        runner, _batch, _archive = self.run_compress(["a.txt", "b.exe", "c.zip"])
        listfiles = [next(a for a in c if a.startswith("@")) for c in runner.calls("7z", "a")]
        self.assertEqual(len(set(listfiles)), 3)
        self.assertEqual([Path(p[1:]).read_text() for p in listfiles], ["./a.txt\n", "./b.exe\n", "./c.zip\n"])

    def test_trailing_space_rule_applies_per_command(self):
        archives.ARGUMENT_BUDGET, original = 1, archives.ARGUMENT_BUDGET
        self.addCleanup(setattr, archives, "ARGUMENT_BUDGET", original)
        with self.assertRaises(BackupError):
            self.run_compress(["a.txt", "b.exe ", "c.zip"])

    def test_progress_scopes_partition_the_compression_stage(self):
        job = self.job
        for name, size in (("a.txt", 10), ("b.exe", 30), ("c.zip", 60)):
            self.file(name, b"x" * size)
        found = scan(self.source)
        batch = plan(found, 10 ** 6)[0]
        scopes = []
        progress = Mock()
        progress.scope.side_effect = lambda a, b: scopes.append((round(a, 6), round(b, 6))) or Mock()
        listing = "\n\n".join(f"Path = ./{f.path}\nSize = {f.size}\nAttributes = A" for f in batch.files)
        ctx = Context(replace(self.config, jobs=(job,)), job, FakeRunner({"7z l": listing}), Mock(), found, 1000)
        ctx.progress = progress
        archives.compress(ctx, batch, self.root)
        compression = scopes[1:4]
        self.assertEqual(compression, [(0.1, 0.16), (0.16, 0.34), (0.34, 0.7)])

    def test_temporary_bytes_bound(self):
        for name, size in (("a.txt", 100), ("b.exe", 300), ("c.zip", 600)):
            self.file(name, b"x" * size)
        batch = plan(scan(self.source), 10 ** 6)[0]
        single = archives.temporary_bytes(batch)
        self.assertEqual(archives.temporary_bytes(batch, replace(self.job, compression_text_extensions=("txt", "exe"),
                                                                   compression_compressed_extensions=("zip",))) - single, 400)
        self.assertEqual(archives.temporary_bytes(batch, self.job) - single, 1000 - 600)
        self.assertEqual(archives.temporary_bytes(batch, replace(self.job, compression_text_extensions=("txt", "exe", "zip"),
                                                                   compression_compressed_extensions=())), single)


class ConfigurationTests(Sandbox):
    def test_defaults(self):
        j = Job("j", Path("/s"), "/d")
        self.assertEqual((j.compression_level, j.compression_text_level, j.compression_compressed_level), (1, 1, 0))
        self.assertIs(j.compression_text_extensions, DEFAULT_TEXT_EXTENSIONS)
        self.assertIsInstance(j.compression_compressed_extensions, tuple)

    def test_invalid_levels_and_lists_on_hand_built_jobs(self):
        base = self.config.jobs[0]
        bad = []
        for name in ("compression_level", "compression_text_level", "compression_compressed_level"):
            bad += [{name: -1}, {name: 10}, {name: True}, {name: 1.0}, {name: "1"}]
        bad += [{"compression_text_extensions": "txt"}, {"compression_text_extensions": ["txt"]},
                {"compression_text_extensions": ("txt", 1)}, {"compression_text_extensions": ("a.b",)},
                {"compression_text_extensions": ("",)}, {"compression_text_extensions": ("a b",)},
                {"compression_text_extensions": ("TXT",)}, {"compression_text_extensions": (".txt",)},
                {"compression_text_extensions": ("txt", "txt")}, {"compression_compressed_extensions": ("a/b",)},
                {"compression_text_extensions": ("txt", "zip"), "compression_compressed_extensions": ("zip",)},
                {"compression_text_extensions": (NONE,), "compression_compressed_extensions": (NONE,)}]
        for values in bad:
            with self.subTest(values=values), self.assertRaises(ConfigError):
                validate(replace(self.config, jobs=(replace(base, **values),)))
        for level in (0, 9):
            validate(replace(self.config, jobs=(replace(base, compression_text_level=level),)))

    def toml(self, body):
        path = self.root / "c.toml"
        path.write_text(f'[[jobs]]\nname="t"\nsource="source"\ndestination="destination"\nlogic="batch"\n{body}\n')
        return path

    def test_load_normalizes_into_tuples(self):
        config = load(self.toml('compression_text_extensions = [" .TXT", "Md", "<none>"]\n'
                                'compression_compressed_extensions = [".ZIP", "7z"]\n'
                                'compression_text_level = 3\ncompression_compressed_level = 2'))
        j = config.jobs[0]
        self.assertEqual((j.compression_text_extensions, j.compression_compressed_extensions),
                         (("txt", "md", NONE), ("zip", "7z")))
        self.assertEqual((j.compression_text_level, j.compression_compressed_level), (3, 2))

    def test_load_rejects_bad_values(self):
        for body in ('compression_text_extensions = "txt"', 'compression_text_extensions = ["a", 1]',
                     'compression_text_extensions = ["a.b"]', 'compression_text_extensions = [""]',
                     'compression_text_extensions = ["a b"]', 'compression_text_extensions = ["a", ".A"]',
                     'compression_text_extensions = ["zip"]\ncompression_compressed_extensions = ["ZIP"]',
                     'compression_text_extensions = ["<none>"]\ncompression_compressed_extensions = ["<none>"]',
                     'compression_text_level = 10', 'compression_compressed_level = true',
                     'compression_level = "1"'):
            with self.subTest(body=body), self.assertRaises(ConfigError):
                load(self.toml(body))

    def test_new_options_are_job_only_like_compression_level(self):
        for name in ("compression_level", "compression_text_level", "compression_compressed_level",
                     "compression_text_extensions", "compression_compressed_extensions"):
            path = self.root / "b.toml"
            path.write_text(f'[backup]\n{name} = 1\n[[jobs]]\nname="t"\nsource="source"\ndestination="destination"\n')
            with self.subTest(name=name), self.assertRaises(ConfigError):
                load(path)

    def test_cli_options_are_job_fields_and_lists_are_not_exposed(self):
        names = {f.name for f in fields(Job)}
        self.assertLessEqual({"compression_text_level", "compression_compressed_level"}, set(OVERRIDES))
        self.assertTrue(set(OVERRIDES) - {"job", "dry_run"} <= names | {f.name for f in fields(type(self.config))})
        for name in EXTENSION_OPTIONS:
            self.assertNotIn(name, OVERRIDES)
        for name in ("compression_level", "compression_text_level", "compression_compressed_level"):
            self.assertIs(OVERRIDES[name].type, int)

    def test_example_files_round_trip_to_the_defaults(self):
        for name in ("sambinha.example.toml", "sambinha.example.pt-BR.toml"):
            data = tomllib.loads((ROOT / name).read_text(encoding="utf-8"))
            batch = [j for j in data["jobs"] if j["logic"] == "batch"]
            self.assertTrue(batch)
            for item in batch:
                with self.subTest(name=name, job=item["name"]):
                    self.assertEqual((item["compression_text_level"], item["compression_compressed_level"],
                                      item["compression_level"]), (1, 0, 1))
            first = batch[0]
            self.assertEqual(normalize_extensions(first["compression_text_extensions"], "t"), DEFAULT_TEXT_EXTENSIONS)
            self.assertEqual(normalize_extensions(first["compression_compressed_extensions"], "c"),
                             DEFAULT_COMPRESSED_EXTENSIONS)
