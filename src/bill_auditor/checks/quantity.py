"""Check 4, quantity over the unit limit (MUE). SYNTHETIC rules: Z-codes only (ADR 0005).

MAI 1 is a claim-line edit: each line is compared with the limit on its own.
MAI 2 and 3 are per-day edits: units of the code on all lines for that date
are added up first (manual Chapter I, section V). Amount: units over the limit
x unit price. A code with no published limit is "cannot check", never "pass".
"""

from collections import defaultdict

from bill_auditor.checks.context import CheckContext, CheckResult, dollars
from bill_auditor.checks.models import CannotCheck, Evidence, Flag
from bill_auditor.guardrails.confidence import confidence_for
from bill_auditor.tables.unit_limits import get_unit_limit


def _flag(lines, limit, units) -> Flag:
    first = lines[0]
    excess = units - limit.mue_value
    rule = f"mue.mai_{limit.mai}"
    numbers = tuple(l.line_no for l in lines)
    label = "SYNTHETIC unit limit" if limit.synthetic else "Unit limit"
    return Flag(
        check="quantity", kind="error", bill_lines=numbers, amount_cents=excess * first.unit_price_cents,
        rule=rule, confidence=confidence_for(rule),
        evidence=(
            Evidence(source="table_row", table="mue_limits", release_label=limit.release_label, synthetic=bool(limit.synthetic),
                     values={"code": limit.code, "mue_value": limit.mue_value, "mai": limit.mai, "rationale": limit.rationale}),
            Evidence(source="bill", values={"lines": list(numbers), "units": [l.units for l in lines],
                                            "unit_price_cents": first.unit_price_cents}),
        ),
        message=(f"Possible error to verify: {units} units of {first.code} on {first.date_of_service}; "
                 f"the {label.lower()} is {limit.mue_value} (MAI {limit.mai}: {limit.mai_meaning})."),
        ask=(f"Ask for documentation supporting {units} units of {first.code}; "
             f"{excess} unit(s) over the limit come to {dollars(excess * first.unit_price_cents)}."),
    )


def run(ctx: CheckContext) -> CheckResult:
    result = CheckResult()
    if not ctx.table_checks:
        return result
    per_day = defaultdict(list)
    for line in ctx.lines():
        limit = get_unit_limit(ctx.conn, line.code, line.date_of_service)
        if limit.status != "limit":
            result.cannot.append(CannotCheck(check="quantity", target="line", bill_lines=(line.line_no,),
                                             reason=limit.message))
            continue
        if limit.mai == "1":
            if line.units > limit.mue_value:
                result.flags.append(_flag([line], limit, line.units))
        else:
            per_day[(line.code, line.date_of_service)].append((line, limit))

    for entries in per_day.values():
        lines = [line for line, _ in entries]
        limit = entries[0][1]
        units = sum(l.units for l in lines)
        if units > limit.mue_value:
            result.flags.append(_flag(lines, limit, units))
    return result
