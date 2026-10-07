"""Check 3, arithmetic: line charges, total charges, balance due.

Pure arithmetic on the bill's own numbers; this is exactly where the
model-alone baseline was weakest (most of its false flags were mis-added
totals), and where code is always right.
"""

from bill_auditor.checks.context import CheckContext, CheckResult, dollars
from bill_auditor.checks.models import Evidence, Flag
from bill_auditor.guardrails.confidence import confidence_for


def run(ctx: CheckContext) -> CheckResult:
    result = CheckResult()
    bill = ctx.bill

    for line in bill.lines:  # all lines: arithmetic needs no table, so unknown codes are still checked
        expected = line.units * line.unit_price_cents
        if line.charge_cents != expected:
            diff = abs(line.charge_cents - expected)
            result.flags.append(Flag(
                check="arithmetic", kind="error", target="line", bill_lines=(line.line_no,), amount_cents=diff,
                rule="arithmetic.line", confidence=confidence_for("arithmetic.line"),
                evidence=(Evidence(source="bill", values={
                    "line_no": line.line_no, "units": line.units, "unit_price_cents": line.unit_price_cents,
                    "charge_cents": line.charge_cents}),),
                message=(f"Possible error to verify: line {line.line_no} is {line.units} x "
                         f"{dollars(line.unit_price_cents)} = {dollars(expected)}, but is charged {dollars(line.charge_cents)}."),
                ask=f"Ask the billing office to correct the charge on line {line.line_no} or explain the difference.",
            ))

    line_sum = sum(line.charge_cents for line in bill.lines)
    if bill.total_charges_cents != line_sum:
        diff = abs(bill.total_charges_cents - line_sum)
        result.flags.append(Flag(
            check="arithmetic", kind="error", target="total_charges", amount_cents=diff,
            rule="arithmetic.total_charges", confidence=confidence_for("arithmetic.total_charges"),
            evidence=(Evidence(source="bill", values={
                "total_charges_cents": bill.total_charges_cents, "sum_of_lines_cents": line_sum}),),
            message=(f"Possible error to verify: the lines add up to {dollars(line_sum)}, "
                     f"but total charges are {dollars(bill.total_charges_cents)}."),
            ask="Ask the billing office to reconcile the total with the line charges.",
        ))

    expected_balance = bill.total_charges_cents - bill.payments_cents - bill.adjustments_cents
    if bill.balance_due_cents != expected_balance:
        diff = abs(bill.balance_due_cents - expected_balance)
        result.flags.append(Flag(
            check="arithmetic", kind="error", target="balance_due", amount_cents=diff,
            rule="arithmetic.balance_due", confidence=confidence_for("arithmetic.balance_due"),
            evidence=(Evidence(source="bill", values={
                "total_charges_cents": bill.total_charges_cents, "payments_cents": bill.payments_cents,
                "adjustments_cents": bill.adjustments_cents, "balance_due_cents": bill.balance_due_cents}),),
            message=(f"Possible error to verify: charges minus payments minus adjustments is "
                     f"{dollars(expected_balance)}, but the balance due is {dollars(bill.balance_due_cents)}."),
            ask="Ask the billing office to recalculate the balance due.",
        ))
    return result
