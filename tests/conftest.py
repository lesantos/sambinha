"""Mark every collected test ``unit`` or ``integration`` from its directory (``tests/unit``, ``tests/integration``)."""
from pathlib import Path

import pytest

HERE = Path(__file__).parent


def pytest_collection_modifyitems(items):
    for item in items:
        parts = Path(str(item.path)).relative_to(HERE).parts
        if parts[0] in ('unit', 'integration'):
            item.add_marker(getattr(pytest.mark, parts[0]))
