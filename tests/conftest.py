"""Mark every collected test ``unit`` or ``integration`` from its directory (``tests/unit``, ``tests/integration``)."""
import os
import tempfile
from pathlib import Path

import pytest

HERE = Path(__file__).parent


@pytest.fixture(scope='session', autouse=True)
def real_temporary_directory():
    """Resolve the default temp dir, as production does (macOS /var -> /private/var).

    Metadata directories refuse symlinks in their ancestry, so tests that build
    work directories under the default temp dir need its real path.
    """
    previous = tempfile.tempdir
    tempfile.tempdir = os.path.realpath(tempfile.gettempdir())
    yield
    tempfile.tempdir = previous


def pytest_collection_modifyitems(items):
    for item in items:
        parts = Path(str(item.path)).relative_to(HERE).parts
        if parts[0] in ('unit', 'integration'):
            item.add_marker(getattr(pytest.mark, parts[0]))
