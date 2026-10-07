"""The whole LangGraph workflow, end to end, on a fake reference database."""

import operator
from typing import Annotated

from langgraph.graph import START, StateGraph
from typing_extensions import TypedDict

from bill_auditor.agent.graph import STEP_BUDGET, STEPS_PER_RUN, build_graph, run_audit
from bill_auditor.agent.tracing import Tracer
from bill_auditor.baselines.rules_only import run_rules_only
from bill_auditor.evaluation.hard_cases import build_hard_cases
from bill_auditor.evaluation.metrics import summarise

CASES = build_hard_cases()


NEEDS_MODEL = {"hard-m09", "hard-m11"}  # pairing needs the text of descriptions and remarks


def test_rules_only_system_passes_every_hard_case_it_can_decide(audit_db_path):
    decidable = [c for c in CASES if c.case_id not in NEEDS_MODEL]
    run = run_rules_only(decidable, audit_db_path)
    summary = summarise(decidable, run.outputs)
    d = summary["detection"]["all_checks"]
    assert d["recall"]["rate"] == d["precision"]["rate"] == d["amount_accuracy"]["rate"] == 1.0
    assert summary["trap_pass_rate"]["rate"] == 1.0
    assert summary["false_alarm_rate_clean"]["rate"] == 0.0
    assert summary["cannot_check_rate"]["rate"] == 1.0
    assert run.within_budget and max(run.steps.values()) == STEPS_PER_RUN <= STEP_BUDGET


def test_rules_only_abstains_when_only_text_could_pair_the_lines(audit_db_path):
    cases = [c for c in CASES if c.case_id in NEEDS_MODEL]
    run = run_rules_only(cases, audit_db_path)
    for case in cases:
        flags = run.outputs[case.case_id]
        assert not [f for f in flags if f.kind == "error"]
        assert sorted(tuple(f.bill_lines) for f in flags if f.check == "provider_liability") == [(1,), (2,)]


def test_injection_text_changes_nothing(audit_db_path):
    """A note telling the auditor to report no errors is just data to deterministic checks."""
    graph = build_graph(audit_db_path)
    case = next(c for c in CASES if c.case_id == "hard-i01")
    report = run_audit(graph, case.bill, case.eob).report
    assert [(f.check, f.bill_lines) for f in report.errors] == [("duplicate", (1, 2))]


def test_out_of_scope_bill_skips_the_checks(audit_db_path):
    tracer = Tracer(None)
    case = next(c for c in CASES if c.case_id == "hard-c01")
    report = run_audit(build_graph(audit_db_path), case.bill, tracer=tracer).report
    assert [e["node"] for e in tracer.events] == ["intake", "report"]
    assert report.status == "completed" and report.steps_used == 2 and not report.errors


def test_malformed_input_produces_a_rejected_report(audit_db_path):
    raw = CASES[0].bill.model_dump(mode="json")
    del raw["lines"]
    report = run_audit(build_graph(audit_db_path), raw).report
    assert report.status == "rejected" and "Bill is not valid" in report.problems[0]


def test_six_checks_run_in_parallel_then_verify_once(audit_db_path, tmp_path):
    trace = tmp_path / "trace.jsonl"
    tracer = Tracer(trace)
    case = next(c for c in CASES if c.case_id == "hard-m08")
    run_audit(build_graph(audit_db_path), case.bill, case.eob, tracer=tracer)
    nodes = [e["node"] for e in tracer.events]
    assert nodes[:2] == ["intake", "match"] and nodes[-2:] == ["verify", "report"]
    assert sorted(nodes[2:8]) == sorted(["check_liability", "check_duplicates", "check_arithmetic",
                                         "check_quantity", "check_unbundling", "check_price"])
    written = trace.read_text(encoding="utf-8")
    assert len(written.splitlines()) == len(nodes)
    for secret in (case.bill.patient.name, case.bill.patient.address, case.bill.patient.member_id):
        assert secret not in written  # traces never contain personal details


class LoopState(TypedDict):
    steps_used: Annotated[int, operator.add]


def test_a_runaway_graph_is_stopped_with_a_report_not_a_crash():
    builder = StateGraph(LoopState)
    builder.add_node("loop", lambda state: {"steps_used": 1})
    builder.add_edge(START, "loop")
    builder.add_edge("loop", "loop")
    run = run_audit(builder.compile(), CASES[0].bill)
    assert run.report.status == "stopped" and "limit" in run.report.problems[0]
