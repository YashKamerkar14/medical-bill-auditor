"""The dispute letter (Milestone 7): a draft a person reviews, edits and sends themselves.

Guardrails (CLAUDE.md, Output):
- built only from verified, high-confidence error flags; price benchmarks,
  medium- and low-confidence flags (for example a provider-liability finding
  that rests on AI-assisted matching) never enter a letter;
- a person must confirm they reviewed every chosen item first (`reviewed`); the
  UI makes this an explicit step, and there is no way around it here;
- cites no law or regulation: the only sources are the table release behind
  each flag and NCCI Policy Manual quotes that passed verification;
- neutral wording, a DRAFT banner and the research-prototype notice; items that
  rest on synthetic rule tables are marked as such;
- traceable: every item is one flag, by id.

The letter is assembled by code from the report and the bill, on this
machine. No model writes it, so no personal detail is sent anywhere, and its
wording is fixed and tested.
"""

import re
from datetime import date

from pydantic import BaseModel

from bill_auditor.checks.context import dollars
from bill_auditor.checks.models import Flag
from bill_auditor.guardrails.wording import wording_problem
from bill_auditor.parsing.schemas import Bill
from bill_auditor.report.report import NOTICE, Report

LETTER_CONFIDENCE = "high"
BANNER = ("DRAFT - REVIEW BEFORE SENDING. Prepared by a research prototype from a synthetic test bill. "
          "Not legal or financial advice.")
SYNTHETIC_NOTE = ("NOTE: items marked [SYNTHETIC] rely on made-up rule tables used for testing; they are not Medicare "
                  "rules. Do not send this letter about a real bill.")
# Citations the letter must never contain unless verified (it verifies none, so: never).
LEGAL_CITATION = re.compile(r"(§|\bU\.?S\.?C\b|\bC\.?F\.?R\b|\bstatute\b|\bregulations?\b|\blaws?\b|\bAct\b)")
SOURCES = {"pfs_amounts": "Medicare Physician Fee Schedule", "ptp_edits": "code-pair (PTP) edit table",
           "mue_limits": "unit limit (MUE) table", "charge_benchmarks": "Medicare average charges"}


class LetterNotAllowed(ValueError):
    """The letter cannot be built as asked (not reviewed, ineligible item, nothing chosen, bad wording)."""


class Letter(BaseModel):
    text: str
    flag_ids: list[str]          # one per item, in order: every claim maps to a verified flag
    total_cents: int


def eligible_flags(report: Report) -> list[Flag]:
    """Error flags a letter may use: verified (only verified flags reach the report) and high confidence."""
    return [f for f in report.errors if f.kind == "error" and f.confidence == LETTER_CONFIDENCE]


def _synthetic(flag: Flag) -> bool:
    return any(e.synthetic for e in flag.evidence)


def _basis(flag: Flag) -> str:
    parts = []
    for e in flag.evidence:
        if e.source == "table_row":
            parts.append(f"{SOURCES.get(e.table, e.table)}, release {e.release_label}")
        elif e.source == "eob":
            parts.append(f"the insurer's explanation of benefits, line {e.values.get('eob_line')}")
        elif e.source == "bill":
            parts.append("the amounts on this statement")
    return "; ".join(dict.fromkeys(parts)) or "the amounts on this statement"


def build_letter(report: Report, bill: Bill, selected_flag_ids: list[str], reviewed: bool,
                 today: date | None = None) -> Letter:
    if not reviewed:
        raise LetterNotAllowed("A person must review every chosen item against the bill and EOB before a letter is drafted.")
    if report.status != "completed" or report.bill_id != bill.bill_id:
        raise LetterNotAllowed("A letter needs a completed audit of this bill.")
    eligible = {f.flag_id: f for f in eligible_flags(report)}
    ineligible = [fid for fid in selected_flag_ids if fid not in eligible]
    if ineligible:
        raise LetterNotAllowed(f"Only verified, high-confidence errors can go in a letter; not eligible: {ineligible}")
    chosen = [eligible[fid] for fid in dict.fromkeys(selected_flag_ids)]
    if not chosen:
        raise LetterNotAllowed("Choose at least one item.")

    p, lines = bill.patient, {l.line_no: l for l in bill.lines}
    reference = f"Statement {bill.bill_id} dated {bill.statement_date.isoformat()}; account {p.account_number}"
    if p.member_id:
        reference += f"; member ID {p.member_id}"
    out = [BANNER]
    if any(_synthetic(f) for f in chosen):
        out.append(SYNTHETIC_NOTE)
    out += ["", (today or date.today()).isoformat(), p.name, p.address, "",
            f"To: Billing Office, {bill.provider.name}", f"Re: {reference}", "", "Dear Billing Office,", "",
            "I am reviewing my statement and would like you to check the items below, which may be billing errors. "
            "Please review each one and send me a corrected itemized statement, or explain why the charge is correct.", ""]
    quotes = {}
    for i, f in enumerate(chosen, 1):
        codes = ", ".join(dict.fromkeys(lines[n].code for n in f.bill_lines if n in lines)) or "the statement totals"
        dates = ", ".join(dict.fromkeys(lines[n].date_of_service.isoformat() for n in f.bill_lines if n in lines))
        where = f"Line{'s' if len(f.bill_lines) > 1 else ''} {', '.join(map(str, f.bill_lines))}" if f.bill_lines else "Totals"
        tag = " [SYNTHETIC]" if _synthetic(f) else ""
        out += [f"{i}. {where} ({codes}{', ' + dates if dates else ''}){tag}: {f.message}",
                f"   Amount in question: {dollars(f.amount_cents)}", f"   Request: {f.ask}", f"   Basis: {_basis(f)}."]
        explanation = report.explanations.get(f.flag_id)
        if explanation is not None and explanation.status == "explained" and explanation.quotes:
            quotes[i] = (explanation.sections, explanation.quotes[0].text.strip())
            out.append(f"   {{QUOTE_{i}}}")
        out.append("")
    total = sum(f.amount_cents for f in chosen)
    out += [f"Total in question: {dollars(total)}", "", "Thank you for your help.", "", p.name, "", "---", NOTICE]

    text = "\n".join(out)
    if wording_problem(text):
        raise LetterNotAllowed(f"Letter wording is not neutral: {wording_problem(text)}")
    if LEGAL_CITATION.search(text):  # before verified quotes are added: the letter itself cites no law
        raise LetterNotAllowed(f"The letter may not cite law or regulation: {LEGAL_CITATION.search(text).group(0)!r}")
    for i, (sections, quote) in quotes.items():  # quotes passed verbatim verification against the manual
        text = text.replace(f"{{QUOTE_{i}}}", f"The NCCI Policy Manual (section {', '.join(sections)}) says: \"{quote}\"")
    return Letter(text=text, flag_ids=[f.flag_id for f in chosen], total_cents=total)
