"""The answer key must be exactly right: every planted error recorded, nothing else wrong.

These tests re-check every generated case against simple, independent versions
of the six rules (written here, not imported from the checks) and require that
what they find equals the answer key. Uses a fake code pool, so no CMS data.
"""

import random
from collections import defaultdict

import pytest

from bill_auditor.checks.settings import PRICE_BENCHMARK_RATIO
from bill_auditor.evaluation.generator import (
    LOCALITIES,
    SCHEDULE,
    RealCode,
    generate,
    load_synthetic_rules,
    read_cases,
    write_cases,
)
from bill_auditor.parsing.schemas import PLACE_OF_SERVICE_SETTING
from bill_auditor.tables.code_pairs import PTP_ASSOCIATED_MODIFIERS

RULES = load_synthetic_rules()


def fake_pool() -> list[RealCode]:
    rng = random.Random(1)
    return [
        RealCode(f"ZZ{n:03d}", f"Fake service {n}",
                 {(loc, s): rng.randint(1500, 30000) for loc in LOCALITIES for s in ("nonfacility", "facility")})
        for n in range(1, 61)
    ]


POOL = {c.code: c for c in fake_pool()}


@pytest.fixture(scope="module")
def cases():
    return generate(None, pool=list(POOL.values()))


def flags(case, check, kind="error"):
    return [f for f in case.expected if f.check == check and f.kind == kind]


def expected_by_lines(case, check, kind="error"):
    return {frozenset(f.bill_lines): f.amount_cents for f in flags(case, check, kind)}


# --- shape of the set ---------------------------------------------------------------

def test_schedule_size_and_split(cases):
    assert len(cases) == sum(SCHEDULE.values()) == 300
    assert len({c.case_id for c in cases}) == 300
    dev = sum(c.split == "dev" for c in cases)
    assert dev == 210


def test_generation_is_deterministic(cases):
    again = generate(None, pool=list(POOL.values()))
    assert [c.model_dump_json() for c in again] == [c.model_dump_json() for c in cases]


def test_every_error_case_has_its_planted_flag(cases):
    for case in cases:
        if case.tags[0].startswith("error:"):
            assert case.expected, case.case_id
        if case.tags[0] == "clean" or case.tags[0].startswith("trap:"):
            assert not case.expected, case.case_id


def test_all_bills_are_synthetic_with_fake_npis(cases):
    for case in cases:
        assert case.bill.synthetic
        assert case.bill.provider.npi.startswith("9")  # real NPIs start with 1 or 2


def test_cases_round_trip_through_jsonl(cases, tmp_path):
    path = write_cases(cases, tmp_path)
    assert read_cases(path) == cases
    assert (tmp_path / "manifest.json").exists()


# --- no accidental errors: independent re-check of every rule -----------------------

def test_arithmetic_matches_answer_key(cases):
    for case in cases:
        bill = case.bill
        line_errors = {}
        for line in bill.lines:
            diff = abs(line.charge_cents - line.units * line.unit_price_cents)
            if diff:
                line_errors[frozenset([line.line_no])] = diff
        assert line_errors == {
            frozenset(f.bill_lines): f.amount_cents for f in flags(case, "arithmetic") if f.target == "line"
        }, case.case_id

        total_diff = abs(bill.total_charges_cents - sum(l.charge_cents for l in bill.lines))
        total_flags = [f.amount_cents for f in flags(case, "arithmetic") if f.target == "total_charges"]
        assert total_flags == ([total_diff] if total_diff else []), case.case_id

        balance_diff = abs(bill.balance_due_cents - (bill.total_charges_cents - bill.payments_cents - bill.adjustments_cents))
        balance_flags = [f.amount_cents for f in flags(case, "arithmetic") if f.target == "balance_due"]
        assert balance_flags == ([balance_diff] if balance_diff else []), case.case_id


def test_duplicates_match_answer_key(cases):
    for case in cases:
        groups = defaultdict(list)
        for line in case.bill.lines:
            groups[(line.code, line.date_of_service, line.rendering_provider_npi, line.modifiers)].append(line)
        found = {frozenset(l.line_no for l in group): group[-1].charge_cents for group in groups.values() if len(group) > 1}
        assert found == expected_by_lines(case, "duplicate"), case.case_id


