"""SQLite schema for the reference tables.

Every data row points at a release. A release is one versioned snapshot of a
CMS dataset (e.g. "PFS 2026 nonQP A-D") with the window of dates of service it
applies to, plus the exact files it was built from and their SHA-256 hashes.
That is what lets a report say which table release a flag rests on, and lets
the verify step prove the cited row really exists in that release.

Money is stored as integer cents: floating-point dollars cannot represent
values like 0.10 exactly, and the plan requires amounts to match exactly.

The PTP and MUE tables are SYNTHETIC (our own made-up rules over fake Z-codes;
see docs/decisions/0005-synthetic-ncci-tables.md). The `synthetic` flag on a
release carries that fact to every lookup result and report.
"""

import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS releases (
    release_id      INTEGER PRIMARY KEY,
    dataset         TEXT NOT NULL CHECK (dataset IN ('pfs', 'ptp', 'mue', 'charges', 'manual')),
    label           TEXT NOT NULL UNIQUE,
    effective_start TEXT NOT NULL,  -- ISO date, inclusive
    effective_end   TEXT NOT NULL,  -- ISO date, inclusive
    synthetic       INTEGER NOT NULL CHECK (synthetic IN (0, 1)),  -- 1 = made up, not CMS data
    loaded_at       TEXT NOT NULL,
    CHECK (effective_start <= effective_end)
);

-- One row per source file that went into a release (PFS has four parts).
CREATE TABLE IF NOT EXISTS release_files (
    release_id  INTEGER NOT NULL REFERENCES releases (release_id) ON DELETE CASCADE,
    part        TEXT NOT NULL,
    file_name   TEXT NOT NULL,
    sha256      TEXT NOT NULL,
    rows_loaded INTEGER NOT NULL,
    PRIMARY KEY (release_id, part)
);

-- PFS National Payment Amount File, nonQP variant.
-- Column meanings come from the layout PDF shipped inside each PFREV ZIP.
CREATE TABLE IF NOT EXISTS pfs_amounts (
    release_id             INTEGER NOT NULL REFERENCES releases (release_id) ON DELETE CASCADE,
    carrier                TEXT NOT NULL,     -- 5 digits
    locality               TEXT NOT NULL,     -- 2 digits; carrier + locality identifies a pricing locality
    code                   TEXT NOT NULL,     -- HCPCS / CPT
    modifier               TEXT NOT NULL,     -- '' (global), '26', 'TC' or '53'
    nonfacility_cents      INTEGER NOT NULL,
    facility_cents         INTEGER NOT NULL,
    pctc_indicator         TEXT NOT NULL,
    status_code            TEXT NOT NULL,
    mult_surgery_indicator TEXT NOT NULL,
    opps_indicator         TEXT NOT NULL,
    opps_nonfacility_cents INTEGER NOT NULL,
    opps_facility_cents    INTEGER NOT NULL,
    source_part            TEXT NOT NULL,     -- which file last wrote this row: 'A', 'B', 'C' or 'D'
    PRIMARY KEY (release_id, carrier, locality, code, modifier)
);

-- Code-pair edits ("don't bill these together"). SYNTHETIC: fake Z-codes only.
-- An edit applies to a date of service when effective_date <= DOS < deletion_date.
CREATE TABLE IF NOT EXISTS ptp_edits (
    release_id         INTEGER NOT NULL REFERENCES releases (release_id) ON DELETE CASCADE,
    column1            TEXT NOT NULL,   -- the code that is paid
    column2            TEXT NOT NULL,   -- the code that is denied when billed with column1
    effective_date     TEXT NOT NULL,   -- ISO date
    deletion_date      TEXT,            -- ISO date; NULL = still active
    modifier_indicator TEXT NOT NULL CHECK (modifier_indicator IN ('0', '1', '9')),
    rationale          TEXT NOT NULL,
    PRIMARY KEY (release_id, column1, column2, effective_date)
);

