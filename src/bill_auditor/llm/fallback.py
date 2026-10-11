"""Gemini free tier first; the same model through OpenRouter only when the free quota is spent.

Gemini returns 429 both for a per-minute limit ("retry in 20s") and for the daily
quota ("retry in 23h52m"). A short wait is waited out on Gemini; a long one means
the free quota is gone for today, so this client switches to the OpenRouter
overflow (same model, dollar-capped) for the rest of the process. The logical
model name stays the same, so cached answers are shared between providers.
"""

import re
import time

from bill_auditor.llm.gemini import RateLimited

SHORT_WAIT_LIMIT_S = 90


def retry_after_seconds(message: str) -> float | None:
    """'Please retry in 1h34m26.1s' / 'retry in 20.5s' -> seconds, or None if not stated."""
    match = re.search(r"retry in\s+((?:\d+h)?(?:\d+m)?(?:[\d.]+s)?)", message)
    if not match or not match.group(1):
        return None
    parts = dict((unit, float(value)) for value, unit in re.findall(r"([\d.]+)([hms])", match.group(1)))
    return parts.get("h", 0) * 3600 + parts.get("m", 0) * 60 + parts.get("s", 0)


class FallbackClient:
    def __init__(self, primary, overflow, sleep=time.sleep):
        self.primary, self.overflow, self.sleep = primary, overflow, sleep
        self.using_overflow = False
        self.calls = {"gemini_free": 0, "openrouter": 0}

    @property
    def model(self) -> str:
        return self.primary.model

    def structured(self, system, user, schema):
        if not self.using_overflow:
            try:
                result = self._primary_call(system, user, schema)
                self.calls["gemini_free"] += 1
                return result
            except RateLimited as error:
                wait = retry_after_seconds(str(error))
                if wait is not None and wait <= SHORT_WAIT_LIMIT_S:
                    self.sleep(wait + 1)
                    return self.structured(system, user, schema)
                self.using_overflow = True  # free quota gone for today
        result = self.overflow.structured(system, user, schema)
        self.calls["openrouter"] += 1
        return result

    def _primary_call(self, system, user, schema):
        return self.primary.structured(system, user, schema)
