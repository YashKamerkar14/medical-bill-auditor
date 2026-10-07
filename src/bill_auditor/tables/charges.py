"""Real-world charge benchmark: what providers typically bill Medicare for a code.

Averages from CMS "Physician & Other Practitioners - by Geography and Service".
Like the fee schedule, this is context for a price comparison, never evidence
of an error: the average submitted charge is what providers ask for, and the
allowed amount is what Medicare accepts.
"""

import re
import sqlite3
from datetime import date
from typing import Literal

from pydantic import BaseModel

from bill_auditor.tables.fee_schedule import Setting
from bill_auditor.tables.releases import find_release

_PLACE_OF_SERVICE = {"facility": "F", "nonfacility": "O"}
_STATE_FIPS = re.compile(r"^[0-9A-Z]{2}$")


class ChargeBenchmarkResult(BaseModel):
    status: Literal["found", "not_found", "no_release"]
    code: str
    setting: Setting
    geography: str  # 'National' or a state FIPS code
    date_of_service: date
    geo_name: str | None = None
    description: str | None = None
    total_services: float | None = None
    avg_submitted_cents: int | None = None
    avg_allowed_cents: int | None = None
    avg_payment_cents: int | None = None
    release_label: str | None = None
    synthetic: bool | None = None
    message: str


def get_charge_benchmark(
    conn: sqlite3.Connection,
    code: str,
    setting: Setting,
    date_of_service: date,
    state_fips: str | None = None,
) -> ChargeBenchmarkResult:
    """National averages by default; state averages when `state_fips` (e.g. '06') is given.

    A missing state row is reported as not found rather than silently replaced
    by the national figure; the caller decides whether to ask for national.
    """
    if state_fips is not None and not _STATE_FIPS.match(state_fips):
        raise ValueError(f"state_fips must be a 2-character FIPS code, got {state_fips!r}")
    code = code.strip().upper()
    level, geo_code = ("State", state_fips) if state_fips else ("National", "")
    base = dict(code=code, setting=setting, geography=state_fips or "National", date_of_service=date_of_service)

    release = find_release(conn, "charges", date_of_service)
    if release is None:
        return ChargeBenchmarkResult(
            status="no_release", **base,
            message=f"Cannot check: no charge benchmark loaded for {date_of_service.isoformat()}.",
        )

    found = dict(release_label=release.label, synthetic=release.synthetic)
    row = conn.execute(
        """SELECT geo_name, description, total_services, avg_submitted_cents, avg_allowed_cents, avg_payment_cents
           FROM charge_benchmarks
           WHERE release_id = ? AND geo_level = ? AND geo_code = ? AND code = ? AND place_of_service = ?""",
        (release.release_id, level, geo_code, code, _PLACE_OF_SERVICE[setting]),
    ).fetchone()
    if row is None:
        return ChargeBenchmarkResult(
            status="not_found", **base, **found,
            message=f"No charge benchmark for {code} ({setting}, {base['geography']}).",
        )
    return ChargeBenchmarkResult(
        status="found", **base, **found, **dict(row),
        message="Average billed and Medicare-allowed amounts (a benchmark, not an error).",
    )
