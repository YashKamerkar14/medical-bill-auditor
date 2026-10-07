"""Charge benchmark loader and lookup, on a fake file in the real CMS layout."""

from datetime import date
from pathlib import Path

import pytest

from bill_auditor.ingest.load_charges import ChargesFormatError, ChargesSummary, average_to_cents, load_charges
from bill_auditor.ingest.releases import ReleaseSpec
from bill_auditor.tables.charges import get_charge_benchmark

FIXTURE = Path(__file__).parent / "fixtures" / "charges" / "geo_service.csv"
SPEC = ReleaseSpec("charges", "Charges (test)", date(2026, 10, 1), date(2026, 12, 31))
NOV = date(2026, 11, 20)


@pytest.fixture
def charges_db(empty_db):
    load_charges(empty_db, FIXTURE, SPEC)
    return empty_db


@pytest.mark.parametrize(
    ("text", "cents"), [("881.4666", 88147), ("250.125", 25013), ("310.4999", 31050), ("80.995", 8100), ("1", 100)]
)
def test_averages_round_half_up_to_cents(text, cents):
    assert average_to_cents(text, "test") == cents


def test_load_skips_and_reports_rows_without_a_state(empty_db):
    assert load_charges(empty_db, FIXTURE, SPEC) == ChargesSummary(rows=4, skipped_blank_state=1)


def test_reload_is_a_no_op(charges_db):
    assert load_charges(charges_db, FIXTURE, SPEC) is None


def test_national_benchmark_by_setting(charges_db):
    office = get_charge_benchmark(charges_db, "ZZ001", "nonfacility", NOV)
    facility = get_charge_benchmark(charges_db, "zz001", "facility", NOV)
    assert (office.status, office.avg_submitted_cents, office.avg_allowed_cents) == ("found", 25013, 10000)
    assert facility.avg_submitted_cents == 30050
    assert office.synthetic is False
    assert office.description == "Fake office visit, established patient"


def test_state_benchmark(charges_db):
    result = get_charge_benchmark(charges_db, "ZZ001", "nonfacility", NOV, state_fips="06")
    assert (result.geography, result.geo_name, result.avg_submitted_cents) == ("06", "California", 31050)


def test_missing_state_is_not_found_not_replaced_by_national(charges_db):
    result = get_charge_benchmark(charges_db, "ZZ001", "nonfacility", NOV, state_fips="36")
    assert result.status == "not_found"


def test_fractional_service_counts_are_kept(charges_db):
    assert get_charge_benchmark(charges_db, "ZZ070", "nonfacility", NOV).total_services == pytest.approx(466.9)


def test_outside_window_cannot_be_checked(charges_db):
    assert get_charge_benchmark(charges_db, "ZZ001", "nonfacility", date(2027, 1, 1)).status == "no_release"


def test_bad_state_code_is_rejected(charges_db):
    with pytest.raises(ValueError, match="FIPS"):
        get_charge_benchmark(charges_db, "ZZ001", "nonfacility", NOV, state_fips="California")


def test_unexpected_columns_are_refused(empty_db, tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text("HCPCS_Cd,Avg_Sbmtd_Chrg\nZZ001,1\n", encoding="utf-8")
    with pytest.raises(ChargesFormatError, match="unexpected columns"):
        load_charges(empty_db, path, SPEC)
