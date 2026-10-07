"""Registering releases: what was loaded, from which files, for which dates."""

import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class ReleaseSpec:
    """What a loader is about to load, decided before reading any rows."""

    dataset: str
    label: str
    effective_start: date
    effective_end: date
    synthetic: bool = False  # True for our made-up tables, never for CMS data


@dataclass(frozen=True)
class SourceFile:
    part: str
    path: Path
    sha256: str


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def source_file(part: str, path: Path) -> SourceFile:
    return SourceFile(part=part, path=path, sha256=sha256_file(path))


def is_already_loaded(conn: sqlite3.Connection, spec: ReleaseSpec, files: list[SourceFile]) -> bool:
    """True when this label was loaded from exactly these files (same hashes)."""
    stored = conn.execute(
        """SELECT f.part, f.sha256 FROM release_files f
           JOIN releases r USING (release_id) WHERE r.label = ?""",
        (spec.label,),
    ).fetchall()
    return {(row["part"], row["sha256"]) for row in stored} == {(f.part, f.sha256) for f in files}


def start_release(conn: sqlite3.Connection, spec: ReleaseSpec) -> int:
    """Create the release row, replacing any earlier load with the same label.

    Refuses a window that overlaps another release of the same dataset: a date
    of service must map to exactly one release.
    """
    conn.execute("DELETE FROM releases WHERE label = ?", (spec.label,))  # cascades to its rows

    overlap = conn.execute(
        """SELECT label FROM releases
           WHERE dataset = ? AND effective_start <= ? AND effective_end >= ?""",
        (spec.dataset, spec.effective_end.isoformat(), spec.effective_start.isoformat()),
    ).fetchone()
    if overlap:
        raise ValueError(f"{spec.label} overlaps release {overlap['label']} for dataset {spec.dataset}")

    cursor = conn.execute(
        """INSERT INTO releases (dataset, label, effective_start, effective_end, synthetic, loaded_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            spec.dataset,
            spec.label,
            spec.effective_start.isoformat(),
            spec.effective_end.isoformat(),
            int(spec.synthetic),
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
        ),
    )
    return cursor.lastrowid


def record_file(conn: sqlite3.Connection, release_id: int, file: SourceFile, rows_loaded: int) -> None:
    conn.execute(
        "INSERT INTO release_files (release_id, part, file_name, sha256, rows_loaded) VALUES (?, ?, ?, ?, ?)",
        (release_id, file.part, file.path.name, file.sha256, rows_loaded),
    )
