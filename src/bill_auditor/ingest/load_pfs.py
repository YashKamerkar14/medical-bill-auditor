"""Load the PFS National Payment Amount File (PFREV) into SQLite.

CMS publishes the year's fee schedule as one full file (PFREV26A) followed by
revision files (B, C, D) holding only changed or new rows. The layout PDF says
they are "not overlay files": a revision is not a complete replacement, so the
loader applies them in order as upserts. A key already present is replaced, a
new key is inserted, and every row records which part last wrote it.

File facts checked on the real 2026 files (do not assume them for other years):
- Each outer ZIP nests a `*_QP.zip` and a `*_nonQP.zip`; names and folder casing
  vary between parts, so members are found by suffix, not by exact name.
- The nonQP ZIP holds a layout PDF and a `.txt`: quoted, comma-separated,
  16 fields, no header row, ASCII.
- The `.txt` ends with trailer rows starting `TRL-` (copyright statements).
- Amounts look like `0008896.73` (7 digits, a dot, 2 digits).
"""

import csv
import io
import re
import sqlite3
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from bill_auditor.ingest.releases import (
    ReleaseSpec,
    SourceFile,
    is_already_loaded,
    record_file,
    source_file,
    start_release,
)

FIELD_COUNT = 16
_PART_PATTERN = re.compile(r"pfrev(\d{2})([a-z])", re.IGNORECASE)
_MONEY_PATTERN = re.compile(r"^\d{7}\.\d{2}$")

COLUMNS = (
    "carrier", "locality", "code", "modifier",
    "nonfacility_cents", "facility_cents",
    "pctc_indicator", "status_code", "mult_surgery_indicator", "opps_indicator",
    "opps_nonfacility_cents", "opps_facility_cents",
)


class PfsFormatError(ValueError):
    """The file does not match the layout this loader was written against."""


@dataclass(frozen=True)
class PartSummary:
    part: str
    rows: int
    replaced: int  # keys that already existed and were overwritten
    added: int     # keys that were new


def find_parts(pfs_dir: Path, year: int) -> dict[str, Path]:
    """Map part letter ('A', 'B', ...) to its outer ZIP, e.g. pfrev26b-....zip for 2026."""
    parts: dict[str, Path] = {}
    for path in sorted(pfs_dir.glob("*.zip")):
        match = _PART_PATTERN.search(path.name)
        if not match or match.group(1) != f"{year % 100:02d}":
            continue
        letter = match.group(2).upper()
        if letter in parts:
            raise PfsFormatError(f"Two files for part {letter}: {parts[letter].name}, {path.name}")
        parts[letter] = path
    return parts


def parse_money(text: str, where: str) -> int:
    """'0008896.73' -> 889673 cents, exactly (no floating point)."""
    if not _MONEY_PATTERN.match(text):
        raise PfsFormatError(f"{where}: bad amount {text!r}")
    return int(text.replace(".", ""))


def parse_row(fields: list[str], year: int, where: str) -> tuple:
    if len(fields) != FIELD_COUNT:
        raise PfsFormatError(f"{where}: expected {FIELD_COUNT} fields, got {len(fields)}")
    if fields[0] != str(year):
        raise PfsFormatError(f"{where}: expected year {year}, got {fields[0]!r}")
    if fields[7].strip():
        raise PfsFormatError(f"{where}: filler field 8 is not blank")
    return (
        fields[1],                          # carrier
        fields[2],                          # locality
        fields[3].strip(),                  # code
        fields[4].strip(),                  # modifier ('' = global service)
        parse_money(fields[5], where),      # non-facility amount
        parse_money(fields[6], where),      # facility amount
        fields[8],                          # PC/TC indicator
        fields[9],                          # status code
        fields[10],                         # multiple surgery indicator
        # fields 11 and 12 (50% therapy reduction amounts) are not used
        fields[13],                         # OPPS indicator
        parse_money(fields[14], where),     # OPPS-capped non-facility amount
        parse_money(fields[15], where),     # OPPS-capped facility amount
    )


