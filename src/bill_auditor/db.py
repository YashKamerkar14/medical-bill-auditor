"""SQLite connection helper shared by loaders and lookups."""

import sqlite3
from pathlib import Path


def connect(path: Path | str) -> sqlite3.Connection:
    """Open the database with the settings every caller relies on."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row  # rows can be read by column name
    conn.execute("PRAGMA foreign_keys = ON")  # needed for ON DELETE CASCADE
    return conn


def connect_readonly(path: Path | str) -> sqlite3.Connection:
    """Open the database read-only (SQLite refuses every write). Used by the lookups and the MCP server."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"database not found: {path}")
    conn = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn
