"""Supported public surface of Sambinha: the only module external code should import from.

Everything else (``engine``, ``recovery``, ``context``, ...) is internal and may change between
releases; names are re-exported here from the module that defines them.
"""
from .config import Config, Job, load
from .config_validation import validate
from .engine import STRATEGIES, run
from .errors import BackupError, ForeignStateError, UnsupportedStateError
from .recovery import recover
from .storage import Storage, StorageBackend

__all__ = [
    "STRATEGIES",
    "BackupError",
    "Config",
    "ForeignStateError",
    "Job",
    "Storage",
    "StorageBackend",
    "UnsupportedStateError",
    "load",
    "recover",
    "run",
    "validate",
]
