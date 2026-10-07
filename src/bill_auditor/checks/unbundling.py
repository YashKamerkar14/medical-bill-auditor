"""Check 5, unbundling (code-pair edits). SYNTHETIC rules: Z-codes only (ADR 0005).

Every pair of lines on the same date is looked up in the code-pair table
(both column orders). Decision rules (PLAN.md):
- indicator 0: an error; no modifier can bypass it;
- indicator 1 with no PTP-associated modifier on either line: an error;
- indicator 1 with such a modifier: no error (a trap case);
- indicator 9 or a deleted edit: no edit (the lookup already returns "no_edit").
Amount: the charge of the column-2 line. When the insurer paid both lines,
the flag stays but its confidence is medium: Medicare edits are a reference,
not every insurer's rule.
"""

from itertools import combinations

from bill_auditor.checks.context import CheckContext, CheckResult, dollars
from bill_auditor.checks.models import Evidence, Flag
from bill_auditor.guardrails.confidence import confidence_for
from bill_auditor.tables.code_pairs import check_code_pair


def _insurer_paid_both(ctx: CheckContext, a: int, b: int) -> bool:
    if ctx.eob is None:
        return False
    paid = {e.line_no: e.paid_cents for e in ctx.eob.lines}
    eob_for = {m.bill_line: m.eob_line for m in ctx.matches}
    return all(eob_for.get(n) is not None and paid[eob_for[n]] > 0 for n in (a, b))


def run(ctx: CheckContext) -> CheckResult:
    result = CheckResult()
    if not ctx.table_checks:
        return result
    for a, b in combinations(ctx.lines(), 2):
        if a.date_of_service != b.date_of_service or a.code == b.code:
            continue
        pair = check_code_pair(ctx.conn, a.code, b.code, a.date_of_service, a.modifiers, b.modifiers)
        if pair.status != "edit":
            continue
        bypass = pair.bypass_modifiers_on_column1 + pair.bypass_modifiers_on_column2
        if pair.modifier_indicator == "1" and bypass:
            continue  # an allowed modifier is present: no error
        first, second = (a, b) if a.code == pair.column1 else (b, a)
        rule = f"ptp.indicator_{pair.modifier_indicator}"
        confidence = confidence_for(rule)
        if _insurer_paid_both(ctx, a.line_no, b.line_no):
            confidence = "medium"
        label = "SYNTHETIC code-pair edit" if pair.synthetic else "Code-pair edit"
        result.flags.append(Flag(
            check="unbundling", kind="error", bill_lines=(first.line_no, second.line_no),
            amount_cents=second.charge_cents, rule=rule, confidence=confidence,
            evidence=(
                Evidence(source="table_row", table="ptp_edits", release_label=pair.release_label, synthetic=bool(pair.synthetic),
                         values={"column1": pair.column1, "column2": pair.column2,
                                 "effective_date": pair.effective_date.isoformat(),
                                 "deletion_date": pair.deletion_date.isoformat() if pair.deletion_date else None,
                                 "modifier_indicator": pair.modifier_indicator, "rationale": pair.rationale}),
                Evidence(source="bill", values={"column1_line": first.line_no, "column2_line": second.line_no,
                                                "modifiers": {str(first.line_no): list(first.modifiers),
                                                              str(second.line_no): list(second.modifiers)},
                                                "column2_charge_cents": second.charge_cents}),
            ),
            message=(f"Possible error to verify: {second.code} (line {second.line_no}) is not normally billed with "
                     f"{first.code} (line {first.line_no}) on the same date ({label.lower()}, indicator "
                     f"{pair.modifier_indicator}: {pair.modifier_indicator_meaning})."),
            ask=(f"Ask why {second.code} was billed separately from {first.code}; if it is part of the same "
                 f"service, the {dollars(second.charge_cents)} charge should be removed."),
        ))
    return result
