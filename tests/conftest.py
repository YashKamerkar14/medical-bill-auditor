"""Shared test fixtures. Everything here uses fake codes (ZZxxx), never CMS rows."""

import io
import sqlite3
import zipfile
from datetime import date
from pathlib import Path

import pytest

from bill_auditor.db import connect
from bill_auditor.ingest.load_pfs import load_pfs
from bill_auditor.ingest.releases import ReleaseSpec
from bill_auditor.ingest.schema import create_schema

FIXTURES = Path(__file__).parent / "fixtures"

FAKE_PFS_SPEC = ReleaseSpec(
    dataset="pfs",
    label="PFS 2026 nonQP A-D (test)",
    effective_start=date(2026, 10, 1),
    effective_end=date(2026, 12, 31),
)


def write_pfrev_zip(directory: Path, letter: str, nonqp_text: str) -> Path:
    """Build an outer ZIP shaped like the real PFREV downloads.

    Outer ZIP -> folder -> `*_QP.zip` and `*_nonQP.zip` -> layout PDF + .txt.
    The QP copy has different amounts, so a loader that read the wrong
    variant would fail the amount tests.
    """
    def inner_zip(text: str) -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as inner:
            inner.writestr(f"PF26P{letter}.pdf", b"%PDF-1.4 fake layout")
            inner.writestr(f"PFREV26{letter}.txt", text)
        return buffer.getvalue()

    qp_text = nonqp_text.replace('"0000', '"0009')  # different, still well-formed
    path = directory / f"pfrev26{letter.lower()}-test.zip"
    with zipfile.ZipFile(path, "w") as outer:
        outer.writestr(f"PFREV26{letter}/PFREV26{letter}_QP.zip", inner_zip(qp_text))
        outer.writestr(f"PFREV26{letter}/PFREV26{letter}_nonQP.zip", inner_zip(nonqp_text))
    return path


@pytest.fixture
def pfs_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "pfs"
    directory.mkdir()
    for letter in "ABCD":
        text = (FIXTURES / "pfs" / f"pfrev_{letter.lower()}.txt").read_text()
        write_pfrev_zip(directory, letter, text)
    return directory


@pytest.fixture
def empty_db(tmp_path: Path) -> sqlite3.Connection:
    conn = connect(tmp_path / "test.sqlite")
    create_schema(conn)
    yield conn
    conn.close()


@pytest.fixture
def pfs_db(empty_db: sqlite3.Connection, pfs_dir: Path) -> sqlite3.Connection:
    load_pfs(empty_db, pfs_dir, FAKE_PFS_SPEC, year=2026, expected_parts="ABCD")
    return empty_db


@pytest.fixture
def audit_db_path(tmp_path: Path, pfs_dir: Path) -> Path:
    """A complete fake reference database on disk: fake PFS (ZZ codes), fake charges, synthetic PTP/MUE."""
    from bill_auditor.ingest.load_charges import load_charges
    from bill_auditor.ingest.load_synthetic_ncci import load_synthetic_mue, load_synthetic_ptp
    from bill_auditor.ingest.sources import SYNTHETIC_MUE, SYNTHETIC_PTP, SYNTHETIC_TABLE_DIR

    path = tmp_path / "audit.sqlite"
    conn = connect(path)
    create_schema(conn)
    load_pfs(conn, pfs_dir, FAKE_PFS_SPEC, year=2026, expected_parts="ABCD")
    load_charges(conn, FIXTURES / "charges" / "geo_service.csv",
                 ReleaseSpec("charges", "Charges (test)", date(2026, 10, 1), date(2026, 12, 31)))
    load_synthetic_ptp(conn, SYNTHETIC_TABLE_DIR, SYNTHETIC_PTP)
    load_synthetic_mue(conn, SYNTHETIC_TABLE_DIR, SYNTHETIC_MUE)
    conn.close()
    return path


@pytest.fixture
def audit_db(audit_db_path: Path) -> sqlite3.Connection:
    conn = connect(audit_db_path)
    yield conn
    conn.close()
