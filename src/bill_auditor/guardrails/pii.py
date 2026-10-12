"""Personal data must never reach a model (CLAUDE.md, Input and Operations).

Two layers:
- Redaction (bill_auditor.parsing.redaction) replaces the bill's own personal
  values with placeholders, and scrubs the generic patterns below from free
  text, where a person could have typed them.
- The pre-send gate `assert_safe_to_send` runs on every outgoing prompt: the
  guarded model client checks the generic patterns on every call, and callers
  that hold the bill also check its own values. If anything survives, nothing
  is sent (fail closed).
"""

import re

from bill_auditor.parsing.redaction import contains_value

# Generic patterns: personal data that is not one of the bill's own fields.
GENERIC = {
    "[SSN]": re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)"),
    "[EMAIL]": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    "[PHONE]": re.compile(r"(?<![\d-])(?:\+?1[\s.-]?)?(?:\(\d{3}\)\s?|\d{3}[\s.-])\d{3}[\s.-]\d{4}(?![\d-])"),
    "[CARD_NUMBER]": re.compile(r"(?<!\d)(?:\d{4}[ -]){3}\d{1,4}(?!\d)"),
}


class UnsafePayload(RuntimeError):
    """A personal value would have been sent to a model; nothing was sent."""


def scrub_generic(text: str) -> str:
    for placeholder, pattern in GENERIC.items():
        text = pattern.sub(placeholder, text)
    return text


def assert_safe_to_send(text: str, mapping: dict[str, str] | None = None, what: str = "prompt") -> None:
    """Raise UnsafePayload if a generic pattern or one of the bill's own values (`mapping`) is in `text`."""
    for placeholder, pattern in GENERIC.items():
        if pattern.search(text):
            raise UnsafePayload(f"{what}: {placeholder} pattern found; nothing was sent")
    for placeholder, value in (mapping or {}).items():
        if contains_value(text, value, placeholder):  # whole words only: "Tate" inside "statement_date" is no leak
            raise UnsafePayload(f"{what}: {placeholder} survived redaction; nothing was sent")
