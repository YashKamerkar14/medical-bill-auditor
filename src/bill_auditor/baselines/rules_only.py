"""System B: rules only. The Milestone 2 graph, no model anywhere.

Runs every case through the audit graph, collects its findings in the shape
the scorer expects, and records the steps each run used so the step budget
can be checked over the whole set.
"""

from dataclasses import dataclass, field
from pathlib import Path

from bill_auditor.agent.graph import STEP_BUDGET, build_graph, run_audit
from bill_auditor.agent.tracing import Tracer
from bill_auditor.checks.models import to_predicted
from bill_auditor.evaluation.schemas import Case, PredictedFlag


@dataclass
class RulesOnlyRun:
    outputs: dict[str, list[PredictedFlag]] = field(default_factory=dict)
    steps: dict[str, int] = field(default_factory=dict)
    statuses: dict[str, str] = field(default_factory=dict)
    dropped: dict[str, int] = field(default_factory=dict)

    @property
    def within_budget(self) -> bool:
        return all(s <= STEP_BUDGET for s in self.steps.values()) and "stopped" not in self.statuses.values()


def run_rules_only(cases: list[Case], db_path: Path, trace_dir: Path | None = None) -> RulesOnlyRun:
    graph = build_graph(db_path)
    result = RulesOnlyRun()
    for case in cases:
        tracer = Tracer(trace_dir / f"{case.case_id}.jsonl" if trace_dir else None)
        run = run_audit(graph, case.bill, case.eob, tracer=tracer)
        report = run.report
        result.outputs[case.case_id] = to_predicted(report.errors + report.benchmarks, report.cannot_check)
        result.steps[case.case_id] = report.steps_used
        result.statuses[case.case_id] = report.status
        result.dropped[case.case_id] = len(report.dropped)
    return result
