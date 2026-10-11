"""OpenRouter as an overflow provider for the SAME model, under a hard dollar cap.

The owner's rule (2026-10-08): Gemini's free tier first; only when its daily
quota is exhausted, continue with the same model (google/gemini-3.5-flash-lite)
through OpenRouter, and never spend more than the cap (default $1.00, all
Milestone 4 runs together).

Spend control:
- before each call, a worst-case cost (prompt length / 3 tokens, plus the full
  max_tokens of output, at the model's list price) must fit under the cap, or
  the call is refused with BudgetExhausted;
- after each call, the larger of OpenRouter's reported cost and a token-based
  calculation is added to a running total saved on disk, so the cap holds
  across runs and days.
Only stdlib HTTP is used (no extra dependency). The key is read from the
environment and never logged.
"""

import json
import os
import threading
import urllib.error
import urllib.request
from pathlib import Path

from pydantic import BaseModel, ValidationError

from bill_auditor.llm.gemini import ModelCallFailed, RateLimited, StructuredResult
from bill_auditor.llm.guarded import BudgetExhausted

ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODEL = "google/gemini-3.5-flash-lite"
PRICE_PER_TOKEN = {"input": 0.30 / 1e6, "output": 2.50 / 1e6}  # list price on OpenRouter, 2026-10-08
MAX_OUTPUT_TOKENS = 4000
DEFAULT_CAP_USD = 1.00


class OpenRouterClient:
    def __init__(self, spend_file: Path, cap_usd: float = DEFAULT_CAP_USD, model: str = OPENROUTER_MODEL,
                 api_key: str | None = None, timeout_s: float = 180.0, post=None):
        self.openrouter_model = model
        self.spend_file, self.cap_usd, self.timeout_s = spend_file, cap_usd, timeout_s
        self._key = api_key or os.environ.get("OPENROUTER_API_KEY", "")
        self._post = post or self._http_post
        self._lock = threading.Lock()

    @property
    def model(self) -> str:
        return self.openrouter_model.split("/", 1)[-1]  # logical name, shared with the Gemini client and the cache

    def spent(self) -> float:
        return json.loads(self.spend_file.read_text())["usd"] if self.spend_file.exists() else 0.0

    def _record(self, usd: float, usage: dict) -> None:
        self.spend_file.parent.mkdir(parents=True, exist_ok=True)
        total = self.spent() + usd
        calls = (json.loads(self.spend_file.read_text())["calls"] if self.spend_file.exists() else 0) + 1
        self.spend_file.write_text(json.dumps({"usd": round(total, 6), "calls": calls, "cap_usd": self.cap_usd,
                                               "last_usage": usage}))

    def _http_post(self, body: dict) -> dict:
        request = urllib.request.Request(ENDPOINT, data=json.dumps(body).encode(), method="POST", headers={
            "Authorization": f"Bearer {self._key}", "Content-Type": "application/json",
            "X-OpenRouter-Title": "medical-bill-auditor (research prototype)"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            message = error.read().decode(errors="replace")[:300]
            if error.code == 429:
                raise RateLimited(f"openrouter: {message}") from error
            raise ModelCallFailed(f"{error.code}: openrouter: {message}") from error

    def structured(self, system: str, user: str, schema: type[BaseModel]) -> StructuredResult:
        if not self._key:
            raise BudgetExhausted("no OPENROUTER_API_KEY set: overflow provider unavailable")
        worst_case = (len(system) + len(user)) / 3 * PRICE_PER_TOKEN["input"] + MAX_OUTPUT_TOKENS * PRICE_PER_TOKEN["output"]
        with self._lock:
            if self.spent() + worst_case > self.cap_usd:
                raise BudgetExhausted(f"OpenRouter cap ${self.cap_usd:.2f} would be exceeded (spent ${self.spent():.4f})")
            body = {
                "model": self.openrouter_model,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "max_tokens": MAX_OUTPUT_TOKENS,
                "response_format": {"type": "json_schema",
                                    "json_schema": {"name": schema.__name__, "schema": schema.model_json_schema()}},
            }
            data = self._post(body)
            usage = data.get("usage") or {}
            tokens_in, tokens_out = usage.get("prompt_tokens") or 0, usage.get("completion_tokens") or 0
            computed = tokens_in * PRICE_PER_TOKEN["input"] + tokens_out * PRICE_PER_TOKEN["output"]
            self._record(max(float(usage.get("cost") or 0.0), computed), usage)

        raw = (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
        result_usage = {"input_tokens": tokens_in, "output_tokens": tokens_out, "thinking_tokens": None,
                        "provider": "openrouter"}
        try:
            return StructuredResult(schema.model_validate_json(raw), raw, result_usage)
        except ValidationError as error:
            return StructuredResult(None, raw, result_usage, error=str(error)[:500])
