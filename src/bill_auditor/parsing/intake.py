"""Intake: validate the input and decide, before any check runs, what cannot be checked.

Guardrails applied here (CLAUDE.md, Input):
- Bill and EOB must match their schemas; malformed input is rejected with a clear message.
- Only bills marked synthetic are accepted unless `allow_non_synthetic` is set.
- Out-of-scope bill types (UB-04 / institutional) and dates outside the loaded
  releases are reported as "cannot check", never guessed.
- Unknown codes are reported as "cannot check" for their line.
- An insured bill without an EOB cannot have its patient balances checked.
"""

import sqlite3

from pydantic import BaseModel, ValidationError

from bill_auditor.checks.models import CannotCheck
from bill_auditor.guardrails.injection import scan
from bill_auditor.parsing.schemas import Bill, Eob
from bill_auditor.tables.codes import is_known_code
from bill_auditor.tables.releases import find_release

TABLE_DATASETS = ("pfs", "ptp", "mue", "charges")


class IntakeResult(BaseModel):
    accepted: bool
    errors: list[str] = []
    bill: Bill | None = None
    eob: Eob | None = None
    in_scope: bool = False              # False: nothing beyond "cannot check" is reported
    table_checks: bool = False          # False: dates have no loaded release, so no table lookups
    cannot_check: list[CannotCheck] = []
    releases: dict[str, str] = {}       # dataset -> release label used for this bill
    input_warnings: list[str] = []      # free text that looks like instructions to an AI (Milestone 6)


def _validation_message(what: str, error: ValidationError) -> str:
    first = error.errors()[0]
    where = ".".join(str(p) for p in first["loc"])
    return f"{what} is not valid ({error.error_count()} problem(s)); first: {where}: {first['msg']}"


def intake(conn: sqlite3.Connection, raw_bill, raw_eob=None, allow_non_synthetic: bool = False) -> IntakeResult:
    try:
        bill = raw_bill if isinstance(raw_bill, Bill) else Bill.model_validate(raw_bill)
    except ValidationError as error:
        return IntakeResult(accepted=False, errors=[_validation_message("Bill", error)])
    try:
        eob = None if raw_eob is None else raw_eob if isinstance(raw_eob, Eob) else Eob.model_validate(raw_eob)
    except ValidationError as error:
        return IntakeResult(accepted=False, errors=[_validation_message("EOB", error)])

    if not allow_non_synthetic and (not bill.synthetic or (eob is not None and not eob.synthetic)):
        return IntakeResult(accepted=False, errors=["Only synthetic bills are accepted (set allow_non_synthetic to override)."])
    if eob is not None and eob.claim_bill_id != bill.bill_id:
        return IntakeResult(accepted=False, errors=[f"The EOB answers bill {eob.claim_bill_id}, not {bill.bill_id}."])

    result = IntakeResult(accepted=True, bill=bill, eob=eob, input_warnings=[
        f"{where} contains text that looks like instructions to an AI ({', '.join(found)}); it was treated as data "
        "and not shown to the model" for where, found in scan(bill, eob)])
    if bill.bill_type != "professional":
        result.cannot_check.append(CannotCheck(check=None, target="bill",
                                               reason="Institutional (UB-04) bills are out of scope"))
        return result
    result.in_scope = True

    # Releases: every line's date of service must fall inside a loaded release.
    dates = sorted({line.date_of_service for line in bill.lines})
    missing = [d for d in dates if find_release(conn, "pfs", d) is None]
    if len(missing) == len(dates):
        result.cannot_check.append(CannotCheck(
            check=None, target="bill",
            reason=f"No table release is loaded for the dates of service ({dates[0]} to {dates[-1]})"))
    else:
        result.table_checks = True
        for line in bill.lines:
            if line.date_of_service in missing:
                result.cannot_check.append(CannotCheck(
                    check=None, target="line", bill_lines=(line.line_no,),
                    reason=f"No table release is loaded for {line.date_of_service}"))
        anchor = next(d for d in dates if d not in missing)
        for dataset in TABLE_DATASETS:
            release = find_release(conn, dataset, anchor)
            if release:
                result.releases[dataset] = release.label

        for line in bill.lines:
            if line.date_of_service not in missing and not is_known_code(conn, line.code, line.date_of_service):
                result.cannot_check.append(CannotCheck(
                    check=None, target="line", bill_lines=(line.line_no,),
                    reason=f"Code {line.code} is not in any loaded table"))

    if bill.is_insured and eob is None:
        result.cannot_check.append(CannotCheck(
            check="provider_liability", target="bill",
            reason="Insured bill but no EOB provided: patient responsibility cannot be checked"))
    return result


def unknown_lines(result: IntakeResult) -> set[int]:
    """Lines no check should look at: unknown code or no release for their date."""
    return {n for c in result.cannot_check if c.check is None and c.target == "line" for n in c.bill_lines}
