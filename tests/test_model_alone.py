"""Baseline runner, with a fake model client (no network, no quota used)."""

import json

import pytest

from bill_auditor.baselines.model_alone import (
    BaselineAnswer,
    UnsafePayload,
    build_user_message,
    load_outputs,
    run,
)
from bill_auditor.evaluation.hard_cases import build_hard_cases
from bill_auditor.llm.gemini import ModelCallFailed, RateLimited, StructuredResult

CASES = build_hard_cases()


class FakeClient:
    """Answers from a script: "ok", "invalid" or "limit" per request."""

    model = "fake-model"

    def __init__(self, script):
        self.script = list(script)
        self.prompts = []

    def structured(self, system, user, schema):
        self.prompts.append(user)
        step = self.script.pop(0)
        if step == "limit":
            raise RateLimited("quota")
        if step == "busy":
            raise ModelCallFailed("503: high demand")
        if step == "invalid":
            return StructuredResult(None, "not json", {"input_tokens": 1}, error="bad")
        return StructuredResult(BaselineAnswer(flags=[]), '{"flags": []}', {"input_tokens": 1})


def test_payload_is_redacted_and_marked_as_data():
    case = next(c for c in CASES if c.eob is not None)
    message = build_user_message(case)
    assert case.bill.patient.name not in message
    assert case.bill.patient.address not in message
    assert "[PATIENT_NAME]" in message


def test_non_synthetic_bills_are_never_sent():
    case = CASES[0]
    real = case.model_copy(update={"bill": case.bill.model_copy(update={"synthetic": False})})
    with pytest.raises(UnsafePayload, match="only synthetic"):
        build_user_message(real)


def test_run_saves_answers_and_resumes(tmp_path):
    cases = CASES[:3]
    first = run(cases, tmp_path, FakeClient(["ok", "ok"]), max_requests=2, min_interval_s=0)
    assert first.sent == 2 and "budget" in first.stopped_reason
    second = run(cases, tmp_path, FakeClient(["ok"]), max_requests=5, min_interval_s=0)
    assert second.skipped_existing == 2 and second.sent == 1
    assert set(load_outputs(tmp_path)) == {c.case_id for c in cases}


def test_rate_limit_stops_the_run_without_saving_a_partial_answer(tmp_path):
    report = run(CASES[:3], tmp_path, FakeClient(["ok", "limit"]), max_requests=10, min_interval_s=0)
    assert "rate limit" in report.stopped_reason
    assert len(list(tmp_path.glob("*.json"))) == 1


def test_busy_server_is_retried_after_a_wait(tmp_path):
    waits = []
    report = run(CASES[:2], tmp_path, FakeClient(["busy", "ok", "busy", "ok"]), max_requests=10, min_interval_s=0,
                 busy_waits_s=(60, 120), sleep=waits.append)
    assert report.stopped_reason is None and report.busy_retries == 2 and report.sent == 2
    assert waits == [60, 120]
    assert len(list(tmp_path.glob("*.json"))) == 2


def test_busy_server_stops_the_run_cleanly_when_retries_run_out(tmp_path):
    report = run(CASES[:3], tmp_path, FakeClient(["ok", "busy", "busy"]), max_requests=10, min_interval_s=0,
                 busy_waits_s=(60,), sleep=lambda s: None)
    assert "503" in report.stopped_reason
    assert len(list(tmp_path.glob("*.json"))) == 1


def test_rate_limit_is_never_retried(tmp_path):
    waits = []
    report = run(CASES[:2], tmp_path, FakeClient(["limit", "ok"]), max_requests=10, min_interval_s=0,
                 busy_waits_s=(60,), sleep=waits.append)
    assert "rate limit" in report.stopped_reason and waits == [] and report.sent == 0


def test_invalid_answer_is_regenerated_once_then_recorded(tmp_path):
    report = run(CASES[:1], tmp_path, FakeClient(["invalid", "ok"]), max_requests=10, min_interval_s=0)
    assert report.sent == 2 and report.invalid == 0
    report = run(CASES[1:2], tmp_path, FakeClient(["invalid", "invalid"]), max_requests=10, min_interval_s=0)
    assert report.invalid == 1
    record = json.loads((tmp_path / f"{CASES[1].case_id}.json").read_text())
    assert record["valid"] is False and len(record["attempts"]) == 2
    assert load_outputs(tmp_path)[CASES[1].case_id] is None
