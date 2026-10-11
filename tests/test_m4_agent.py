"""Milestone 4 runner: resumable, stops cleanly on a model failure, counts required lookups."""

import json

from bill_auditor.baselines.react_agent import AgentAnswer, ReActStep, ToolCall
from bill_auditor.evaluation.hard_cases import build_hard_cases
from bill_auditor.evaluation.m4_agent import PROMPT_VERSION, fully_correct, load_react, lookups_made, required_lookups, run_react
from bill_auditor.llm.gemini import RateLimited, StructuredResult

CASES = {c.case_id: c for c in build_hard_cases()}


class FakeLLM:
    model = "fake"

    def __init__(self, fail_on_call=None):
        self.calls = self.cache_hits = 0
        self.tokens = {"input_tokens": 0, "output_tokens": 0}
        self.fail_on_call = fail_on_call

    def structured(self, system, user, schema):
        self.calls += 1
        if self.calls == self.fail_on_call:
            raise RateLimited("quota")
        self.tokens["input_tokens"] += 100
        if "--- Turn 1" not in user:
            return StructuredResult(ReActStep(thought="look", tool_calls=[
                ToolCall(tool="get_unit_limit", arguments_json='{"code": "Z1040", "date_of_service": "2026-11-10"}')]), "{}", {})
        return StructuredResult(ReActStep(thought="done", final=AgentAnswer(flags=[])), "{}", {})


def test_required_lookups_for_a_pair_case():
    needed = required_lookups(CASES["hard-t02"])  # Z1040 + Z1041 on one date
    assert needed == {("unit", "Z1040"), ("unit", "Z1041"), ("pair", frozenset({"Z1040", "Z1041"}))}
    made = lookups_made([("check_code_pair", {"code_a": "z1041", "code_b": "Z1040"}), ("get_unit_limit", {"code": "Z1040"})])
    assert needed - made == {("unit", "Z1041")}


def test_run_saves_resumes_and_stops_on_quota(tmp_path, audit_db_path):
    cases = [CASES["hard-t02"], CASES["hard-m01"]]
    report = run_react(cases, tmp_path, FakeLLM(fail_on_call=3), audit_db_path, None, None)
    assert report["done"] == 1 and report["stopped"].startswith("hard-m01")
    assert set(load_react(tmp_path)) == {"hard-t02"}  # nothing partial saved for the failed case
    record = json.loads((tmp_path / "hard-t02.json").read_text())
    assert record["prompt_version"] == PROMPT_VERSION and record["turns"] == 2 and record["required_lookups_made"] == 1

    report = run_react(cases, tmp_path, FakeLLM(), audit_db_path, None, None)
    assert report == {"done": 1, "skipped": 1, "stopped": None}


def test_invalid_answer_is_never_fully_correct():
    assert not fully_correct(CASES["hard-t02"], None)


def test_compare_scores_every_system_on_every_case():
    from bill_auditor.evaluation.m4_agent import compare
    cases = [CASES["hard-t02"], CASES["hard-m01"]]
    result = compare(cases, {name: {} for name in "ABCD"})  # nobody answered: everything invalid
    assert result["fully_correct_rate"] == {name: 0.0 for name in "ABCD"}
    assert set(result["holm_adjusted"]) == {"C_vs_A_fully_correct", "C_vs_D_fully_correct", "C_vs_A_precision"}