def test_unit_limits_match_answer_key(cases):
    for case in cases:
        found = {}
        per_day = defaultdict(list)
        for line in case.bill.lines:
            if line.code not in RULES.mue:
                continue
            limit, mai = RULES.mue[line.code]
            if mai == "1":  # claim line edit: each line on its own
                if line.units > limit:
                    found[frozenset([line.line_no])] = (line.units - limit) * line.unit_price_cents
            else:
                per_day[(line.code, line.date_of_service)].append(line)
        for (code, _), lines in per_day.items():
            limit = RULES.mue[code][0]
            units = sum(l.units for l in lines)
            if units > limit:
                found[frozenset(l.line_no for l in lines)] = (units - limit) * lines[0].unit_price_cents
        assert found == expected_by_lines(case, "quantity"), case.case_id


def test_code_pairs_match_answer_key(cases):
    for case in cases:
        found = {}
        z_lines = [l for l in case.bill.lines if l.code.startswith("Z")]
        for a in z_lines:
            for b in z_lines:
                if a.line_no >= b.line_no or a.date_of_service != b.date_of_service:
                    continue
                day = a.date_of_service.isoformat()
                for row in RULES.pair_rows(a.code, b.code):
                    active = row["effective_date"] <= day and (not row["deletion_date"] or day < row["deletion_date"])
                    if not active or row["modifier_indicator"] == "9":
                        continue
                    bypass = set(a.modifiers + b.modifiers) & PTP_ASSOCIATED_MODIFIERS
                    if row["modifier_indicator"] == "1" and bypass:
                        continue
                    second = a if a.code == row["column2"] else b
                    found[frozenset([a.line_no, b.line_no])] = second.charge_cents
        assert found == expected_by_lines(case, "unbundling"), case.case_id


def test_price_benchmarks_match_answer_key(cases):
    for case in cases:
        bill = case.bill
        if bill.is_insured or bill.bill_type != "professional" or case.tags[0] == "cannot_check:outside_release":
            assert not flags(case, "price_benchmark", "benchmark"), case.case_id
            continue
        found = {}
        for line in bill.lines:
            if line.code in POOL:
                setting = PLACE_OF_SERVICE_SETTING[line.place_of_service]
                medicare = POOL[line.code].medicare_cents[(bill.provider.locality_id, setting)]
                if line.unit_price_cents >= PRICE_BENCHMARK_RATIO * medicare:
                    found[frozenset([line.line_no])] = line.charge_cents - medicare * line.units
        assert found == expected_by_lines(case, "price_benchmark", "benchmark"), case.case_id


def test_eob_is_internally_consistent_and_liability_matches_answer_key(cases):
    for case in cases:
        if case.eob is None:
            assert not case.bill.is_insured, case.case_id
            continue
        found = {}
        eob_by_line = {e.line_no: e for e in case.eob.lines}
        for line in case.bill.lines:
            eob = eob_by_line[line.line_no]
            contractual = sum(a.amount_cents for a in eob.adjustments if a.group_code == "CO")
            assert eob.billed_cents == line.charge_cents == eob.allowed_cents + contractual, case.case_id
            assert eob.allowed_cents == eob.paid_cents + eob.patient_responsibility_cents, case.case_id
            assert line.insurance_paid_cents == eob.paid_cents, case.case_id
            assert line.charge_cents == line.insurance_paid_cents + line.adjustment_cents + line.patient_balance_cents
            if line.patient_balance_cents > eob.patient_responsibility_cents:
                found[frozenset([line.line_no])] = line.patient_balance_cents - eob.patient_responsibility_cents
        assert found == expected_by_lines(case, "provider_liability"), case.case_id


def test_cannot_check_cases(cases):
    for case in cases:
        kind = case.tags[0]
        bill_level = [f for f in case.expected if f.kind == "cannot_check" and f.target == "bill"]
        if kind in ("cannot_check:institutional", "cannot_check:outside_release"):
            assert len(bill_level) == 1, case.case_id
        else:
            assert not bill_level, case.case_id
        if kind == "cannot_check:institutional":
            assert case.bill.bill_type == "institutional"
        if kind == "cannot_check:outside_release":
            assert all(l.date_of_service.month == 9 for l in case.bill.lines)
        if kind == "cannot_check:unknown_code":
            (flag,) = [f for f in case.expected if f.kind == "cannot_check"]
            assert case.bill.lines[flag.bill_lines[0] - 1].code == "Y9001"


def test_traps_point_at_lines_with_no_expected_error(cases):
    trap_cases = [c for c in cases if c.tags[0].startswith("trap:")]
    assert len(trap_cases) == 25
    for case in trap_cases:
        assert case.traps, case.case_id
        for check, lines in case.traps:
            assert not [f for f in flags(case, check) if set(f.bill_lines) & set(lines)], case.case_id
