import ast
import importlib
import unittest
from pathlib import Path

import sambinha
from sambinha import api

ORIGINS = {
    "run": ("engine", "run"), "recover": ("recovery", "recover"), "load": ("config", "load"),
    "validate": ("config_validation", "validate"), "Config": ("config", "Config"), "Job": ("config", "Job"),
    "BackupError": ("errors", "BackupError"), "ForeignStateError": ("errors", "ForeignStateError"),
    "UnsupportedStateError": ("errors", "UnsupportedStateError"), "STRATEGIES": ("engine", "STRATEGIES"),
    "StorageBackend": ("storage", "StorageBackend"), "Storage": ("storage", "Storage"),
}
PACKAGE = Path(sambinha.__file__).parent


def same_name_reexports(path):
    """Names imported as ``from .x import name as name`` (aliased to themselves)."""
    tree = ast.parse(path.read_text())
    return [alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
            for alias in node.names if alias.asname == alias.name]


class PublicApiTests(unittest.TestCase):
    def test_all_matches_the_documented_surface(self):
        self.assertEqual(set(api.__all__), set(ORIGINS))
        self.assertEqual(len(api.__all__), len(set(api.__all__)))

    def test_every_exported_name_is_the_object_from_its_defining_module(self):
        for name in api.__all__:
            with self.subTest(name=name):
                module, attribute = ORIGINS[name]
                origin = getattr(importlib.import_module(f"sambinha.{module}"), attribute)
                self.assertIs(getattr(api, name), origin)

    def test_package_root_exposes_only_the_version(self):
        public = {name for name in vars(sambinha) if not name.startswith("_")}
        modules = {name for name in public if isinstance(getattr(sambinha, name), type(api))}
        self.assertEqual(public - modules, set())
        self.assertIsInstance(sambinha.__version__, str)

    def test_no_aliased_same_name_reexports_in_engine_or_recovery(self):
        for module in ("engine", "recovery", "config"):
            with self.subTest(module=module):
                self.assertEqual(same_name_reexports(PACKAGE / f"{module}.py"), [])

    def test_guard_detects_an_aliased_reexport(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            sample = Path(directory) / "sample.py"
            sample.write_text("from .x import a as a\nfrom .y import b as c\nfrom .z import d\n")
            self.assertEqual(same_name_reexports(sample), ["a"])


if __name__ == "__main__":
    unittest.main()
