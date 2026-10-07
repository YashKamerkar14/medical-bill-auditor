"""Load CMS "Medicare Physician & Other Practitioners - by Geography and Service".

Real, public data (no licence click-through; catalogued as public on data.cms.gov):
for each code, setting and geography, the average amount providers submitted
(billed), the average Medicare allowed amount and the average payment. Used to
make synthetic bills look like real billing and as context for the price
benchmark. It holds no patient or provider identifiers; CMS suppresses rows
with fewer than 11 services.

File facts checked on the data-year-2024 file (MUP_PHY_R26_P05_V10_D24_Geo.csv):
- UTF-8 with a byte-order mark, a header row, 15 columns, 268,350 rows.
- Geography levels "National" (geo code blank) and "State" (2-character FIPS code).
- 5 "State" rows have a blank state; they cannot be attributed and are skipped,
  and the skip count is reported.
- Averages have up to 8 decimals; Tot_Srvcs can be fractional (drug units).
"""

import csv
import sqlite3
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

from bill_auditor.ingest.releases import ReleaseSpec, is_already_loaded, record_file, source_file, start_release

EXPECTED_COLUMNS = [
    "Rndrng_Prvdr_Geo_Lvl", "Rndrng_Prvdr_Geo_Cd", "Rndrng_Prvdr_Geo_Desc", "HCPCS_Cd", "HCPCS_Desc",
    "HCPCS_Drug_Ind", "Place_Of_Srvc", "Tot_Rndrng_Prvdrs", "Tot_Benes", "Tot_Srvcs", "Tot_Bene_Day_Srvcs",
    "Avg_Sbmtd_Chrg", "Avg_Mdcr_Alowd_Amt", "Avg_Mdcr_Pymt_Amt", "Avg_Mdcr_Stdzd_Amt",
]


class ChargesFormatError(ValueError):
    """The file does not match the layout this loader was written against."""


@dataclass(frozen=True)
class ChargesSummary:
    rows: int
    skipped_blank_state: int


def average_to_cents(text: str, where: str) -> int:
    """'881.4666' -> 88147 cents, rounding half up (an average, so rounding is expected)."""
    try:
        return int((Decimal(text) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except InvalidOperation as error:
        raise ChargesFormatError(f"{where}: bad amount {text!r}") from error


def _rows(path: Path, skipped: list[int]):
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != EXPECTED_COLUMNS:
            raise ChargesFormatError(f"{path.name}: unexpected columns {reader.fieldnames}")
        for line_no, row in enumerate(reader, start=2):
            where = f"{path.name} line {line_no}"
            level = row["Rndrng_Prvdr_Geo_Lvl"]
            if level not in ("National", "State"):
                raise ChargesFormatError(f"{where}: unknown geography level {level!r}")
            if level == "State" and not row["Rndrng_Prvdr_Geo_Cd"]:
                skipped[0] += 1
                continue
            if row["Place_Of_Srvc"] not in ("F", "O"):
                raise ChargesFormatError(f"{where}: unknown place of service {row['Place_Of_Srvc']!r}")
            try:
                services = float(row["Tot_Srvcs"])
            except ValueError as error:
                raise ChargesFormatError(f"{where}: bad service count {row['Tot_Srvcs']!r}") from error
            yield (
                level,
                row["Rndrng_Prvdr_Geo_Cd"],
                row["Rndrng_Prvdr_Geo_Desc"],
                row["HCPCS_Cd"].strip().upper(),
                row["HCPCS_Desc"],
                row["Place_Of_Srvc"],
                services,
                average_to_cents(row["Avg_Sbmtd_Chrg"], where),
                average_to_cents(row["Avg_Mdcr_Alowd_Amt"], where),
                average_to_cents(row["Avg_Mdcr_Pymt_Amt"], where),
            )


def load_charges(conn: sqlite3.Connection, csv_path: Path, spec: ReleaseSpec) -> ChargesSummary | None:
    """Load the file as one release; None if the same file is already loaded."""
    files = [source_file("geo_service", csv_path)]
    if is_already_loaded(conn, spec, files):
        return None

    skipped = [0]
    with conn:
        release_id = start_release(conn, spec)
        try:
            conn.executemany(
                """INSERT INTO charge_benchmarks
                   (release_id, geo_level, geo_code, geo_name, code, description, place_of_service,
                    total_services, avg_submitted_cents, avg_allowed_cents, avg_payment_cents)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                ((release_id, *row) for row in _rows(csv_path, skipped)),
            )
        except sqlite3.IntegrityError as error:
            raise ChargesFormatError(f"{csv_path.name}: repeated (geography, code, place of service)") from error
        loaded = conn.execute(
            "SELECT COUNT(*) FROM charge_benchmarks WHERE release_id = ?", (release_id,)
        ).fetchone()[0]
        record_file(conn, release_id, files[0], loaded)
    return ChargesSummary(rows=loaded, skipped_blank_state=skipped[0])
