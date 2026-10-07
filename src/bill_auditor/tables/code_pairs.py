"""Code-pair (PTP) lookup: is there an edit saying two codes should not be billed together?

The loaded PTP table is SYNTHETIC (fake Z-codes); results carry `synthetic` so
nothing downstream can present them as Medicare rules. The lookup reports
facts only; deciding whether a bill line is an error is the unbundling check's
job (Milestone 2).
"""

import sqlite3
from collections.abc import Iterable
from datetime import date
from typing import Literal

from pydantic import BaseModel

from bill_auditor.tables.releases import find_release


def _expand(prefix: str, first: int, last: int) -> set[str]:
    return {f"{prefix}{n}" for n in range(first, last + 1)}


# NCCI PTP-associated modifiers, from the NCCI Policy Manual 2026, Chapter I,
# Section E: anatomic, global surgery and other modifiers that "may be used
# under appropriate clinical circumstances to bypass an NCCI PTP edit".
# Modifiers 22, 76 and 77 are explicitly not on this list.
PTP_ASSOCIATED_MODIFIERS = frozenset(
    _expand("E", 1, 4) | {"FA"} | _expand("F", 1, 9) | {"TA"} | _expand("T", 1, 9)
    | {"LT", "RT", "LC", "LD", "RC", "LM", "RI"}
    | {"24", "25", "57", "58", "78", "79"}
    | {"27", "59", "91", "XE", "XS", "XP", "XU"}
)

MODIFIER_INDICATOR_MEANING = {
    "0": "NCCI PTP-associated modifiers cannot be used to bypass the edit",
    "1": "NCCI PTP-associated modifiers may be used to bypass the edit under appropriate circumstances",
}


class CodePairResult(BaseModel):
    status: Literal["edit", "no_edit", "no_release"]
    code_a: str
    code_b: str
    date_of_service: date
    column1: str | None = None
    column2: str | None = None
    modifier_indicator: str | None = None
    modifier_indicator_meaning: str | None = None
    effective_date: date | None = None
    deletion_date: date | None = None
    rationale: str | None = None
    bypass_modifiers_on_column1: list[str] = []
    bypass_modifiers_on_column2: list[str] = []
    release_label: str | None = None
    synthetic: bool | None = None
    message: str


def _bypass(modifiers: Iterable[str]) -> list[str]:
    return sorted({m.strip().upper() for m in modifiers} & PTP_ASSOCIATED_MODIFIERS)


def check_code_pair(
    conn: sqlite3.Connection,
    code_a: str,
    code_b: str,
    date_of_service: date,
    modifiers_a: Iterable[str] = (),
    modifiers_b: Iterable[str] = (),
) -> CodePairResult:
    """Find the edit (in either column order) that applies to these codes on this date."""
    code_a, code_b = code_a.strip().upper(), code_b.strip().upper()
    modifiers_a, modifiers_b = list(modifiers_a), list(modifiers_b)
    base = dict(code_a=code_a, code_b=code_b, date_of_service=date_of_service)

    release = find_release(conn, "ptp", date_of_service)
    if release is None:
        return CodePairResult(
            status="no_release", **base,
            message=f"Cannot check: no code-pair table loaded for {date_of_service.isoformat()}.",
        )

    dos = date_of_service.isoformat()
    row = conn.execute(
        """SELECT column1, column2, modifier_indicator, effective_date, deletion_date, rationale
           FROM ptp_edits
           WHERE release_id = ?
             AND ((column1 = ? AND column2 = ?) OR (column1 = ? AND column2 = ?))
             AND effective_date <= ? AND (deletion_date IS NULL OR ? < deletion_date)""",
        (release.release_id, code_a, code_b, code_b, code_a, dos, dos),
    ).fetchone()  # the loader guarantees at most one edit applies per pair and date

    found = dict(release_label=release.label, synthetic=release.synthetic)
    if row is None:
        return CodePairResult(
            status="no_edit", **base, **found,
            message=f"No code-pair edit for {code_a} and {code_b} on {dos}.",
        )

    on_a, on_b = _bypass(modifiers_a), _bypass(modifiers_b)
    column1_is_a = row["column1"] == code_a
    return CodePairResult(
        status="edit", **base, **found,
        column1=row["column1"],
        column2=row["column2"],
        modifier_indicator=row["modifier_indicator"],
        modifier_indicator_meaning=MODIFIER_INDICATOR_MEANING.get(row["modifier_indicator"]),
        effective_date=date.fromisoformat(row["effective_date"]),
        deletion_date=date.fromisoformat(row["deletion_date"]) if row["deletion_date"] else None,
        rationale=row["rationale"],
        bypass_modifiers_on_column1=on_a if column1_is_a else on_b,
        bypass_modifiers_on_column2=on_b if column1_is_a else on_a,
        message=(
            f"{'SYNTHETIC ' if release.synthetic else ''}code-pair edit: "
            f"{row['column2']} is not normally billed with {row['column1']}."
        ),
    )
