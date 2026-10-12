"""Check 6, price benchmark (self-pay bills). A BENCHMARK, never an error.

A line's stated unit price is compared with the Medicare fee schedule amount
for its code, pricing modifier, locality and setting. At or above
PRICE_BENCHMARK_RATIO times Medicare it is reported as a benchmark for
negotiation, with the CMS average billed charge as context. Charging more than
Medicare is normal and is never called an error.
"""

from bill_auditor.checks.context import CheckContext, CheckResult, dollars, lookup_unavailable
from bill_auditor.checks.models import CannotCheck, Evidence, Flag
from bill_auditor.checks.settings import PRICE_BENCHMARK_RATIO, PRICE_CHECK_SELF_PAY_ONLY
from bill_auditor.guardrails.confidence import confidence_for
from bill_auditor.parsing.schemas import PLACE_OF_SERVICE_SETTING
from bill_auditor.lookups import LookupUnavailable
from bill_auditor.tables.fee_schedule import PRICING_MODIFIERS

RULE = "price.ratio"


def run(ctx: CheckContext) -> CheckResult:
    result = CheckResult()
    if not ctx.table_checks or (PRICE_CHECK_SELF_PAY_ONLY and ctx.bill.is_insured):
        return result
    locality = ctx.bill.provider.locality_id
    for line in ctx.lines():
        setting = PLACE_OF_SERVICE_SETTING[line.place_of_service]
        modifier = next((m for m in line.modifiers if m in PRICING_MODIFIERS), None)
        try:
            fee = ctx.lookups.get_fee_schedule_amount(line.code, modifier, locality, setting, line.date_of_service)
        except LookupUnavailable as error:
            result.cannot.append(lookup_unavailable("price_benchmark", [line.line_no], error))
            continue
        if fee.status != "found" or not fee.amount_cents:
            result.cannot.append(CannotCheck(check="price_benchmark", target="line", bill_lines=(line.line_no,),
                                             reason=fee.message if fee.status != "found" else "Medicare amount is $0"))
            continue
        if line.unit_price_cents < PRICE_BENCHMARK_RATIO * fee.amount_cents:
            continue
        medicare_total = fee.amount_cents * line.units
        try:
            typical = ctx.lookups.get_charge_benchmark(line.code, setting, line.date_of_service)
        except LookupUnavailable:
            typical = None  # price context only: the benchmark flag stands without it
        evidence = [Evidence(source="table_row", table="pfs_amounts", release_label=fee.release_label, values={
            "code": fee.code, "pricing_modifier": fee.pricing_modifier, "locality_id": locality, "setting": setting,
            "amount_cents": fee.amount_cents, "source_part": fee.source_part})]
        context = ""
        if typical is not None and typical.status == "found":
            evidence.append(Evidence(source="table_row", table="charge_benchmarks", release_label=typical.release_label,
                                     values={"code": typical.code, "geography": typical.geography, "setting": setting,
                                             "avg_submitted_cents": typical.avg_submitted_cents,
                                             "avg_allowed_cents": typical.avg_allowed_cents}))
            context = f" Providers billed Medicare {dollars(typical.avg_submitted_cents)} on average for this service."
        ratio = line.unit_price_cents / fee.amount_cents
        result.flags.append(Flag(
            check="price_benchmark", kind="benchmark", bill_lines=(line.line_no,),
            amount_cents=line.charge_cents - medicare_total, rule=RULE, confidence=confidence_for(RULE),
            evidence=tuple(evidence),
            message=(f"Price benchmark (not an error): line {line.line_no} ({line.code}) is priced at "
                     f"{dollars(line.unit_price_cents)} per unit, {ratio:.1f} times the Medicare amount of "
                     f"{dollars(fee.amount_cents)} for this locality.{context}"),
            ask=("As a self-pay patient you can ask for a discount or a price closer to the Medicare amount; "
                 "this is a negotiation point, not a billing error."),
        ))
    return result
