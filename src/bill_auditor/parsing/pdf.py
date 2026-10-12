"""PDF bills and EOBs (Milestone 7): render our synthetic statement layout, and parse it back.

Scope: the parser reads the project's own "synthetic itemized statement" and
"synthetic EOB" layouts (written by `render_bill_pdf` / `render_eob_pdf`), not
arbitrary real-world PDFs; real bills are out of scope (CLAUDE.md). Every
section is a bordered table (header fields, line items, totals, notes), which
pdfplumber extracts reliably.

The parser returns plain dicts, never trusted objects: they go through the same
schema validation, injection scan and redaction as JSON input. Text from a PDF
is data. pdfplumber also extracts text a person cannot see (white or tiny
text), so hidden instructions end up in a field where the detector and the
data-only prompts meet them.
"""

import io
import re
from pathlib import Path

import pdfplumber
from fpdf import FPDF
from fpdf.fonts import FontFace

from bill_auditor.parsing.schemas import Bill, Eob

BILL_TITLE = "ITEMIZED STATEMENT - SYNTHETIC TEST BILL"
EOB_TITLE = "EXPLANATION OF BENEFITS - SYNTHETIC TEST DOCUMENT"
LINE_HEADINGS = ("Line", "Date", "Code", "Modifiers", "Units", "Unit price", "Charge", "POS", "Rendering NPI",
                 "Ins. paid", "Adjustment", "Patient owes", "Description")
EOB_HEADINGS = ("Line", "Date", "Code", "Modifiers", "Units", "Billed", "Allowed", "Paid", "Adjustments", "Remark")
NONE = "-"


class PdfParseError(ValueError):
    """The PDF is not one of our synthetic statement layouts, or a value could not be read."""


# --- money and small helpers ------------------------------------------------------------------

def money(cents: int | None) -> str:
    if cents is None:
        return NONE
    sign = "-" if cents < 0 else ""
    return f"{sign}${abs(cents) // 100:,}.{abs(cents) % 100:02d}"


def cents(text: str) -> int | None:
    text = text.strip()
    if text == NONE:
        return None
    found = re.fullmatch(r"(-?)\$([\d,]+)\.(\d{2})", text)
    if not found:
        raise PdfParseError(f"not an amount: {text!r}")
    value = int(found.group(2).replace(",", "")) * 100 + int(found.group(3))
    return -value if found.group(1) else value


def _cell(text) -> str:
    return " ".join(str(text or "").split())  # wrapped cells come back with line breaks


# --- rendering ---------------------------------------------------------------------------------

def _pdf(title: str) -> FPDF:
    pdf = FPDF(orientation="L", unit="mm", format="Letter")
    pdf.set_auto_page_break(auto=True, margin=12)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 8, title, new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", size=8)
    return pdf


def _table(pdf: FPDF, rows: list[tuple], widths: tuple, heading: bool = True) -> None:
    with pdf.table(col_widths=widths, text_align="LEFT", line_height=4.5, first_row_as_headings=heading,
                   headings_style=FontFace(emphasis="BOLD"), repeat_headings=1 if heading else 0) as table:
        for values in rows:
            row = table.row()
            for value in values:
                row.cell(str(value))
    pdf.ln(3)


def _check_latin1(*texts: str) -> None:
    for text in texts:
        try:
            text.encode("latin-1")
        except UnicodeEncodeError as error:
            raise PdfParseError(f"text outside Latin-1 cannot be rendered with the standard PDF fonts: {text[:40]!r}") from error


def render_bill_pdf(bill: Bill) -> bytes:
    p = bill.patient
    _check_latin1(bill.notes, p.name, p.address, bill.provider.name, *(l.description for l in bill.lines))
    pdf = _pdf(BILL_TITLE)
    _table(pdf, [("Field", "Value"),
                 ("Synthetic", "yes" if bill.synthetic else "no"), ("Bill ID", bill.bill_id),
                 ("Bill type", bill.bill_type), ("Statement date", bill.statement_date.isoformat()),
                 ("Patient name", p.name), ("Patient date of birth", p.date_of_birth.isoformat()),
                 ("Patient address", p.address), ("Account number", p.account_number),
                 ("Member ID", p.member_id or NONE), ("Provider", bill.provider.name),
                 ("Provider NPI", bill.provider.npi), ("Pricing locality", bill.provider.locality_id)], (45, 200))
    rows = [LINE_HEADINGS] + [
        (l.line_no, l.date_of_service.isoformat(), l.code, " ".join(l.modifiers) or NONE, l.units,
         money(l.unit_price_cents), money(l.charge_cents), l.place_of_service, l.rendering_provider_npi,
         money(l.insurance_paid_cents), money(l.adjustment_cents), money(l.patient_balance_cents), l.description or NONE)
        for l in bill.lines]
    _table(pdf, rows, (9, 18, 12, 16, 10, 18, 18, 9, 21, 17, 18, 18, 71))
    _table(pdf, [("Totals", "Amount"), ("Total charges", money(bill.total_charges_cents)),
                 ("Payments", money(bill.payments_cents)), ("Adjustments", money(bill.adjustments_cents)),
                 ("Balance due", money(bill.balance_due_cents))], (45, 60))
    _table(pdf, [("Notes",), (bill.notes or NONE,)], (245,))
    return bytes(pdf.output())


