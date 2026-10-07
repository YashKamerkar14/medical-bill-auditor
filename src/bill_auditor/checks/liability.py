"""Check 1, billed for provider liability (insured bills): the core check.

For each bill line matched to an EOB line: if the bill asks the patient for
more than the EOB's patient responsibility (the sum of its PR adjustments),
the difference is a possible error. Typical causes: a contractual (CO)
adjustment not written off, coinsurance computed on the charge instead of the
allowed amount, or a line the insurer denied as provider liability billed to
the patient anyway.
"""

from bill_auditor.checks.context import CheckContext, CheckResult, dollars
from bill_auditor.checks.models import CannotCheck, Evidence, Flag
from bill_auditor.guardrails.confidence import confidence_for

RULE = "liability.balance_exceeds_eob"


def run(ctx: CheckContext) -> CheckResult:
    result = CheckResult()
    if not ctx.bill.is_insured or ctx.eob is None:
        return result  # intake already reported "insured but no EOB"
    eob_lines = {e.line_no: e for e in ctx.eob.lines}
    by_bill_line = {m.bill_line: m for m in ctx.matches}

    for line in ctx.lines():
        match = by_bill_line[line.line_no]
        if match.eob_line is None:
            reason = ("Several EOB lines could answer this line; model-assisted matching is needed"
                      if match.method == "ambiguous" else "No EOB line answers this service")
            result.cannot.append(CannotCheck(check="provider_liability", target="line",
                                             bill_lines=(line.line_no,), reason=reason))
            continue
        if line.patient_balance_cents is None:
            continue
        eob_line = eob_lines[match.eob_line]
        owed = eob_line.patient_responsibility_cents
        extra = line.patient_balance_cents - owed
        if extra <= 0:
            continue
        result.flags.append(Flag(
            check="provider_liability", kind="error", bill_lines=(line.line_no,), amount_cents=extra,
            rule=RULE, confidence=confidence_for(RULE),
            evidence=(
                Evidence(source="bill", values={"line_no": line.line_no, "patient_balance_cents": line.patient_balance_cents}),
                Evidence(source="eob", values={
                    "eob_line": eob_line.line_no, "match_method": match.method,
                    "patient_responsibility_cents": owed, "paid_cents": eob_line.paid_cents,
                    "adjustments": [a.model_dump() for a in eob_line.adjustments],
                }),
            ),
            message=(f"Possible error to verify: line {line.line_no} ({line.code}) bills the patient "
                     f"{dollars(line.patient_balance_cents)}, but the EOB says the patient owes {dollars(owed)}."),
            ask=(f"Ask the billing office why line {line.line_no} shows {dollars(extra)} more than the insurer's "
                 "patient responsibility, and whether the contractual adjustment was applied."),
        ))
    return result
