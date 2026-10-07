from dataclasses import replace

import pytest

from bill_auditor.ingest.load_pfs import PartSummary, PfsFormatError, load_pfs, parse_money
from bill_auditor.ingest.releases import start_release
from conftest import FAKE_PFS_SPEC, FIXTURES, write_pfrev_zip


def row(conn, code, modifier="", locality="01"):
    return conn.execute(
        "SELECT * FROM pfs_amounts WHERE code = ? AND modifier = ? AND locality = ?",
        (code, modifier, locality),
    ).fetchone()


@pytest.mark.parametrize(
    ("text", "cents"),
    [("0008896.73", 889673), ("0000000.10", 10), ("0000000.00", 0), ("9999999.99", 999999999)],
)
def test_parse_money_is_exact(text, cents):
    assert parse_money(text, "test") == cents


@pytest.mark.parametrize("text", ["12.50", "0000100.0", "-000100.00", "0000100,00", ""])
def test_parse_money_rejects_other_formats(text):
    with pytest.raises(PfsFormatError):
        parse_money(text, "test")


def test_each_part_reports_what_it_replaced_and_added(empty_db, pfs_dir):
    summaries = load_pfs(empty_db, pfs_dir, FAKE_PFS_SPEC, year=2026, expected_parts="ABCD")
    assert summaries == [
        PartSummary(part="A", rows=5, replaced=0, added=5),  # trailer row skipped
        PartSummary(part="B", rows=2, replaced=1, added=1),
        PartSummary(part="C", rows=1, replaced=1, added=0),
        PartSummary(part="D", rows=1, replaced=0, added=1),
    ]
    assert empty_db.execute("SELECT COUNT(*) FROM pfs_amounts").fetchone()[0] == 7


def test_revision_overwrites_base_row_and_records_its_part(pfs_db):
    revised = row(pfs_db, "ZZ001")
    assert (revised["nonfacility_cents"], revised["facility_cents"]) == (12000, 9000)
    assert revised["source_part"] == "B"


def test_rows_not_in_any_revision_keep_part_a(pfs_db):
    untouched = row(pfs_db, "ZZ001", locality="02")
    assert untouched["nonfacility_cents"] == 11000
    assert untouched["source_part"] == "A"


def test_identical_revision_row_still_records_latest_part(pfs_db):
    assert row(pfs_db, "ZZ002")["source_part"] == "C"


def test_nonqp_amounts_are_loaded_not_qp(pfs_db):
    # The QP copy in each fake ZIP has amounts starting 0009...
    assert row(pfs_db, "ZZ002", modifier="TC")["nonfacility_cents"] == 15040


def test_modifier_and_indicators_are_kept(pfs_db):
    added = row(pfs_db, "ZZ004")
    assert (added["status_code"], added["pctc_indicator"], added["source_part"]) == ("R", "3", "D")
    assert row(pfs_db, "ZZ002", modifier="26")["nonfacility_cents"] == 5010


def test_release_records_every_file_and_hash(pfs_db):
    files = pfs_db.execute("SELECT part, sha256, rows_loaded FROM release_files ORDER BY part").fetchall()
    assert [(f["part"], f["rows_loaded"]) for f in files] == [("A", 5), ("B", 2), ("C", 1), ("D", 1)]
    assert all(len(f["sha256"]) == 64 for f in files)


def test_reloading_the_same_files_is_a_no_op(pfs_db, pfs_dir):
    assert load_pfs(pfs_db, pfs_dir, FAKE_PFS_SPEC, year=2026, expected_parts="ABCD") is None
    assert pfs_db.execute("SELECT COUNT(*) FROM pfs_amounts").fetchone()[0] == 7
    assert pfs_db.execute("SELECT COUNT(*) FROM releases").fetchone()[0] == 1


def test_changed_file_replaces_the_release(pfs_db, pfs_dir):
    text = (FIXTURES / "pfs" / "pfrev_d.txt").read_text().replace("0000000.10", "0000000.20")
    write_pfrev_zip(pfs_dir, "D", text)
    assert load_pfs(pfs_db, pfs_dir, FAKE_PFS_SPEC, year=2026, expected_parts="ABCD") is not None
    assert row(pfs_db, "ZZ004")["nonfacility_cents"] == 20
    assert pfs_db.execute("SELECT COUNT(*) FROM pfs_amounts").fetchone()[0] == 7


def test_missing_revision_is_refused(empty_db, pfs_dir):
    (pfs_dir / "pfrev26d-test.zip").unlink()
    with pytest.raises(PfsFormatError, match="Expected parts ABCD"):
        load_pfs(empty_db, pfs_dir, FAKE_PFS_SPEC, year=2026, expected_parts="ABCD")


def test_wrong_field_count_names_the_file_and_line(empty_db, pfs_dir):
    bad = '"2026","99999","01","ZZ009","  ","0000001.00"\n'
    write_pfrev_zip(pfs_dir, "B", bad)
    with pytest.raises(PfsFormatError, match="pfrev26b-test.zip line 1: expected 16 fields"):
        load_pfs(empty_db, pfs_dir, FAKE_PFS_SPEC, year=2026, expected_parts="ABCD")


def test_failed_load_leaves_nothing_behind(empty_db, pfs_dir):
    write_pfrev_zip(pfs_dir, "C", '"2025","99999","01","ZZ001","  ","0000001.00","0000001.00"," ","0","A","2","0000000.00","0000000.00","9","0000000.00","0000000.00"\n')
    with pytest.raises(PfsFormatError, match="expected year 2026"):
        load_pfs(empty_db, pfs_dir, FAKE_PFS_SPEC, year=2026, expected_parts="ABCD")
    assert empty_db.execute("SELECT COUNT(*) FROM releases").fetchone()[0] == 0
    assert empty_db.execute("SELECT COUNT(*) FROM pfs_amounts").fetchone()[0] == 0


def test_repeated_key_within_one_part_is_refused(empty_db, pfs_dir):
    line = (FIXTURES / "pfs" / "pfrev_d.txt").read_text().splitlines()[0]
    write_pfrev_zip(pfs_dir, "D", f"{line}\n{line}\n")
    with pytest.raises(PfsFormatError, match="repeats"):
        load_pfs(empty_db, pfs_dir, FAKE_PFS_SPEC, year=2026, expected_parts="ABCD")


def test_overlapping_release_windows_are_refused(pfs_db):
    other = replace(FAKE_PFS_SPEC, label="Another PFS release")
    with pytest.raises(ValueError, match="overlaps"):
        start_release(pfs_db, other)
