"""Is a procedure code known to any loaded table?

A code the auditor has never seen is reported as "cannot check", never
guessed about (CLAUDE.md guardrail). Known = in the fee schedule, in the
CMS charge data, or in the synthetic code catalogue, for the release in force
on the date of service.
"""

import sqlite3
from datetime import date

from bill_auditor.tables.releases import find_release


def is_known_code(conn: sqlite3.Connection, code: str, date_of_service: date) -> bool:
    code = code.strip().upper()
    checks = (
        ("pfs", "SELECT 1 FROM pfs_amounts WHERE release_id = ? AND code = ? LIMIT 1"),
        ("charges", "SELECT 1 FROM charge_benchmarks WHERE release_id = ? AND geo_level = 'National' "
                    "AND geo_code = '' AND code = ? LIMIT 1"),
        ("ptp", "SELECT 1 FROM synthetic_codes WHERE release_id = ? AND code = ? LIMIT 1"),
    )
    for dataset, sql in checks:
        release = find_release(conn, dataset, date_of_service)
        if release and conn.execute(sql, (release.release_id, code)).fetchone():
            return True
    return False
