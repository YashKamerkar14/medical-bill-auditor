"""Synthetic PTP/MUE tables: loader validation, and lookups against the committed tables.

The lookup tests use the real files in synthetic_tables/ on purpose: each row
there exists to exercise one rule branch, and these tests pin that behaviour.
"""

import shutil
from datetime import date

import pytest

from bill_auditor.ingest.load_synthetic_ncci import (
    SyntheticTableError,
    load_synthetic_mue,
    load_synthetic_ptp,
    read_codes,
    read_mue,
    read_ptp,
)
from bill_auditor.ingest.sources import SYNTHETIC_MUE, SYNTHETIC_PTP, SYNTHETIC_TABLE_DIR
from bill_auditor.tables.code_pairs import PTP_ASSOCIATED_MODIFIERS, check_code_pair
from bill_auditor.tables.unit_limits import get_unit_limit

NOV = date(2026, 11, 20)
CODES = {"Z1001", "Z1002", "Z1003"}
PTP_HEADER = "column1,column2,effective_date,deletion_date,modifier_indicator,rationale\n"


@pytest.fixture
def ncci_db(empty_db):
    load_synthetic_ptp(empty_db, SYNTHETIC_TABLE_DIR, SYNTHETIC_PTP)
    load_synthetic_mue(empty_db, SYNTHETIC_TABLE_DIR, SYNTHETIC_MUE)
    return empty_db


# --- loader validation -------------------------------------------------------

def write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_committed_tables_are_valid():
    codes = read_codes(SYNTHETIC_TABLE_DIR / "codes.csv")
    assert read_ptp(SYNTHETIC_TABLE_DIR / "ptp_edits.csv", codes)
    assert read_mue(SYNTHETIC_TABLE_DIR / "mue_limits.csv", codes)


@pytest.mark.parametrize("bad_code", ["99213", "ZZ001", "Z100", "A1001"])
def test_only_fake_z_codes_are_accepted(tmp_path, bad_code):
    path = write(tmp_path, "codes.csv", f'code,description\n{bad_code},"x"\n')
    with pytest.raises(SyntheticTableError, match="not a fake code"):
        read_codes(path)


@pytest.mark.parametrize(
    ("row", "error"),
    [
        ("Z1001,Z1009,2026-01-01,,0,r", "not in codes.csv"),
        ("Z1001,Z1001,2026-01-01,,0,r", "paired with itself"),
        ("Z1001,Z1002,2026-13-01,,0,r", "bad date"),
        ("Z1001,Z1002,2026-01-01,,5,r", "0, 1 or 9"),
        ("Z1001,Z1002,2026-06-01,2026-01-01,0,r", "deleted before"),
        ("Z1001,Z1002,2026-01-01,,9,r", "indicator 9"),
        ("Z1001,Z1002,2026-01-01,2026-01-01,0,r", "indicator 9"),
        ("Z1001,Z1002,2026-01-01,,0,", "rationale"),
    ],
)
def test_bad_ptp_rows_are_refused(tmp_path, row, error):
    path = write(tmp_path, "ptp_edits.csv", PTP_HEADER + row + "\n")
    with pytest.raises(SyntheticTableError, match=error):
        read_ptp(path, CODES)


def test_overlapping_edits_for_a_pair_are_refused_in_either_order(tmp_path):
    rows = "Z1001,Z1002,2026-01-01,,0,r\nZ1002,Z1001,2026-06-01,,1,r\n"
    with pytest.raises(SyntheticTableError, match="overlapping"):
        read_ptp(write(tmp_path, "ptp_edits.csv", PTP_HEADER + rows), CODES)


def test_back_to_back_edits_for_a_pair_are_allowed(tmp_path):
    rows = "Z1001,Z1002,2026-01-01,2026-06-01,0,r\nZ1001,Z1002,2026-06-01,,1,r\n"
    assert len(read_ptp(write(tmp_path, "ptp_edits.csv", PTP_HEADER + rows), CODES)) == 2


@pytest.mark.parametrize(
    ("row", "error"),
    [("Z1001,0,2,r", "positive"), ("Z1001,1.5,2,r", "positive"), ("Z1001,1,4,r", "MAI"), ("Z1009,1,2,r", "codes.csv")],
)
def test_bad_mue_rows_are_refused(tmp_path, row, error):
    path = write(tmp_path, "mue_limits.csv", "code,mue_value,mai,rationale\n" + row + "\n")
    with pytest.raises(SyntheticTableError, match=error):
        read_mue(path, CODES)


def test_synthetic_tables_require_a_synthetic_release(empty_db):
    from dataclasses import replace

    with pytest.raises(ValueError, match="synthetic release"):
        load_synthetic_ptp(empty_db, SYNTHETIC_TABLE_DIR, replace(SYNTHETIC_PTP, synthetic=False))


