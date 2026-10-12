"""The audit graph.

Rules only (Milestone 2, `llm=None`):

    intake --(rejected or out of scope)--> report
       |
     match --> [six checks in parallel] --> verify --> report

Full system (Milestone 3, with a model client): the model is used only where
language helps, never to decide what is an error:

    match --(ambiguous lines)--> ai_match --> [six checks] --> verify
    verify --(flags the manual covers)--> Send: explain x N (corrective-RAG subgraph, in parallel)
           --> verify_quotes --> report

The six checks run in one superstep (edges from match / ai_match); verify has
an edge from each, so it runs once after all six (fan-in). `Send` starts one
explain task per flag with its own input; their explanations merge by flag id
through the `merge_dicts` reducer, and verify_quotes runs once after all of them.

Budgets (CLAUDE.md, Agent loop): STEP_BUDGET node executions per run and an
explicit recursion limit; if it is ever hit, the run ends with a "stopped"
report instead of an exception. At most MAX_EXPLAINED_FLAGS flags per bill are
sent to the explain subgraph, so the budget holds for any bill.
"""

from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from bill_auditor.agent.ai_match import model_match
from bill_auditor.agent.explain import EXPLAINED_CHECKS, SUBGRAPH_RECURSION_LIMIT, Explanation, build_explain_graph
from bill_auditor.agent.state import AuditState
from bill_auditor.agent.tracing import Tracer
from bill_auditor.checks import arithmetic, duplicates, liability, price, quantity, unbundling
from bill_auditor.checks.context import CheckContext
from bill_auditor.checks.matching import LineMatch, match_lines
from bill_auditor.db import connect
from bill_auditor.guardrails.pii import UnsafePayload
from bill_auditor.guardrails.quotes import verify_quote
from bill_auditor.guardrails.verify import verify
from bill_auditor.llm.gemini import ModelCallFailed, RateLimited
from bill_auditor.llm.guarded import BudgetExhausted
from bill_auditor.lookups import DirectLookups, Lookups
from bill_auditor.parsing.intake import intake, unknown_lines
from bill_auditor.report.report import Report, build_report

RULES_ONLY_MODEL = "none (rules only)"
CHECK_NODES = {
    "check_liability": liability.run,
    "check_duplicates": duplicates.run,
    "check_arithmetic": arithmetic.run,
    "check_quantity": quantity.run,
    "check_unbundling": unbundling.run,
    "check_price": price.run,
}
MAX_EXPLAINED_FLAGS = 8
STEPS_PER_RUN = 3 + len(CHECK_NODES) + 1   # rules only: intake, match, verify, report + the checks
STEP_BUDGET = 22                          # full system worst case: 10 + ai_match + 8 explain + verify_quotes = 20
RECURSION_LIMIT = 12                      # supersteps; the full graph needs at most 8
MODEL_ERRORS = (BudgetExhausted, RateLimited, ModelCallFailed)


def _context(conn, lookups, state: AuditState) -> CheckContext:
    result = state["intake"]
    return CheckContext(conn=conn, lookups=lookups, bill=result.bill, eob=result.eob, matches=state["matches"],
                        skip_lines=frozenset(unknown_lines(result)), table_checks=result.table_checks)


