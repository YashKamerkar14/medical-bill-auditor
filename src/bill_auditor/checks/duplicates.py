"""Check 2, duplicate charge.

Two or more lines with the same code, date of service, rendering provider and
modifiers. A legitimate repeat carries a repeat modifier (76, 77, 91) or an
anatomic one (RT/LT), which makes the modifiers differ, so it is not grouped
here. Amount: the charges of every copy after the first.
"""

from collections import defaultdict

from bill_auditor.checks.context import CheckContext, CheckResult, dollars
from bill_auditor.checks.models import Evidence, Flag
from bill_auditor.guardrails.confidence import confidence_for

RULE = "duplicate.identical_lines"


def run(ctx: CheckContext) -> CheckResult:
    result = CheckResult()
    groups = defaultdict(list)
    for line in ctx.lines():
        key = (line.code, line.date_of_service, line.rendering_provider_npi, tuple(sorted(line.modifiers)))
        groups[key].append(line)

    for (code, dos, _, modifiers), lines in groups.items():
        if len(lines) < 2:
            continue
        repeats = lines[1:]
        amount = sum(l.charge_cents for l in repeats)
        numbers = tuple(l.line_no for l in lines)
        result.flags.append(Flag(
            check="duplicate", kind="error", bill_lines=numbers, amount_cents=amount,
            rule=RULE, confidence=confidence_for(RULE),
            evidence=(Evidence(source="bill", values={
                "lines": list(numbers), "code": code, "date_of_service": dos.isoformat(),
                "modifiers": list(modifiers), "charges_cents": [l.charge_cents for l in lines],
            }),),
            message=(f"Possible error to verify: {code} on {dos} appears on lines "
                     f"{', '.join(map(str, numbers))} with the same provider and modifiers."),
            ask=(f"Ask whether {code} was really performed {len(lines)} times on {dos}; if not, "
                 f"the repeated charge of {dollars(amount)} should be removed."),
        ))
    return result
