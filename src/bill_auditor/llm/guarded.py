"""Every model call in the system goes through GuardedLLM (free tier, ADR 0006 and 0008).

- Cache: a valid answer is stored under a hash of model + schema + prompt, so
  re-running an evaluation costs no quota. Invalid answers are not cached.
- Budgets: a cap on real calls per run and a per-day counter on disk
  (CLAUDE.md: spending cap per run and per day; on the free tier the "spend"
  is quota). Exceeding either raises BudgetExhausted before any call is made.
- Pacing: at least `min_interval_s` between calls, shared by the parallel
  explain tasks (they run in threads), so bursts stay under per-minute limits.
- Busy server (5xx): wait and retry a few times; quota error (429): never retried.
"""

import hashlib
import json
import threading
import time
from datetime import date
from pathlib import Path

from pydantic import BaseModel

from bill_auditor.guardrails.pii import assert_safe_to_send
from bill_auditor.llm.types import ModelCallFailed, StructuredResult

BUSY_WAITS_S = (30, 60, 120)


class BudgetExhausted(RuntimeError):
    """The per-run or per-day call budget is used up; nothing was sent."""


def _busy(error: ModelCallFailed) -> bool:
    code = str(error).split(":", 1)[0]
    return code.isdigit() and 500 <= int(code) < 600


class GuardedLLM:
    def __init__(self, client, cache_dir: Path, usage_dir: Path, max_calls: int, daily_cap: int,
                 min_interval_s: float = 5.0, busy_waits_s=BUSY_WAITS_S, sleep=time.sleep):
        self.client = client
        self.cache_dir, self.usage_dir = cache_dir, usage_dir
        self.max_calls, self.daily_cap = max_calls, daily_cap
        self.min_interval_s, self.busy_waits_s, self.sleep = min_interval_s, busy_waits_s, sleep
        self.calls = self.cache_hits = self.busy_retries = 0
        self.tokens = {"input_tokens": 0, "output_tokens": 0}
        self._lock = threading.Lock()
        self._last_call = 0.0

    @property
    def model(self) -> str:
        return self.client.model

    def _key(self, system: str, user: str, schema: type[BaseModel]) -> str:
        text = json.dumps([self.model, schema.__name__, system, user])
        return hashlib.sha256(text.encode()).hexdigest()

    def _usage_file(self) -> Path:
        return self.usage_dir / f"{date.today().isoformat()}.json"

    def used_today(self) -> int:
        path = self._usage_file()
        return json.loads(path.read_text())["calls"] if path.exists() else 0

    def _count_call(self) -> None:
        self.usage_dir.mkdir(parents=True, exist_ok=True)
        path = self._usage_file()
        path.write_text(json.dumps({"calls": self.used_today() + 1, "model": self.model}))

    def structured(self, system: str, user: str, schema: type[BaseModel]) -> StructuredResult:
        assert_safe_to_send(system + "\n" + user)  # generic personal-data patterns: nothing is sent if one is found
        key = self._key(system, user, schema)
        cached = self.cache_dir / f"{key}.json"
        if cached.exists():
            record = json.loads(cached.read_text(encoding="utf-8"))
            with self._lock:
                self.cache_hits += 1
            return StructuredResult(schema.model_validate(record["parsed"]), record["raw_text"], record["usage"])

        with self._lock:  # one call at a time: budgets and pacing are shared across threads
            if self.calls >= self.max_calls:
                raise BudgetExhausted(f"per-run cap of {self.max_calls} model calls reached")
            if self.used_today() >= self.daily_cap:
                raise BudgetExhausted(f"daily cap of {self.daily_cap} model calls reached")
            busy_used = 0
            while True:
                wait = self.min_interval_s - (time.monotonic() - self._last_call)
                if wait > 0:
                    self.sleep(wait)
                self._last_call = time.monotonic()
                try:
                    result = self.client.structured(system, user, schema)  # RateLimited propagates: never retried
                    break
                except ModelCallFailed as error:
                    if _busy(error) and busy_used < len(self.busy_waits_s):
                        self.sleep(self.busy_waits_s[busy_used])
                        busy_used += 1
                        self.busy_retries += 1
                        continue
                    raise
            self.calls += 1
            self._count_call()
            for name in self.tokens:
                self.tokens[name] += result.usage.get(name) or 0

        if result.parsed is not None:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            cached.write_text(json.dumps({"parsed": result.parsed.model_dump(mode="json"), "raw_text": result.raw_text,
                                          "usage": result.usage}), encoding="utf-8")
        return result
