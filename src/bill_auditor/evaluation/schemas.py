"""Test cases and the answer key, plus the shape every system's output must take.

The dollar amount of each flag is DEFINED here once per check, so scoring can
demand exact equality (docs/decisions/0006-evaluation-design.md):

| check              | amount_cents                                                          |
| ------------------ | --------------------------------------------------------------------- |
| provider_liability | patient balance on the line minus the EOB patient responsibility      |
| duplicate          | charge of the repeated line                                           |
| arithmetic         | absolute difference between the stated and the recomputed figure      |
| quantity           | units over the limit x unit price                                     |
| unbundling         | charge of the column-2 line                                           |
| price_benchmark    | charge minus Medicare amount x units (a benchmark, not an error)      |
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from bill_auditor.parsing.schemas import Bill, Eob

CheckType = Literal["provider_liability", "duplicate", "arithmetic", "quantity", "unbundling", "price_benchmark"]
FlagKind = Literal["error", "benchmark", "cannot_check"]

# Where a flag points when it is not about specific lines.
BillTarget = Literal["line", "total_charges", "balance_due", "bill"]

# Checks whose rules come from our synthetic tables; reported separately because
# no model can know rules for invented codes (docs/decisions/0005).
SYNTHETIC_RULE_CHECKS = ("quantity", "unbundling")

_STRICT = ConfigDict(extra="forbid", frozen=True)


class ExpectedFlag(BaseModel):
    """One entry of the answer key."""

    model_config = _STRICT
    check: CheckType | None  # None for bill-level "cannot check" (out of scope, no release)
    kind: FlagKind
    target: BillTarget = "line"
    bill_lines: tuple[int, ...] = ()
    amount_cents: int | None = None
    reason: str  # short human explanation of what was planted


class Case(BaseModel):
    model_config = _STRICT
    case_id: str
    source: Literal["generated", "hard"]
    split: Literal["dev", "heldout"]
    tags: tuple[str, ...] = ()        # e.g. ("clean",), ("trap:modifier_59",), ("injection",)
    bill: Bill
    eob: Eob | None = None
    expected: tuple[ExpectedFlag, ...] = ()
    # Lines a trap is about, with the check that must NOT fire on them.
    traps: tuple[tuple[CheckType, tuple[int, ...]], ...] = ()

    @property
    def is_clean(self) -> bool:
        return not any(f.kind == "error" for f in self.expected)


class PredictedFlag(BaseModel):
    """What a system under test reports. Same vocabulary as the answer key."""

    model_config = ConfigDict(extra="forbid")
    check: CheckType | None = Field(description="Which check; null only for a whole-bill 'cannot check'")
    kind: FlagKind
    target: BillTarget = Field(default="line", description="'line' for line-level flags; otherwise what the flag is about")
    bill_lines: list[int] = Field(default_factory=list, description="Bill line numbers involved")
    amount_cents: int | None = Field(default=None, description="Dollar amount in cents, as defined per check")
    explanation: str = ""


class SystemOutput(BaseModel):
    """A system's full answer for one case."""

    model_config = ConfigDict(extra="forbid")
    flags: list[PredictedFlag]