def test_reload_is_a_no_op_until_a_table_changes(ncci_db, tmp_path):
    assert load_synthetic_ptp(ncci_db, SYNTHETIC_TABLE_DIR, SYNTHETIC_PTP) is None
    copy = tmp_path / "tables"
    shutil.copytree(SYNTHETIC_TABLE_DIR, copy)
    with (copy / "ptp_edits.csv").open("a", encoding="utf-8") as handle:
        handle.write('Z1080,Z1001,2026-01-01,,1,"added row"\n')
    assert load_synthetic_ptp(ncci_db, copy, SYNTHETIC_PTP) == 12


# --- code-pair lookup ----------------------------------------------------------

def test_edit_found_in_either_order_and_marked_synthetic(ncci_db):
    for a, b in (("Z1040", "Z1041"), ("Z1041", "Z1040")):
        result = check_code_pair(ncci_db, a, b, NOV)
        assert (result.status, result.column1, result.column2) == ("edit", "Z1040", "Z1041")
        assert result.synthetic is True
        assert result.release_label.startswith("SYNTHETIC")
        assert result.message.startswith("SYNTHETIC")


def test_no_edit_for_unrelated_codes(ncci_db):
    result = check_code_pair(ncci_db, "Z1001", "Z1080", NOV)
    assert result.status == "no_edit"
    assert result.synthetic is True


def test_bypass_modifiers_are_reported_per_column(ncci_db):
    result = check_code_pair(ncci_db, "Z1020", "Z1010", NOV, modifiers_a=["59", "RT"], modifiers_b=["25"])
    assert result.modifier_indicator == "1"
    assert result.bypass_modifiers_on_column1 == ["25"]        # Z1010 is column 1
    assert result.bypass_modifiers_on_column2 == ["59", "RT"]  # Z1020 is column 2


@pytest.mark.parametrize("modifier", ["22", "76", "77"])
def test_modifiers_the_manual_says_do_not_bypass(ncci_db, modifier):
    assert modifier not in PTP_ASSOCIATED_MODIFIERS
    result = check_code_pair(ncci_db, "Z1010", "Z1020", NOV, modifiers_b=[modifier])
    assert result.bypass_modifiers_on_column2 == []


@pytest.mark.parametrize(("dos", "status"), [(date(2026, 10, 31), "no_edit"), (date(2026, 11, 1), "edit")])
def test_edit_added_mid_quarter_starts_on_its_effective_date(ncci_db, dos, status):
    assert check_code_pair(ncci_db, "Z1002", "Z1022", dos).status == status


@pytest.mark.parametrize(("dos", "status"), [(date(2026, 11, 14), "edit"), (date(2026, 11, 15), "no_edit")])
def test_deleted_edit_stops_on_its_deletion_date(ncci_db, dos, status):
    assert check_code_pair(ncci_db, "Z1060", "Z1061", dos).status == status


def test_indicator_9_edit_never_applies(ncci_db):
    assert check_code_pair(ncci_db, "Z1070", "Z1071", NOV).status == "no_edit"


@pytest.mark.parametrize(("dos", "indicator"), [(date(2026, 10, 14), "0"), (date(2026, 10, 15), "1")])
def test_indicator_change_mid_quarter(ncci_db, dos, indicator):
    assert check_code_pair(ncci_db, "Z1044", "Z1045", dos).modifier_indicator == indicator


@pytest.mark.parametrize("dos", [date(2026, 9, 30), date(2027, 1, 1)])
def test_code_pair_outside_release_cannot_be_checked(ncci_db, dos):
    assert check_code_pair(ncci_db, "Z1040", "Z1041", dos).status == "no_release"


# --- unit-limit lookup ---------------------------------------------------------

def test_limit_with_adjudication_meaning(ncci_db):
    result = get_unit_limit(ncci_db, "z1060", NOV)
    assert (result.status, result.mue_value, result.mai) == ("limit", 4, "3")
    assert "clinical" in result.mai_meaning
    assert result.synthetic is True


def test_line_edit_indicator(ncci_db):
    assert get_unit_limit(ncci_db, "Z1010", NOV).mai_meaning.startswith("Claim line edit")


def test_code_without_limit_is_not_published_not_pass(ncci_db):
    result = get_unit_limit(ncci_db, "Z1080", NOV)
    assert result.status == "not_published"
    assert result.mue_value is None
    assert "cannot check" in result.message


def test_real_looking_code_has_no_synthetic_limit(ncci_db):
    assert get_unit_limit(ncci_db, "99213", NOV).status == "not_published"


def test_unit_limit_outside_release_cannot_be_checked(ncci_db):
    assert get_unit_limit(ncci_db, "Z1060", date(2026, 9, 30)).status == "no_release"
