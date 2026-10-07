"""Medicare fee schedule lookup (price benchmark evidence).

The result is a benchmark, never proof of an error: a charge above the
Medicare amount is normal. The price check (Milestone 2) decides what to do
with it.
"""

import re
import sqlite3
from datetime import date
from typing import Literal

from pydantic import BaseModel

from bill_auditor.tables.releases import find_release

Setting = Literal["facility", "nonfacility"]

# Only these modifiers have their own rows in the fee schedule. Any other
# modifier (25, 59, ...) does not change the Medicare amount, so the global row
# (blank modifier) is the one that prices the service.
PRICING_MODIFIERS = {"26", "TC", "53"}

_LOCALITY_PATTERN = re.compile(r"^\d{7}$")


class FeeScheduleResult(BaseModel):
    status: Literal["found", "not_found", "no_release"]
    code: str
    modifier_requested: str
    pricing_modifier: str
    locality_id: str
    setting: Setting
    date_of_service: date
    amount_cents: int | None = None
    status_code: str | None = None
    pctc_indicator: str | None = None
    release_label: str | None = None
    source_part: str | None = None
    message: str


def pricing_modifier(modifier: str | None) -> str:
    modifier = (modifier or "").strip().upper()
    return modifier if modifier in PRICING_MODIFIERS else ""


def get_fee_schedule_amount(
    conn: sqlite3.Connection,
    code: str,
    modifier: str | None,
    locality_id: str,
    setting: Setting,
    date_of_service: date,
) -> FeeScheduleResult:
    """Look up the Medicare amount for one code in one locality and setting.

    `locality_id` is the 7-digit CMS id: 5-digit carrier + 2-digit locality
    (e.g. '0111205'), as listed in Attachment A of the file layout.
    """
    if not _LOCALITY_PATTERN.match(locality_id):
        raise ValueError(f"locality_id must be 7 digits (carrier + locality), got {locality_id!r}")

    code = code.strip().upper()
    used_modifier = pricing_modifier(modifier)
    base = dict(
        code=code,
        modifier_requested=(modifier or "").strip().upper(),
        pricing_modifier=used_modifier,
        locality_id=locality_id,
        setting=setting,
        date_of_service=date_of_service,
    )

    release = find_release(conn, "pfs", date_of_service)
    if release is None:
        return FeeScheduleResult(
            status="no_release", **base,
            message=f"Cannot check: no fee schedule release loaded for {date_of_service.isoformat()}.",
        )

    row = conn.execute(
        """SELECT nonfacility_cents, facility_cents, status_code, pctc_indicator, source_part
           FROM pfs_amounts
           WHERE release_id = ? AND carrier = ? AND locality = ? AND code = ? AND modifier = ?""",
        (release.release_id, locality_id[:5], locality_id[5:], code, used_modifier),
    ).fetchone()
    if row is None:
        return FeeScheduleResult(
            status="not_found", **base, release_label=release.label,
            message=f"No fee schedule row for {code} (modifier '{used_modifier}') in locality {locality_id}.",
        )

    amount = row["facility_cents"] if setting == "facility" else row["nonfacility_cents"]
    return FeeScheduleResult(
        status="found", **base,
        amount_cents=amount,
        status_code=row["status_code"],
        pctc_indicator=row["pctc_indicator"],
        release_label=release.label,
        source_part=row["source_part"],
        message="Medicare fee schedule amount (a benchmark, not an allowed amount for other insurers).",
    )
