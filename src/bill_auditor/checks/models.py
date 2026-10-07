"""What a check produces: flags that carry their own evidence, and "cannot check" notes.

A flag is a *possible* error. It names the rule it rests on, the evidence (a
table row with its release, or the bill and EOB figures used), a dollar amount
defined per check (docs/decisions/0006), and a confidence set by rule
(guardrails/confidence.py), never by a model.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict

from bill_auditor.evaluation.schemas import BillTarget, CheckType, PredictedFlag

Confidence = Literal["high", "medium", "low"]
_STRICT = ConfigDict(extra="forbid", frozen=True)


class Evidence(BaseModel):
    model_config = _STRICT
    source: Literal["table_row", "bill", "eob"]
    table: str | None = None            # e.g. "ptp_edits", "mue_limits", "pfs_amounts"
    release_label: str | None = None
    synthetic: bool = False             # True for our made-up PTP/MUE tables (ADR 0005)
    values: dict                        # the cited row, or the figures used from the bill/EOB


class Flag(BaseModel):
    model_config = _STRICT
    check: CheckType
    kind: Literal["error", "benchmark"]
    target: BillTarget = "line"
    bill_lines: tuple[int, ...] = ()
    amount_cents: int
    rule: str                           # e.g. "ptp.indicator_0", "mue.mai_2", "arithmetic.balance_due"
    confidence: Confidence
    evidence: tuple[Evidence, ...]
    message: str                        # neutral wording: a possible error to verify
    ask: str                            # what to ask the billing office or insurer

    @property
    def flag_id(self) -> str:
        lines = ",".join(str(n) for n in self.bill_lines)
        return f"{self.check}:{self.target}:{lines}"


class CannotCheck(BaseModel):
    model_config = _STRICT
    check: CheckType | None             # None = nothing on this bill / line can be checked
    target: BillTarget = "line"
    bill_lines: tuple[int, ...] = ()
    reason: str


def to_predicted(flags: list[Flag], cannot: list[CannotCheck]) -> list[PredictedFlag]:
    """The shape the scorer compares with the answer key."""
    out = [PredictedFlag(check=f.check, kind=f.kind, target=f.target, bill_lines=list(f.bill_lines),
                         amount_cents=f.amount_cents, explanation=f.message) for f in flags]
    out += [PredictedFlag(check=c.check, kind="cannot_check", target=c.target, bill_lines=list(c.bill_lines),
                          explanation=c.reason) for c in cannot]
    return out
