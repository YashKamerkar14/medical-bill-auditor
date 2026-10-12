"""Types every model client shares, with no SDK import (the rules-only demo installs no model SDK)."""

from dataclasses import dataclass

from pydantic import BaseModel


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
