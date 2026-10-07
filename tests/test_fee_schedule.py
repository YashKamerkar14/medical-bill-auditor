from datetime import date

import pytest

from bill_auditor.tables.fee_schedule import get_fee_schedule_amount

IN_WINDOW = date(2026, 11, 15)


def lookup(conn, code="ZZ001", modifier=None, locality_id="9999901", setting="nonfacility", dos=IN_WINDOW):
    return get_fee_schedule_amount(conn, code, modifier, locality_id, setting, dos)


def test_found_returns_amount_release_and_source_part(pfs_db):
    result = lookup(pfs_db)
    assert result.status == "found"
    assert result.amount_cents == 12000  # revised by part B
    assert result.release_label == "PFS 2026 nonQP A-D (test)"
    assert result.source_part == "B"


def test_setting_selects_facility_or_nonfacility_amount(pfs_db):
    assert lookup(pfs_db, code="ZZ002", setting="nonfacility").amount_cents == 20050
    assert lookup(pfs_db, code="ZZ002", setting="facility").amount_cents == 15025


def test_locality_matters(pfs_db):
    assert lookup(pfs_db, locality_id="9999902").amount_cents == 11000


@pytest.mark.parametrize(("modifier", "expected_cents"), [("26", 5010), ("tc", 15040), (" 26 ", 5010)])
def test_component_modifiers_have_their_own_rows(pfs_db, modifier, expected_cents):
    assert lookup(pfs_db, code="ZZ002", modifier=modifier).amount_cents == expected_cents


@pytest.mark.parametrize("modifier", ["25", "59", "", None])
def test_other_modifiers_are_priced_from_the_global_row(pfs_db, modifier):
    result = lookup(pfs_db, code="ZZ002", modifier=modifier)
    assert result.pricing_modifier == ""
    assert result.amount_cents == 20050


def test_unknown_code_is_not_found_not_guessed(pfs_db):
    result = lookup(pfs_db, code="ZZ999")
    assert result.status == "not_found"
    assert result.amount_cents is None
    assert result.release_label == "PFS 2026 nonQP A-D (test)"


@pytest.mark.parametrize("dos", [date(2026, 10, 1), date(2026, 12, 31)])
def test_window_edges_are_inclusive(pfs_db, dos):
    assert lookup(pfs_db, dos=dos).status == "found"


@pytest.mark.parametrize("dos", [date(2026, 9, 30), date(2027, 1, 1)])
def test_date_outside_loaded_release_cannot_be_checked(pfs_db, dos):
    result = lookup(pfs_db, dos=dos)
    assert result.status == "no_release"
    assert result.amount_cents is None
    assert "Cannot check" in result.message


@pytest.mark.parametrize("bad", ["99999", "99999-01", "ABCDEFG"])
def test_malformed_locality_is_rejected(pfs_db, bad):
    with pytest.raises(ValueError, match="7 digits"):
        lookup(pfs_db, locality_id=bad)


def test_result_serialises_to_json_for_tools(pfs_db):
    payload = lookup(pfs_db).model_dump(mode="json")
    assert payload["date_of_service"] == "2026-11-15"
    assert payload["amount_cents"] == 12000
