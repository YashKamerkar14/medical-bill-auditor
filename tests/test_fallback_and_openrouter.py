"""Free-first fallback and the OpenRouter dollar cap (no network)."""

import pytest
from pydantic import BaseModel

from bill_auditor.llm.fallback import FallbackClient, retry_after_seconds
from bill_auditor.llm.gemini import RateLimited, StructuredResult
from bill_auditor.llm.guarded import BudgetExhausted
from bill_auditor.llm.openrouter import OpenRouterClient


class Answer(BaseModel):
    value: int


def fake_post(cost=None, prompt_tokens=1000, completion_tokens=100, content='{"value": 7}'):
    calls = []

    def post(body):
        calls.append(body)
        usage = {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens}
        if cost is not None:
            usage["cost"] = cost
        return {"choices": [{"message": {"content": content}}], "usage": usage}
    post.calls = calls
    return post


@pytest.mark.parametrize(("message", "seconds"), [
    ("Please retry in 1h34m26.103839105s.", 3600 + 34 * 60 + 26.103839105),
    ("Please retry in 23h52m56.3s", 23 * 3600 + 52 * 60 + 56.3),
    ("retry in 20.5s", 20.5),
    ("quota exceeded", None),
])
def test_retry_delay_is_parsed(message, seconds):
    assert retry_after_seconds(message) == (pytest.approx(seconds) if seconds else None)


def test_openrouter_records_the_larger_of_reported_and_computed_cost(tmp_path):
    client = OpenRouterClient(tmp_path / "spend.json", cap_usd=1.0, api_key="k", post=fake_post(cost=0.00001))
    result = client.structured("sys", "user", Answer)
    assert result.parsed == Answer(value=7)
    computed = 1000 * 0.30 / 1e6 + 100 * 2.50 / 1e6
    assert client.spent() == pytest.approx(computed)  # reported cost was lower, so the computed one is used
    assert client.model == "gemini-3.5-flash-lite"


def test_openrouter_refuses_a_call_that_could_cross_the_cap(tmp_path):
    post = fake_post()
    client = OpenRouterClient(tmp_path / "spend.json", cap_usd=0.005, api_key="k", post=post)
    with pytest.raises(BudgetExhausted, match="cap"):
        client.structured("sys", "user", Answer)  # worst case includes 4000 output tokens = $0.01
    assert post.calls == []  # nothing was sent


def test_cap_holds_across_client_instances(tmp_path):
    spend = tmp_path / "spend.json"
    for _ in range(3):
        OpenRouterClient(spend, cap_usd=1.0, api_key="k", post=fake_post()).structured("s", "u", Answer)
    assert OpenRouterClient(spend, cap_usd=1.0, api_key="k").spent() == pytest.approx(3 * (0.0003 + 0.00025))


def test_no_key_means_no_overflow(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(BudgetExhausted, match="no OPENROUTER_API_KEY"):
        OpenRouterClient(tmp_path / "s.json").structured("s", "u", Answer)


class FakeGemini:
    model = "gemini-3.5-flash-lite"

    def __init__(self, script):
        self.script = list(script)

    def structured(self, system, user, schema):
        step = self.script.pop(0)
        if step != "ok":
            raise RateLimited(step)
        return StructuredResult(Answer(value=1), "{}", {})


class FakeOverflow:
    model = "gemini-3.5-flash-lite"

    def __init__(self):
        self.used = 0

    def structured(self, system, user, schema):
        self.used += 1
        return StructuredResult(Answer(value=2), "{}", {})


def test_short_rate_limit_waits_on_the_free_tier():
    waits, overflow = [], FakeOverflow()
    client = FallbackClient(FakeGemini(["Please retry in 20s", "ok"]), overflow, sleep=waits.append)
    assert client.structured("s", "u", Answer).parsed.value == 1
    assert waits == [21] and overflow.used == 0 and client.calls == {"gemini_free": 1, "openrouter": 0}


def test_daily_quota_switches_to_overflow_for_the_rest_of_the_run():
    overflow = FakeOverflow()
    client = FallbackClient(FakeGemini(["Please retry in 23h52m56s", "ok"]), overflow, sleep=lambda s: None)
    assert client.structured("s", "u", Answer).parsed.value == 2
    assert client.structured("s", "u", Answer).parsed.value == 2  # stays on overflow, Gemini not retried
    assert client.calls == {"gemini_free": 0, "openrouter": 2} and client.model == "gemini-3.5-flash-lite"
