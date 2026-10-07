"""Load the synthetic PTP and MUE tables (synthetic_tables/*.csv) into SQLite.

These are hand-written rules over fake Z-codes, used instead of the
licence-gated CMS tables (docs/decisions/0005-synthetic-ncci-tables.md).
Because a typo in a hand-written rule table would silently produce wrong
flags, every row is validated and any problem stops the load.
"""

import csv
import re
import sqlite3
from datetime import date
from pathlib import Path

from bill_auditor.ingest.releases import ReleaseSpec, is_already_loaded, record_file, source_file, start_release

_FAKE_CODE = re.compile(r"^Z\d{4}$")


class SyntheticTableError(ValueError):
    """A synthetic table breaks one of its own rules."""


def _read(path: Path, columns: list[str]) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != columns:
            raise SyntheticTableError(f"{path.name}: expected columns {columns}, got {reader.fieldnames}")
        return [{k: v.strip() for k, v in row.items()} for row in reader]


def read_catalogue(path: Path) -> dict[str, str]:
    """code -> description, validated: fake Z-codes only, no duplicates, a description for each."""
    rows = _read(path, ["code", "description"])
    codes = [row["code"] for row in rows]
    for row in rows:
        if not _FAKE_CODE.match(row["code"]):
            raise SyntheticTableError(f"{path.name}: {row['code']!r} is not a fake code (Z + 4 digits)")
        if not row["description"]:
            raise SyntheticTableError(f"{path.name}: {row['code']} has no description")
    if len(set(codes)) != len(codes):
        raise SyntheticTableError(f"{path.name}: duplicate codes")
    return {row["code"]: row["description"] for row in rows}


def read_codes(path: Path) -> set[str]:
    return set(read_catalogue(path))


def _iso(value: str, where: str) -> str:
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as error:
        raise SyntheticTableError(f"{where}: bad date {value!r}") from error


def read_ptp(path: Path, codes: set[str]) -> list[tuple]:
    rows = _read(path, ["column1", "column2", "effective_date", "deletion_date", "modifier_indicator", "rationale"])
    edits = []
    for line_no, row in enumerate(rows, start=2):  # line 1 is the header
        where = f"{path.name} line {line_no}"
        c1, c2 = row["column1"], row["column2"]
        for code in (c1, c2):
            if code not in codes:
                raise SyntheticTableError(f"{where}: {code} is not in codes.csv")
        if c1 == c2:
            raise SyntheticTableError(f"{where}: a code cannot be paired with itself")
        effective = _iso(row["effective_date"], where)
        deletion = _iso(row["deletion_date"], where) if row["deletion_date"] else None
        indicator = row["modifier_indicator"]
        if indicator not in ("0", "1", "9"):
            raise SyntheticTableError(f"{where}: modifier indicator must be 0, 1 or 9")
        if deletion is not None and deletion < effective:
            raise SyntheticTableError(f"{where}: deleted before it took effect")
        # Manual I.E: indicator 9 is used for pairs whose deletion date equals the effective date.
        if (indicator == "9") != (deletion == effective):
            raise SyntheticTableError(f"{where}: indicator 9 if and only if deletion date equals effective date")
        if not row["rationale"]:
            raise SyntheticTableError(f"{where}: rationale is required")
        edits.append((c1, c2, effective, deletion, indicator, row["rationale"]))

    _check_no_overlaps(edits, path.name)
    return edits


def _check_no_overlaps(edits: list[tuple], file_name: str) -> None:
    """At most one edit may apply to an unordered code pair on any date."""
    by_pair: dict[frozenset, list[tuple[str, str]]] = {}
    for c1, c2, effective, deletion, _, _ in edits:
        by_pair.setdefault(frozenset((c1, c2)), []).append((effective, deletion or "9999-12-31"))
    for pair, windows in by_pair.items():
        windows.sort()
        for (_, end), (start, _) in zip(windows, windows[1:]):
            if start < end:
                raise SyntheticTableError(f"{file_name}: overlapping edits for pair {sorted(pair)}")


def read_mue(path: Path, codes: set[str]) -> list[tuple]:
    rows = _read(path, ["code", "mue_value", "mai", "rationale"])
    limits, seen = [], set()
    for line_no, row in enumerate(rows, start=2):
        where = f"{path.name} line {line_no}"
        code = row["code"]
        if code not in codes:
            raise SyntheticTableError(f"{where}: {code} is not in codes.csv")
        if code in seen:
            raise SyntheticTableError(f"{where}: duplicate code {code}")
        seen.add(code)
        if not row["mue_value"].isdigit() or int(row["mue_value"]) < 1:
            raise SyntheticTableError(f"{where}: MUE value must be a positive whole number")
        if row["mai"] not in ("1", "2", "3"):
            raise SyntheticTableError(f"{where}: MAI must be 1, 2 or 3")
        if not row["rationale"]:
            raise SyntheticTableError(f"{where}: rationale is required")
        limits.append((code, int(row["mue_value"]), row["mai"], row["rationale"]))
    return limits


def load_synthetic_ptp(conn: sqlite3.Connection, table_dir: Path, spec: ReleaseSpec) -> int | None:
    """Load ptp_edits.csv; returns rows loaded, or None if unchanged since the last load."""
    if not spec.synthetic:
        raise ValueError("Synthetic tables must be loaded under a synthetic release")
    files = [source_file("codes", table_dir / "codes.csv"), source_file("ptp", table_dir / "ptp_edits.csv")]
    if is_already_loaded(conn, spec, files):
        return None
    catalogue = read_catalogue(table_dir / "codes.csv")
    edits = read_ptp(table_dir / "ptp_edits.csv", set(catalogue))
    with conn:
        release_id = start_release(conn, spec)
        conn.executemany(  # the code catalogue travels with the PTP release
            "INSERT INTO synthetic_codes (release_id, code, description) VALUES (?, ?, ?)",
            [(release_id, code, description) for code, description in catalogue.items()],
        )
        conn.executemany(
            """INSERT INTO ptp_edits
               (release_id, column1, column2, effective_date, deletion_date, modifier_indicator, rationale)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            [(release_id, *edit) for edit in edits],
        )
        record_file(conn, release_id, files[0], len(catalogue))
        record_file(conn, release_id, files[1], len(edits))
    return len(edits)


def load_synthetic_mue(conn: sqlite3.Connection, table_dir: Path, spec: ReleaseSpec) -> int | None:
    """Load mue_limits.csv; returns rows loaded, or None if unchanged since the last load."""
    if not spec.synthetic:
        raise ValueError("Synthetic tables must be loaded under a synthetic release")
    files = [source_file("codes", table_dir / "codes.csv"), source_file("mue", table_dir / "mue_limits.csv")]
    if is_already_loaded(conn, spec, files):
        return None
    limits = read_mue(table_dir / "mue_limits.csv", read_codes(table_dir / "codes.csv"))
    with conn:
        release_id = start_release(conn, spec)
        conn.executemany(
            "INSERT INTO mue_limits (release_id, code, mue_value, mai, rationale) VALUES (?, ?, ?, ?, ?)",
            [(release_id, *limit) for limit in limits],
        )
        record_file(conn, release_id, files[0], 0)
        record_file(conn, release_id, files[1], len(limits))
    return len(limits)
