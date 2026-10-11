"""The ReAct agent loop with a scripted fake model (no network, no quota)."""

import json

import pytest

from bill_auditor.baselines.model_alone import SYSTEM_PROMPT, build_user_message
from bill_auditor.baselines.react_agent import (
    MAX_TURNS,
    REACT_SYSTEM,
    AgentAnswer,
    ReActStep,
    ToolCall,
    Toolbox,
    build_react_graph,
    calculate,
    run_agent,
)
from bill_auditor.evaluation.hard_cases import build_hard_cases
from bill_auditor.llm.gemini import StructuredResult

CASES = {c.case_id: c for c in build_hard_cases()}


class ScriptedLLM:
    model = "fake"

    def __init__(self, steps):
        self.steps = list(steps)
        self.prompts = []

    def structured(self, system, user, schema):
        assert schema is ReActStep and system == REACT_SYSTEM
        self.prompts.append(user)
        step = self.steps.pop(0) if self.steps else ReActStep(thought="keep looking", tool_calls=[
            ToolCall(tool="calculate", arguments_json='{"expression": "1 + 1"}')])
        return StructuredResult(step, "{}", {}) if step != "invalid" else StructuredResult(None, "nope", {})


def call(tool, **args):
    return ToolCall(tool=tool, arguments_json=json.dumps(args))


def final(*flags):
    return ReActStep(thought="done", final=AgentAnswer(flags=list(flags)))


def test_prompt_is_the_baseline_prompt_plus_tools():
    assert REACT_SYSTEM.startswith(SYSTEM_PROMPT) and "calculate" in REACT_SYSTEM


@pytest.mark.parametrize(("expression", "value"), [("3 * 4500 + 1200", 14700), ("(250 - 128) - 32", 90), ("-5 / 2", -2.5)])
def test_calculator(expression, value):
    assert calculate(expression) == value


@pytest.mark.parametrize("expression", ["__import__('os').system('dir')", "open('x')", "2 ** 1000000", "a + 1"])
def test_calculator_refuses_anything_but_arithmetic(expression):
    with pytest.raises(ValueError):
        calculate(expression)


def test_agent_uses_tools_then_answers(audit_db_path):
    case = CASES["hard-t02"]  # Z1040 + Z1041, indicator 0
    llm = ScriptedLLM([
        ReActStep(thought="check the pair and the limit", tool_calls=[
            call("check_code_pair", code_a="Z1040", code_b="Z1041", date_of_service="2026-11-10",
                 modifiers_a=[], modifiers_b=["59"]),
            call("get_unit_limit", code="Z1040", date_of_service="2026-11-10")]),
        final({"check": "unbundling", "kind": "error", "target": "line", "bill_lines": [1, 2], "amount_cents": 50000}),
    ])
    graph = build_react_graph(llm, Toolbox(audit_db_path, None, None))
    out = run_agent(graph, build_user_message(case))
    assert out["final"].flags[0].bill_lines == [1, 2] and out["turns"] == 2 and not out["exhausted"]
    assert [tool for tool, _ in out["tool_log"]] == ["check_code_pair", "get_unit_limit"]
    second_prompt = llm.prompts[1]
    assert '"modifier_indicator": "0"' in second_prompt and '"mue_value": 1' in second_prompt  # tool results were shown


def test_tool_errors_come_back_as_text(audit_db_path):
    llm = ScriptedLLM([
        ReActStep(thought="try", tool_calls=[call("get_unit_limit", code="Z1040"),  # missing date
                                              ToolCall(tool="calculate", arguments_json="not json")]),
        final(),
    ])
    out = run_agent(build_react_graph(llm, Toolbox(audit_db_path, None, None)), "BILL")
    assert out["final"] is not None
    assert "error: KeyError" in llm.prompts[1] and "not a JSON object" in llm.prompts[1]


def test_running_out_of_turns_makes_the_case_invalid(audit_db_path):
    llm = ScriptedLLM([])  # never answers
    out = run_agent(build_react_graph(llm, Toolbox(audit_db_path, None, None)), "BILL")
    assert out["final"] is None and out["exhausted"] and out["turns"] == MAX_TURNS
    assert len(llm.prompts) == MAX_TURNS


def test_invalid_step_is_noted_and_counts_as_a_turn(audit_db_path):
    llm = ScriptedLLM(["invalid", final()])
    out = run_agent(build_react_graph(llm, Toolbox(audit_db_path, None, None)), "BILL")
    assert out["turns"] == 2 and "did not match the schema" in llm.prompts[1]


def test_bill_is_redacted_before_the_agent_sees_it(audit_db_path):
    case = CASES["hard-m01"]
    llm = ScriptedLLM([final()])
    run_agent(build_react_graph(llm, Toolbox(audit_db_path, None, None)), build_user_message(case))
    assert case.bill.patient.name not in llm.prompts[0] and "[PATIENT_NAME]" in llm.prompts[0]
