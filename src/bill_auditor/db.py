"""SQLite connection helper shared by loaders and lookups."""

import sqlite3
from pathlib import Path


def connect(path: Path | str) -> sqlite3.Connection:
    """Open the database with the settings every caller relies on."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row  # rows can be read by column name
    conn.execute("PRAGMA foreign_keys = ON")  # needed for ON DELETE CASCADE
    return conn