def build_graph(db_path: Path, llm=None, chroma=None, embedder=None, lookups: Lookups | None = None):
    """Rules only when `llm` is None; otherwise the full system.

    Evidence lookups go through `lookups`: by default direct (`chroma` and `embedder` are needed for the manual
    search in the full system), or `McpLookups` to make them over MCP. Intake and verify always read the database
    at `db_path` directly.
    """
    lookups = lookups or DirectLookups(db_path, chroma, embedder)
    explain_graph = build_explain_graph(db_path, lookups, llm) if llm is not None else None
    model_label = RULES_ONLY_MODEL if llm is None else f"{llm.model} (matching of ambiguous lines and explanations only)"

    def open_db():
        return closing(connect(db_path))

    def intake_node(state: AuditState) -> dict:
        with open_db() as conn:
            result = intake(conn, state["raw_bill"], state.get("raw_eob"), state.get("allow_non_synthetic", False))
        return {"intake": result, "cannot_check": result.cannot_check, "steps_used": 1}

    def route_after_intake(state: AuditState) -> str:
        result = state["intake"]
        return "match" if result.accepted and result.in_scope else "report"

    def match_node(state: AuditState) -> dict:
        result = state["intake"]
        if result.eob is not None:
            matches = match_lines(result.bill, result.eob)
        else:
            matches = [LineMatch(bill_line=l.line_no, eob_line=None, method="unmatched") for l in result.bill.lines]
        return {"matches": matches, "steps_used": 1}

    def route_after_match(state: AuditState):
        if llm is not None and any(m.method == "ambiguous" for m in state["matches"]):
            return "ai_match"
        return list(CHECK_NODES)  # fan-out straight to the six checks

    def ai_match_node(state: AuditState) -> dict:
        result = state["intake"]
        try:
            matches = model_match(result.bill, result.eob, state["matches"], llm)
            return {"matches": matches, "steps_used": 1}
        except MODEL_ERRORS as error:  # lines stay ambiguous: their balances are "cannot check"
            return {"model_failures": [f"ai_match: {error}"], "steps_used": 1}

    def check_node(check):
        def node(state: AuditState) -> dict:
            with open_db() as conn:
                found = check(_context(conn, lookups, state))
            return {"candidate_flags": found.flags, "cannot_check": found.cannot, "steps_used": 1}
        return node

    def verify_node(state: AuditState) -> dict:
        with open_db() as conn:
            kept, dropped = verify(_context(conn, lookups, state), state.get("candidate_flags", []))
        return {"verified_flags": kept, "dropped_flags": dropped, "steps_used": 1}

    def route_to_explain(state: AuditState):
        if llm is None:
            return "report"
        explainable = [f for f in state.get("verified_flags", []) if f.check in EXPLAINED_CHECKS][:MAX_EXPLAINED_FLAGS]
        if not explainable:
            return "report"
        return [Send("explain", {"flag": flag}) for flag in explainable]  # one parallel task per flag

    def explain_node(payload: dict) -> dict:
        flag = payload["flag"]  # a Send task receives its own input, not the whole graph state
        try:
            out = explain_graph.invoke({"flag": flag}, config={"recursion_limit": SUBGRAPH_RECURSION_LIMIT})
            return {"explanations": {flag.flag_id: out["explanation"]}, "steps_used": 1}
        except MODEL_ERRORS as error:
            explanation = Explanation(flag_id=flag.flag_id, status="not_explained", note=f"model unavailable: {error}")
            return {"explanations": {flag.flag_id: explanation}, "model_failures": [f"explain: {error}"], "steps_used": 1}
        except UnsafePayload as error:  # personal data would have been sent: nothing was, fail closed
            explanation = Explanation(flag_id=flag.flag_id, status="not_explained", note=f"not sent: {error}")
            return {"explanations": {flag.flag_id: explanation}, "steps_used": 1}
        except GraphRecursionError:
            explanation = Explanation(flag_id=flag.flag_id, status="not_explained", note="explain step limit reached")
            return {"explanations": {flag.flag_id: explanation}, "steps_used": 1}

    def verify_quotes_node(state: AuditState) -> dict:
        """Defence in depth: re-check every quote against the database before anything is reported."""
        checked = {}
        with open_db() as conn:
            for flag_id, explanation in state.get("explanations", {}).items():
                if explanation.status == "explained" and any(verify_quote(conn, q.passage_id, q.text) for q in explanation.quotes):
                    explanation = explanation.model_copy(update={
                        "status": "unverified", "text": "", "quotes": [], "quotes_verified": 0,
                        "note": "Explanation dropped: a quote failed the final check"})
                checked[flag_id] = explanation
        return {"explanations": checked, "steps_used": 1}

    def report_node(state: AuditState) -> dict:
        result = state["intake"]
        status = "completed" if result.accepted else "rejected"
        report = build_report(
            bill_id=result.bill.bill_id if result.bill else None, status=status, model=model_label,
            releases=result.releases, flags=state.get("verified_flags", []), cannot=state.get("cannot_check", []),
            dropped=state.get("dropped_flags", []), problems=result.errors, steps_used=state.get("steps_used", 0) + 1,
            explanations=state.get("explanations", {}), input_warnings=result.input_warnings,
        )
        return {"report": report, "steps_used": 1}

    builder = StateGraph(AuditState)
    builder.add_node("intake", intake_node)
    builder.add_node("match", match_node)
    for name, check in CHECK_NODES.items():
        builder.add_node(name, check_node(check))
    builder.add_node("verify", verify_node)
    builder.add_node("report", report_node)

    builder.add_edge(START, "intake")
    builder.add_conditional_edges("intake", route_after_intake, {"match": "match", "report": "report"})
    if llm is not None:
        builder.add_node("ai_match", ai_match_node)
        builder.add_node("explain", explain_node)
        builder.add_node("verify_quotes", verify_quotes_node)
        builder.add_conditional_edges("match", route_after_match, ["ai_match", *CHECK_NODES])
        for name in CHECK_NODES:
            builder.add_edge("ai_match", name)
        builder.add_conditional_edges("verify", route_to_explain, ["explain", "report"])
        builder.add_edge("explain", "verify_quotes")
        builder.add_edge("verify_quotes", "report")
    else:
        for name in CHECK_NODES:
            builder.add_edge("match", name)    # fan-out: all six run in the same superstep
        builder.add_edge("verify", "report")
    for name in CHECK_NODES:
        builder.add_edge(name, "verify")       # fan-in: verify waits for all six
    builder.add_edge("report", END)
    return builder.compile()


@dataclass
class AuditRun:
    report: Report
    state: dict


def run_audit(graph, raw_bill, raw_eob=None, allow_non_synthetic: bool = False, tracer: Tracer | None = None) -> AuditRun:
    """Run one bill through the graph with an explicit recursion limit; never raises on a runaway graph."""
    inputs = {"raw_bill": raw_bill, "raw_eob": raw_eob, "allow_non_synthetic": allow_non_synthetic,
              "candidate_flags": [], "cannot_check": [], "steps_used": 0, "explanations": {}, "model_failures": []}
    tracer = tracer or Tracer(None)
    try:
        state = tracer.run(graph, inputs, {"recursion_limit": RECURSION_LIMIT})
    except GraphRecursionError:
        report = Report(bill_id=None, status="stopped", model=RULES_ONLY_MODEL,
                        problems=[f"Stopped: the run exceeded its limit of {RECURSION_LIMIT} graph steps."])
        tracer.event("stopped", {"reason": "recursion limit"})
        return AuditRun(report=report, state={})
    return AuditRun(report=state["report"], state=state)
