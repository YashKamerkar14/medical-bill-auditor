"""Unit-limit (MUE) lookup: the most units of a code normally billed for one patient.

The loaded MUE table is SYNTHETIC (fake Z-codes); results carry `synthetic`.
A code missing from the table means "no published limit", which the quantity
check reports as "cannot check", never as "pass".
"""

import sqlite3
from datetime import date
from typing import Literal

from pydantic import BaseModel

from bill_auditor.tables.releases import find_release

# Meanings from the NCCI Policy Manual 2026, Chapter I, Section V.
MAI_MEANING = {
    "1": "Claim line edit: units on each claim line are compared to the limit",
    "2": "Date of service edit, per day edit based on policy (absolute)",
    "3": "Date of service edit, per day edit based on clinical benchmarks",
}


class UnitLimitResult(BaseModel):
    status: Literal["limit", "not_published", "no_release"]
    code: str
    date_of_service: date
    mue_value: int | None = None
    mai: str | None = None
    mai_meaning: str | None = None
    rationale: str | None = None
    release_label: str | None = None
    synthetic: bool | None = None
    message: str


def get_unit_limit(conn: sqlite3.Connection, code: str, date_of_service: date) -> UnitLimitResult:
    code = code.strip().upper()
    base = dict(code=code, date_of_service=date_of_service)

    release = find_release(conn, "mue", date_of_service)
    if release is None:
        return UnitLimitResult(
            status="no_release", **base,
            message=f"Cannot check: no unit-limit table loaded for {date_of_service.isoformat()}.",
        )

    found = dict(release_label=release.label, synthetic=release.synthetic)
    row = conn.execute(
        "SELECT mue_value, mai, rationale FROM mue_limits WHERE release_id = ? AND code = ?",
        (release.release_id, code),
    ).fetchone()
    if row is None:
        return UnitLimitResult(
            status="not_published", **base, **found,
            message=f"No published unit limit for {code}: cannot check quantity.",
        )

    return UnitLimitResult(
        status="limit", **base, **found,
        mue_value=row["mue_value"],
        mai=row["mai"],
        mai_meaning=MAI_MEANING[row["mai"]],
        rationale=row["rationale"],
        message=f"{'SYNTHETIC ' if release.synthetic else ''}unit limit for {code}: {row['mue_value']}.",
    )
