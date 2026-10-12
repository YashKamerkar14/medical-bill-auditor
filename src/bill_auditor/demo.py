"""The public demo: a small reference database built from files committed to this repository.

The hosted demo cannot use CMS data (it may not be committed, and each user
downloads it themselves). It runs the rules-only system on the example bills,
which use only fake Z-codes, so the synthetic code-pair and unit-limit tables
(committed in synthetic_tables/) are all it needs. The fee-schedule and charge
releases exist, empty and labelled as such, so dates of service map to a
release as usual; the price benchmark then reports "cannot check" with that
label, instead of the demo pretending to have Medicare data.

Guardrail (CLAUDE.md, Operations): the demo accepts no bills at all, only the
committed examples, so it can never receive a real bill.
"""

import sqlite3
from pathlib import Path

from bill_auditor.db import connect
from bill_auditor.ingest.load_synthetic_ncci import load_synthetic_mue, load_synthetic_ptp
from bill_auditor.ingest.releases import ReleaseSpec, start_release
from bill_auditor.ingest.schema import create_schema
from bill_auditor.ingest.sources import CHARGES_2024, PFS_2026, SYNTHETIC_MUE, SYNTHETIC_PTP, SYNTHETIC_TABLE_DIR

NOT_LOADED = "DEMO ONLY: {} not loaded in the public demo (run it locally for Medicare data)"


def build_demo_db(path: Path) -> Path:
    """Create (or replace) the demo database at `path`: synthetic tables plus empty, labelled CMS releases."""
    path = Path(path)
    path.unlink(missing_ok=True)
    conn = connect(path)
    try:
        create_schema(conn)
        for real, what in ((PFS_2026, "Medicare fee schedule"), (CHARGES_2024, "Medicare average charges")):
            start_release(conn, ReleaseSpec(real.dataset, NOT_LOADED.format(what), real.effective_start, real.effective_end))
        load_synthetic_ptp(conn, SYNTHETIC_TABLE_DIR, SYNTHETIC_PTP)
        load_synthetic_mue(conn, SYNTHETIC_TABLE_DIR, SYNTHETIC_MUE)
        conn.commit()
    finally:
        conn.close()
    return path


def is_demo_db(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT 1 FROM releases WHERE label LIKE 'DEMO ONLY:%' LIMIT 1").fetchone() is not None
