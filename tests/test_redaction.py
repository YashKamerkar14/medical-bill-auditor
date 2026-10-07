"""Redaction: no personal value survives anywhere in what a model would see."""

import json

import pytest

from bill_auditor.evaluation.hard_cases import build_hard_cases
from bill_auditor.baselines.model_alone import build_user_message
from bill_auditor.parsing.redaction import contains_value, redact, restore


@pytest.fixture(scope="module")
def cases():
    return build_hard_cases()


def personal_values(case):
    p = case.bill.patient
    values = [p.name, p.address, p.account_number, p.date_of_birth.isoformat(),
              p.date_of_birth.strftime("%m/%d/%Y"), case.bill.provider.name]
    if p.member_id:
        values.append(p.member_id)
    return values


def test_no_personal_value_survives_in_any_case(cases):
    for case in cases:
        view = redact(case.bill, case.eob)
        dumped = json.dumps({"bill": view.bill, "eob": view.eob}).lower()
        for value in personal_values(case):
            assert value.lower() not in dumped, (case.case_id, value)


def test_values_hidden_inside_free_text_are_scrubbed(cases):
    case = next(c for c in cases if c.eob is not None)
    p = case.bill.patient
    note = f"Call {p.name} ({p.member_id}) at {p.address}; DOB {p.date_of_birth:%m/%d/%Y}. Mr. {p.name.split()[-1]} agreed."
    bill = case.bill.model_copy(update={"notes": note})
    view = redact(bill, case.eob)
    assert view.bill["notes"] == (
        "Call [PATIENT_NAME] ([MEMBER_ID]) at [PATIENT_ADDRESS]; DOB [DATE_OF_BIRTH_US]. Mr. [PATIENT_LAST_NAME] agreed."
    )
    assert restore(view.bill["notes"], view.mapping) == note


def test_names_are_matched_as_whole_words_only(cases):
    """Regression (2026-10-07): a patient surnamed 'Tate' tripped the safety check via 'statement_date'."""
    case = next(c for c in cases if not c.bill.is_insured)
    patient = case.bill.patient.model_copy(update={"name": "Jamie Tate"})
    bill = case.bill.model_copy(update={"patient": patient, "notes": "Statement sent; Ms. Tate will call. Tates' address unknown."})
    view = redact(bill)
    assert view.bill["notes"] == "Statement sent; Ms. [PATIENT_LAST_NAME] will call. [PATIENT_LAST_NAME]' address unknown."
    assert contains_value('"statement_date": "2026-12-08"', "Tate") is False
    assert contains_value("Ms. Tate called", "tate") is True
    assert build_user_message(case.model_copy(update={"bill": bill.model_copy(update={"notes": ""})}))


def test_codes_amounts_and_dates_of_service_are_kept(cases):
    case = cases[0]
    view = redact(case.bill, case.eob)
    for original, shown in zip(case.bill.lines, view.bill["lines"]):
        assert (shown["code"], shown["charge_cents"], shown["date_of_service"]) == (
            original.code, original.charge_cents, original.date_of_service.isoformat())
    assert view.bill["provider"]["npi"] == case.bill.provider.npi


def test_eob_identity_fields_use_the_same_placeholders(cases):
    case = next(c for c in cases if c.eob is not None)
    view = redact(case.bill, case.eob)
    assert (view.eob["patient_name"], view.eob["member_id"]) == ("[PATIENT_NAME]", "[MEMBER_ID]")


def test_self_pay_bill_has_no_member_placeholder(cases):
    case = next(c for c in cases if not c.bill.is_insured)
    view = redact(case.bill)
    assert view.bill["patient"]["member_id"] is None
    assert "[MEMBER_ID]" not in view.mapping
