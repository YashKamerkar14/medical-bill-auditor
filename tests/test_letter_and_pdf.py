"""Milestone 7: the dispute letter's guardrails and the PDF round trip (fake fixtures, no model)."""

from datetime import date

import pytest

from bill_auditor.agent.explain import Explanation, Quote
from bill_auditor.agent.graph import build_graph, run_audit
from bill_auditor.evaluation.hard_cases import build_hard_cases
from bill_auditor.letter.letter import LEGAL_CITATION, LetterNotAllowed, build_letter, eligible_flags
from bill_auditor.parsing.intake import intake
from bill_auditor.parsing.pdf import (PdfParseError, cents, money, parse_bill_pdf, parse_eob_pdf, render_bill_pdf,
                                      render_eob_pdf)
from bill_auditor.parsing.schemas import Bill, Eob
from bill_auditor.report.report import NOTICE

CASES = {c.case_id: c for c in build_hard_cases()}
DAY = date(2026, 12, 1)


def audit(audit_db_path, case_id):
    case = CASES[case_id]
    return run_audit(build_graph(audit_db_path), case.bill, case.eob).report, case.bill


# --- letter --------------------------------------------------------------------------------------

def test_letter_needs_a_human_review_first(audit_db_path):
    report, bill = audit(audit_db_path, "hard-t02")
    ids = [f.flag_id for f in eligible_flags(report)]
    with pytest.raises(LetterNotAllowed, match="review"):
        build_letter(report, bill, ids, reviewed=False)


def test_letter_is_a_traceable_draft_with_the_notice(audit_db_path):
    report, bill = audit(audit_db_path, "hard-t02")  # one synthetic unbundling error
    ids = [f.flag_id for f in eligible_flags(report)]
    letter = build_letter(report, bill, ids, reviewed=True, today=DAY)
    assert letter.flag_ids == ids and letter.total_cents == 50000
    assert letter.text.startswith("DRAFT - REVIEW BEFORE SENDING") and NOTICE in letter.text
    assert "[SYNTHETIC]" in letter.text and "not Medicare rules" in letter.text
    assert bill.patient.name in letter.text  # built locally: the person's own details, never sent anywhere
    assert not LEGAL_CITATION.search(letter.text)


def test_letter_without_synthetic_rules_has_no_synthetic_note(audit_db_path):
    report, bill = audit(audit_db_path, "hard-i02")  # arithmetic error: no rule table involved
    letter = build_letter(report, bill, [f.flag_id for f in eligible_flags(report)], reviewed=True, today=DAY)
    assert "[SYNTHETIC]" not in letter.text and "Amount in question: $45.00" in letter.text


def test_only_high_confidence_errors_are_eligible(audit_db_path):
    report, bill = audit(audit_db_path, "hard-t02")
    flag = report.errors[0]
    medium = report.model_copy(update={"errors": [flag.model_copy(update={"confidence": "medium"})]})
    assert eligible_flags(medium) == []
    with pytest.raises(LetterNotAllowed, match="not eligible"):
        build_letter(medium, bill, [flag.flag_id], reviewed=True)
    with pytest.raises(LetterNotAllowed, match="at least one"):
        build_letter(report, bill, [], reviewed=True)


def test_letter_refuses_another_bill_and_non_neutral_wording(audit_db_path):
    report, bill = audit(audit_db_path, "hard-t02")
    ids = [f.flag_id for f in eligible_flags(report)]
    with pytest.raises(LetterNotAllowed, match="completed audit"):
        build_letter(report, CASES["hard-i02"].bill, ids, reviewed=True)
    tampered = report.model_copy(update={"errors": [report.errors[0].model_copy(update={"message": "Clear fraud."})]})
    with pytest.raises(LetterNotAllowed, match="neutral"):
        build_letter(tampered, bill, ids, reviewed=True)


def test_only_verified_manual_quotes_are_cited(audit_db_path):
    report, bill = audit(audit_db_path, "hard-t02")
    flag = report.errors[0]
    quote = "Medicare regulations allow these codes to be reported together only with a modifier."
    explained = Explanation(flag_id=flag.flag_id, status="explained", text="t", sections=["I.E"], quotes_verified=1,
                            quotes=[Quote(passage_id="I.E#1", text=quote)])
    unverified = Explanation(flag_id=flag.flag_id, status="unverified", note="dropped")
    for explanation, shown in ((explained, True), (unverified, False)):
        r = report.model_copy(update={"explanations": {flag.flag_id: explanation}})
        text = build_letter(r, bill, [flag.flag_id], reviewed=True, today=DAY).text
        assert (quote in text) is shown  # a verified quote may say "regulations"; the letter itself never cites any


# --- PDF -------------------------------------------------------------------------------------------

@pytest.mark.parametrize("case_id", sorted(CASES))
def test_pdf_round_trip_is_exact(case_id):
    case = CASES[case_id]
    assert Bill.model_validate(parse_bill_pdf(render_bill_pdf(case.bill))) == case.bill
    if case.eob is not None:
        assert Eob.model_validate(parse_eob_pdf(render_eob_pdf(case.eob))) == case.eob


def test_money_round_trip():
    for value in (0, 5, 123456, -4500, None):
        assert cents(money(value)) == value
    with pytest.raises(PdfParseError):
        cents("12 dollars")


def test_not_our_layout_is_refused():
    with pytest.raises(PdfParseError):
        parse_bill_pdf(b"not a pdf at all")
    with pytest.raises(PdfParseError, match="not a synthetic itemized statement"):
        parse_bill_pdf(render_eob_pdf(CASES["hard-m01"].eob))


def test_text_from_a_pdf_is_still_data(audit_db):
    """An instruction rendered into a PDF comes back as a field, where the injection detector reports it."""
    bill = CASES["hard-i02"].bill
    raw = parse_bill_pdf(render_bill_pdf(bill))
    result = intake(audit_db, raw)
    assert result.accepted and any("bill line 1 description" in w for w in result.input_warnings)


def test_text_outside_latin1_cannot_be_rendered():
    bill = CASES["hard-t02"].bill
    with pytest.raises(PdfParseError, match="Latin-1"):
        render_bill_pdf(bill.model_copy(update={"notes": "Paid ✓"}))


def test_load_document_accepts_json_and_pdf_only():
    from bill_auditor.ui.core import InputError, load_document

    bill = CASES["hard-t02"].bill
    assert Bill.model_validate(load_document("b.pdf", render_bill_pdf(bill), "bill")) == bill
    assert load_document("b.json", b'{"a": 1}', "bill") == {"a": 1}
    for name, data in (("b.json", b"{nope"), ("b.exe", b"MZ"), ("b.pdf", b"junk")):
        with pytest.raises(InputError):
            load_document(name, data, "bill")