-- The synthetic code catalogue (synthetic_tables/codes.csv), loaded with the PTP
-- release, so "is this a known code?" is answered from a versioned table too.
CREATE TABLE IF NOT EXISTS synthetic_codes (
    release_id  INTEGER NOT NULL REFERENCES releases (release_id) ON DELETE CASCADE,
    code        TEXT NOT NULL,
    description TEXT NOT NULL,
    PRIMARY KEY (release_id, code)
);

-- Unit limits per code per patient ("too many units"). SYNTHETIC: fake Z-codes only.
CREATE TABLE IF NOT EXISTS mue_limits (
    release_id INTEGER NOT NULL REFERENCES releases (release_id) ON DELETE CASCADE,
    code       TEXT NOT NULL,
    mue_value  INTEGER NOT NULL CHECK (mue_value > 0),
    mai        TEXT NOT NULL CHECK (mai IN ('1', '2', '3')),  -- MUE adjudication indicator
    rationale  TEXT NOT NULL,
    PRIMARY KEY (release_id, code)
);

-- Real average charges: CMS "Medicare Physician & Other Practitioners - by
-- Geography and Service". Averages over many claims, so they are rounded to
-- cents; they describe typical billing, they are not a price anyone must charge.
CREATE TABLE IF NOT EXISTS charge_benchmarks (
    release_id           INTEGER NOT NULL REFERENCES releases (release_id) ON DELETE CASCADE,
    geo_level            TEXT NOT NULL CHECK (geo_level IN ('National', 'State')),
    geo_code             TEXT NOT NULL,   -- '' for national, else 2-character state FIPS code
    geo_name             TEXT NOT NULL,
    code                 TEXT NOT NULL,
    description          TEXT NOT NULL,   -- CMS plain-language description
    place_of_service     TEXT NOT NULL CHECK (place_of_service IN ('F', 'O')),  -- facility / office
    total_services       REAL NOT NULL,   -- can be fractional for drug units
    avg_submitted_cents  INTEGER NOT NULL,
    avg_allowed_cents    INTEGER NOT NULL,
    avg_payment_cents    INTEGER NOT NULL,
    PRIMARY KEY (release_id, geo_level, geo_code, code, place_of_service)
);

-- NCCI Policy Manual. Whole sections are kept for quoting and verification;
-- chunks are the retrieval units.
CREATE TABLE IF NOT EXISTS manual_sections (
    section_id    TEXT PRIMARY KEY,           -- e.g. 'I.E' (chapter I, section E)
    release_id    INTEGER NOT NULL REFERENCES releases (release_id) ON DELETE CASCADE,
    chapter       TEXT NOT NULL,
    chapter_title TEXT NOT NULL,
    title         TEXT NOT NULL,
    page_start    TEXT NOT NULL,              -- the manual's own page labels, e.g. 'I-14'
    page_end      TEXT NOT NULL,
    text          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS manual_chunks (
    chunk_rowid   INTEGER PRIMARY KEY,        -- links each chunk to its FTS5 row
    chunk_id      TEXT NOT NULL UNIQUE,       -- e.g. 'I.E#5'
    section_id    TEXT NOT NULL REFERENCES manual_sections (section_id) ON DELETE CASCADE,
    section_title TEXT NOT NULL,
    ordinal       INTEGER NOT NULL,
    page_start    TEXT NOT NULL,
    page_end      TEXT NOT NULL,
    text          TEXT NOT NULL
);

-- Keyword index (BM25) over the chunks. "External content": the text lives in
-- manual_chunks and FTS5 stores only the index, so it must be rebuilt after
-- every load. The porter tokenizer matches "modifiers" to "modifier".
CREATE VIRTUAL TABLE IF NOT EXISTS manual_fts USING fts5 (
    section_title, text,
    content = 'manual_chunks', content_rowid = 'chunk_rowid',
    tokenize = 'porter unicode61'
);
"""


INDEXES = """
-- "Is this code in the fee schedule at all?" without scanning a million rows.
CREATE INDEX IF NOT EXISTS pfs_amounts_code ON pfs_amounts (code);
"""


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.executescript(INDEXES)
