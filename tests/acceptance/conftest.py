"""Acceptance tests run against the real tables under DATA_DIR.

They are skipped, not failed, when the data or the oracle files are missing,
so the unit suite runs anywhere without licensed CMS files.
"""

import pytest

from bill_auditor.config import ConfigError, load_paths
from bill_auditor.db import connect


def real_paths():
    try:
        paths = load_paths()
    except ConfigError as error:
        pytest.skip(f"DATA_DIR not usable: {error}")
    if not paths.sqlite_path.exists():
        pytest.skip("Reference database not built; run scripts/build_data.py")
    return paths


@pytest.fixture(scope="session")
def paths():
    return real_paths()


@pytest.fixture(scope="session")
def real_db(paths):
    conn = connect(paths.sqlite_path)
    yield conn
    conn.close()
