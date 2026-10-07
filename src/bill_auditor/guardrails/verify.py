"""Verify: no flag reaches the report unless code re-confirms it (PLAN.md, node 5).

For every flag:
1. Rule re-run: running its check again on the same bill produces the same
   flag (same lines, same amount).
2. Row exists: every cited table row is read back from the database under the
   stated release, and its values must equal the cited ones.
3. Amount recomputed: the dollar amount is recomputed from the evidence with a
   separate formula written here, not by calling the check again.
A flag failing any step is dropped and the reason recorded. Quote checks for
manual passages are added in Milestone 3.
"""

from pydantic import BaseModel

from bill_auditor.checks import arithmetic, duplicates, liability, price, quantity, unbundling
from bill_auditor.checks.context import CheckContext
from bill_auditor.checks.models import Evidence, Flag

CHECKS = {
    "provider_liability": liability.run,
    "duplicate": duplicates.run,
    "arithmetic": arithmetic.run,
    "quantity": quantity.run,
    "unbundling": unbundling.run,
    "price_benchmark": price.run,
}


class DroppedFlag(BaseModel):
    flag: Flag
    reason: str


def _row_exists(ctx: CheckContext, evidence: Evidence) -> str | None:
    """None if the cited row is in the database under the stated release, else why not."""
    release = ctx.conn.execute("SELECT release_id, synthetic FROM releases WHERE label = ?",
                               (evidence.release_label,)).fetchone()
    if release is None:
        return f"release {evidence.release_label!r} is not loaded"
    if bool(release["synthetic"]) != evidence.synthetic:
        return "the synthetic label on the evidence does not match its release"
    v, rid = evidence.values, release["release_id"]
    if evidence.table == "ptp_edits":
        row = ctx.conn.execute(
            "SELECT modifier_indicator, deletion_date FROM ptp_edits WHERE release_id = ? AND column1 = ? "
            "AND column2 = ? AND effective_date = ?", (rid, v["column1"], v["column2"], v["effective_date"])).fetchone()
        ok = row is not None and row["modifier_indicator"] == v["modifier_indicator"] and row["deletion_date"] == v["deletion_date"]
    elif evidence.table == "mue_limits":
        row = ctx.conn.execute("SELECT mue_value, mai FROM mue_limits WHERE release_id = ? AND code = ?",
                               (rid, v["code"])).fetchone()
        ok = row is not None and (row["mue_value"], row["mai"]) == (v["mue_value"], v["mai"])
    elif evidence.table == "pfs_amounts":
        column = "facility_cents" if v["setting"] == "facility" else "nonfacility_cents"
        row = ctx.conn.execute(
            f"SELECT {column} AS amount FROM pfs_amounts WHERE release_id = ? AND carrier = ? AND locality = ? "
            "AND code = ? AND modifier = ?",
            (rid, v["locality_id"][:5], v["locality_id"][5:], v["code"], v["pricing_modifier"])).fetchone()
        ok = row is not None and row["amount"] == v["amount_cents"]
    elif evidence.table == "charge_benchmarks":
        return None  # context only; no amount depends on it
    else:
        return f"unknown evidence table {evidence.table!r}"
    return None if ok else f"cited {evidence.table} row does not match the database"


def _recompute(ctx: CheckContext, flag: Flag) -> int | None:
    """The flag's amount, recomputed from its evidence and the bill (independent formulas)."""
    lines = {l.line_no: l for l in ctx.bill.lines}
    table = next((e for e in flag.evidence if e.source == "table_row"), None)
    if flag.check == "provider_liability":
        eob = next(e for e in flag.evidence if e.source == "eob").values
        pr = sum(a["amount_cents"] for a in eob["adjustments"] if a["group_code"] == "PR")
        return lines[flag.bill_lines[0]].patient_balance_cents - pr
    if flag.check == "duplicate":
        return sum(lines[n].charge_cents for n in flag.bill_lines[1:])
    if flag.check == "arithmetic":
        b = ctx.bill
        if flag.target == "line":
            line = lines[flag.bill_lines[0]]
            return abs(line.charge_cents - line.units * line.unit_price_cents)
        if flag.target == "total_charges":
            return abs(b.total_charges_cents - sum(l.charge_cents for l in b.lines))
        return abs(b.balance_due_cents - (b.total_charges_cents - b.payments_cents - b.adjustments_cents))
    if flag.check == "quantity":
        units = sum(lines[n].units for n in flag.bill_lines)
        return (units - table.values["mue_value"]) * lines[flag.bill_lines[0]].unit_price_cents
    if flag.check == "unbundling":
        column2 = next(n for n in flag.bill_lines if lines[n].code == table.values["column2"])
        return lines[column2].charge_cents
    if flag.check == "price_benchmark":
        line = lines[flag.bill_lines[0]]
        return line.charge_cents - table.values["amount_cents"] * line.units
    return None


def verify(ctx: CheckContext, flags: list[Flag]) -> tuple[list[Flag], list[DroppedFlag]]:
    kept, dropped = [], []
    rerun_cache: dict[str, list[Flag]] = {}
    for flag in flags:
        if flag.check not in rerun_cache:
            rerun_cache[flag.check] = CHECKS[flag.check](ctx).flags
        rerun = {f.flag_id: f for f in rerun_cache[flag.check]}
        reason = None
        if flag.flag_id not in rerun or rerun[flag.flag_id].amount_cents != flag.amount_cents:
            reason = "re-running the rule does not reproduce this flag"
        if reason is None:
            for evidence in flag.evidence:
                if evidence.source == "table_row":
                    reason = _row_exists(ctx, evidence)
                    if reason:
                        break
        if reason is None:
            try:
                recomputed = _recompute(ctx, flag)
            except (KeyError, StopIteration, TypeError):
                recomputed = None
            if recomputed != flag.amount_cents:
                reason = f"amount does not recompute (flag says {flag.amount_cents}, recomputed {recomputed})"
        if reason:
            dropped.append(DroppedFlag(flag=flag, reason=reason))
        else:
            kept.append(flag)
    return kept, dropped