def iter_part_rows(outer_zip: Path, year: int) -> Iterator[tuple]:
    """Yield parsed rows from the nonQP text file inside one outer ZIP."""
    with zipfile.ZipFile(outer_zip) as outer:
        inner_names = [n for n in outer.namelist() if n.lower().endswith("_nonqp.zip")]
        if len(inner_names) != 1:
            raise PfsFormatError(f"{outer_zip.name}: expected one *_nonQP.zip, found {inner_names}")
        inner = zipfile.ZipFile(io.BytesIO(outer.read(inner_names[0])))

    with inner:
        text_names = [n for n in inner.namelist() if n.lower().endswith(".txt")]
        if len(text_names) != 1:
            raise PfsFormatError(f"{outer_zip.name}: expected one .txt, found {text_names}")
        with inner.open(text_names[0]) as raw:
            reader = csv.reader(io.TextIOWrapper(raw, encoding="ascii", newline=""))
            for line_no, fields in enumerate(reader, start=1):
                if fields and fields[0].startswith("TRL"):
                    continue  # trailer: copyright statements, not data
                yield parse_row(fields, year, f"{outer_zip.name} line {line_no}")


def _merge_part(conn: sqlite3.Connection, release_id: int, part: str, rows: Iterator[tuple]) -> PartSummary:
    """Stage one part, count what it replaces, then upsert it into pfs_amounts."""
    conn.execute("DROP TABLE IF EXISTS temp.pfs_stage")
    conn.execute(
        """CREATE TEMP TABLE pfs_stage AS SELECT * FROM pfs_amounts WHERE 0"""
    )
    # A primary key on staging catches a part that repeats a key: without it the
    # second copy would silently win.
    conn.execute("CREATE UNIQUE INDEX temp.pfs_stage_key ON pfs_stage (carrier, locality, code, modifier)")

    placeholders = ", ".join("?" for _ in COLUMNS)
    try:
        conn.executemany(
            f"INSERT INTO pfs_stage ({', '.join(COLUMNS)}) VALUES ({placeholders})", rows
        )
    except sqlite3.IntegrityError as error:
        raise PfsFormatError(f"Part {part} repeats a (carrier, locality, code, modifier) key") from error

    total = conn.execute("SELECT COUNT(*) FROM pfs_stage").fetchone()[0]
    replaced = conn.execute(
        """SELECT COUNT(*) FROM pfs_stage s JOIN pfs_amounts a
           ON a.release_id = ? AND a.carrier = s.carrier AND a.locality = s.locality
          AND a.code = s.code AND a.modifier = s.modifier""",
        (release_id,),
    ).fetchone()[0]

    updates = ", ".join(f"{c} = excluded.{c}" for c in COLUMNS[4:])
    conn.execute(
        f"""INSERT INTO pfs_amounts (release_id, {', '.join(COLUMNS)}, source_part)
            SELECT ?, {', '.join(COLUMNS)}, ? FROM pfs_stage WHERE true
            ON CONFLICT (release_id, carrier, locality, code, modifier)
            DO UPDATE SET {updates}, source_part = excluded.source_part""",
        (release_id, part),
    )
    conn.execute("DROP TABLE temp.pfs_stage")
    return PartSummary(part=part, rows=total, replaced=replaced, added=total - replaced)


def load_pfs(
    conn: sqlite3.Connection,
    pfs_dir: Path,
    spec: ReleaseSpec,
    year: int,
    expected_parts: str,
) -> list[PartSummary] | None:
    """Load parts in order (e.g. expected_parts='ABCD') as one release.

    Returns None when the same files are already loaded under this label.
    All parts must be present: a fee schedule missing a revision would give
    wrong amounts without any sign that something is missing.
    """
    found = find_parts(pfs_dir, year)
    if sorted(found) != sorted(expected_parts):
        raise PfsFormatError(f"Expected parts {expected_parts} in {pfs_dir}, found {''.join(sorted(found)) or 'none'}")

    files: list[SourceFile] = [source_file(letter, found[letter]) for letter in expected_parts]
    if is_already_loaded(conn, spec, files):
        return None

    summaries = []
    with conn:  # one transaction: either the whole release loads or none of it does
        release_id = start_release(conn, spec)
        for file in files:
            summary = _merge_part(conn, release_id, file.part, iter_part_rows(file.path, year))
            record_file(conn, release_id, file, summary.rows)
            summaries.append(summary)
    return summaries