def render_eob_pdf(eob: Eob) -> bytes:
    _check_latin1(eob.payer_name, eob.patient_name, *(l.remark for l in eob.lines))
    pdf = _pdf(EOB_TITLE)
    _table(pdf, [("Field", "Value"), ("Synthetic", "yes" if eob.synthetic else "no"), ("EOB ID", eob.eob_id),
                 ("Payer", eob.payer_name), ("Member ID", eob.member_id), ("Patient name", eob.patient_name),
                 ("Claim (bill ID)", eob.claim_bill_id)], (45, 200))
    rows = [EOB_HEADINGS] + [
        (l.line_no, l.date_of_service.isoformat(), l.code, " ".join(l.modifiers) or NONE, l.units, money(l.billed_cents),
         money(l.allowed_cents), money(l.paid_cents),
         "; ".join(f"{a.group_code}-{a.reason_code} {money(a.amount_cents)}" for a in l.adjustments) or NONE,
         l.remark or NONE)
        for l in eob.lines]
    _table(pdf, rows, (9, 18, 12, 16, 10, 18, 18, 18, 46, 80))
    return bytes(pdf.output())


# --- parsing -----------------------------------------------------------------------------------

def _tables(source) -> tuple[str, list[list[list[str]]]]:
    data = source if isinstance(source, bytes) else Path(source).read_bytes()
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            first_text = pdf.pages[0].extract_text() or ""
            tables = [[[_cell(c) for c in row] for row in table] for page in pdf.pages for table in page.extract_tables()]
    except PdfParseError:
        raise
    except Exception as error:  # not a PDF, encrypted, damaged
        raise PdfParseError(f"could not read the PDF: {type(error).__name__}") from error
    return first_text, tables


def _fields(tables, first_heading: str) -> dict[str, str]:
    """Label -> value from every table headed `first_heading` (a table split by a page break repeats its heading)."""
    parts = [table for table in tables if table and table[0][:1] == [first_heading]]
    if not parts:
        raise PdfParseError(f"no {first_heading!r} table found: not a synthetic statement layout")
    return {row[0]: row[1] for table in parts for row in table[1:] if len(row) >= 2}


def _rows(tables, headings: tuple) -> list[list[str]]:
    rows = []
    for table in tables:
        if table and tuple(table[0]) == headings:
            rows += [row for row in table[1:] if row and row[0].isdigit()]  # headings repeat on each page
    if not rows:
        raise PdfParseError("no line-item table found")
    return rows


def _text(value: str) -> str:
    return "" if value == NONE else value


def _mods(value: str) -> list[str]:
    return [] if value == NONE else value.split()


def parse_bill_pdf(source) -> dict:
    """A raw bill dict from one of our synthetic statement PDFs (validate it with intake, like JSON input)."""
    title, tables = _tables(source)
    if BILL_TITLE not in title:
        raise PdfParseError("not a synthetic itemized statement")
    f = _fields(tables, "Field")
    try:
        totals = _fields(tables, "Totals")
        notes = " ".join(row[0] for t in tables if t and t[0] == ["Notes"] for row in t[1:] if row) or NONE
        lines = [{
            "line_no": int(r[0]), "date_of_service": r[1], "code": r[2], "modifiers": _mods(r[3]), "units": int(r[4]),
            "unit_price_cents": cents(r[5]), "charge_cents": cents(r[6]), "place_of_service": r[7],
            "rendering_provider_npi": r[8], "insurance_paid_cents": cents(r[9]), "adjustment_cents": cents(r[10]),
            "patient_balance_cents": cents(r[11]), "description": _text(r[12]),
        } for r in _rows(tables, LINE_HEADINGS)]
        return {
            "synthetic": f["Synthetic"] == "yes", "bill_id": f["Bill ID"], "bill_type": f["Bill type"],
            "statement_date": f["Statement date"],
            "patient": {"name": f["Patient name"], "date_of_birth": f["Patient date of birth"],
                        "address": f["Patient address"], "account_number": f["Account number"],
                        "member_id": None if f["Member ID"] == NONE else f["Member ID"]},
            "provider": {"name": f["Provider"], "npi": f["Provider NPI"], "locality_id": f["Pricing locality"]},
            "lines": lines, "total_charges_cents": cents(totals["Total charges"]), "payments_cents": cents(totals["Payments"]),
            "adjustments_cents": cents(totals["Adjustments"]), "balance_due_cents": cents(totals["Balance due"]),
            "notes": _text(notes),
        }
    except (KeyError, IndexError, ValueError) as error:
        raise PdfParseError(f"could not read the statement: {type(error).__name__}: {error}") from error


def _adjustments(value: str) -> list[dict]:
    if value == NONE:
        return []
    out = []
    for part in value.split(";"):
        found = re.fullmatch(r"([A-Z]{2})-([A-Z]?\d{1,3}) (-?\$[\d,]+\.\d{2})", part.strip())
        if not found:
            raise PdfParseError(f"not an adjustment: {part!r}")
        out.append({"group_code": found.group(1), "reason_code": found.group(2), "amount_cents": cents(found.group(3))})
    return out


def parse_eob_pdf(source) -> dict:
    """A raw EOB dict from one of our synthetic EOB PDFs."""
    title, tables = _tables(source)
    if EOB_TITLE not in title:
        raise PdfParseError("not a synthetic EOB")
    f = _fields(tables, "Field")
    try:
        lines = [{
            "line_no": int(r[0]), "date_of_service": r[1], "code": r[2], "modifiers": _mods(r[3]), "units": int(r[4]),
            "billed_cents": cents(r[5]), "allowed_cents": cents(r[6]), "paid_cents": cents(r[7]),
            "adjustments": _adjustments(r[8]), "remark": _text(r[9]),
        } for r in _rows(tables, EOB_HEADINGS)]
        return {"synthetic": f["Synthetic"] == "yes", "eob_id": f["EOB ID"], "payer_name": f["Payer"],
                "member_id": f["Member ID"], "patient_name": f["Patient name"], "claim_bill_id": f["Claim (bill ID)"],
                "lines": lines}
    except (KeyError, IndexError, ValueError) as error:
        raise PdfParseError(f"could not read the EOB: {type(error).__name__}: {error}") from error
