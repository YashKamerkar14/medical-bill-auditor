"""Finding the release in force on a date of service."""

import sqlite3
from datetime import date

from pydantic import BaseModel


class Release(BaseModel):
    release_id: int
    dataset: str
    label: str
    effective_start: date
    effective_end: date
    synthetic: bool


def find_release(conn: sqlite3.Connection, dataset: str, date_of_service: date) -> Release | None:
    """The release of `dataset` whose window contains the date, or None.

    None means "cannot check: no release loaded" for that date. Overlapping
    windows are refused at load time, so there is at most one match.
    """
    row = conn.execute(
        """SELECT release_id, dataset, label, effective_start, effective_end, synthetic FROM releases
           WHERE dataset = ? AND effective_start <= ? AND effective_end >= ?""",
        (dataset, date_of_service.isoformat(), date_of_service.isoformat()),
    ).fetchone()
    return Release(**dict(row)) if row else None
