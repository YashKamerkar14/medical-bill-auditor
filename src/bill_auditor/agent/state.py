"""Graph state.

Fields written by several nodes in the same step need a REDUCER that says how
to merge them. The six checks run in parallel and each adds flags, "cannot
check" notes and a step, so those fields use `operator.add` (list
concatenation / integer sum). Every other field has one writer and is simply
replaced.
"""

import operator
from typing import Annotated, Any

from typing_extensions import TypedDict

from bill_auditor.agent.explain import Explanation
from bill_auditor.checks.matching import LineMatch
from bill_auditor.checks.models import CannotCheck, Flag
from bill_auditor.guardrails.verify import DroppedFlag
from bill_auditor.parsing.intake import IntakeResult
from bill_auditor.report.report import Report


def merge_dicts(left: dict, right: dict) -> dict:
    """Reducer for results keyed by flag id: parallel explain tasks each add their own key."""
    return {**left, **right}


class AuditState(TypedDict, total=False):
    # input
    raw_bill: Any
    raw_eob: Any
    allow_non_synthetic: bool
    # intake and matching (one writer each)
    intake: IntakeResult
    matches: list[LineMatch]
    # written by the six parallel checks: merged by the reducer
    candidate_flags: Annotated[list[Flag], operator.add]
    cannot_check: Annotated[list[CannotCheck], operator.add]
    # verify and report
    verified_flags: list[Flag]
    dropped_flags: list[DroppedFlag]
    report: Report
    # Milestone 3: one explanation per explained flag, written by parallel Send tasks
    explanations: Annotated[dict[str, Explanation], merge_dicts]
    model_failures: Annotated[list[str], operator.add]   # quota / busy errors: the run is incomplete
    # every node adds 1: the step count checked against the budget
    steps_used: Annotated[int, operator.add]
