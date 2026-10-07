"""Google Gemini, FREE TIER ONLY (docs/decisions/0006-evaluation-design.md).

- The API key comes from GOOGLE_API_KEY in .env; billing must never be enabled
  on its project. Free-tier content may be used by Google, so only synthetic,
  redacted bills are ever sent.
- Automatic retries are OFF (the SDK default is 5 attempts): on the free tier a
  rate-limit error (HTTP 429) must stop the run, not burn the daily quota.
- Structured output: the response must match a Pydantic schema; the raw text is
  kept so an invalid answer can be inspected.
"""

from dataclasses import dataclass

from google import genai
from google.genai import errors, types
from pydantic import BaseModel, ValidationError

DEFAULT_MODEL = "gemini-3.5-flash-lite"  # free tier; owner's choice 2026-10-07 after Flash models kept returning 503 (ADR 0006)
TIMEOUT_MS = 180_000


class RateLimited(RuntimeError):
    """The free-tier quota (per minute or per day) is used up: stop and resume later."""


class ModelCallFailed(RuntimeError):
    """Any other API error."""


@dataclass
class StructuredResult:
    parsed: BaseModel | None  # None when the answer did not match the schema
    raw_text: str
    usage: dict
    error: str | None = None


class GeminiClient:
    def __init__(self, model: str = DEFAULT_MODEL):
        self.model = model
        self._client = genai.Client(  # reads GOOGLE_API_KEY from the environment
            http_options=types.HttpOptions(timeout=TIMEOUT_MS, retry_options=types.HttpRetryOptions(attempts=1))
        )

    def structured(self, system: str, user: str, schema: type[BaseModel]) -> StructuredResult:
        try:
            response = self._client.models.generate_content(
                model=self.model,
                contents=user,
                config=types.GenerateContentConfig(
                    system_instruction=system,
                    response_mime_type="application/json",
                    response_schema=schema,
                    # no tools are given; switch the SDK's automatic tool calling off explicitly
                    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                ),
            )
        except errors.APIError as error:
            if error.code == 429:
                raise RateLimited(error.message) from error
            raise ModelCallFailed(f"{error.code}: {error.message}") from error

        meta = response.usage_metadata
        usage = {
            "input_tokens": getattr(meta, "prompt_token_count", None),
            "output_tokens": getattr(meta, "candidates_token_count", None),
            "thinking_tokens": getattr(meta, "thoughts_token_count", None),
        }
        raw = response.text or ""
        try:
            parsed = schema.model_validate_json(raw)
        except ValidationError as error:
            return StructuredResult(None, raw, usage, error=str(error)[:500])
        return StructuredResult(parsed, raw, usage)
