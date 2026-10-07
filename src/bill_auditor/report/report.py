"""Build and render the report (CLAUDE.md, Output guardrails).

- Order: errors (high confidence first, then by amount), then price
  benchmarks (labelled, never errors), then "cannot check".
- Fixed notice on every report; table releases and model recorded.
- Findings that rest on synthetic tables are labelled synthetic.
- Neutral wording: a report containing accusatory words is refused.
"""

import re
from typing import Literal

from pydantic import BaseModel

from bill_auditor.agent.explain import Explanation
from bill_auditor.checks.context import dollars
from bill_auditor.checks.models import CannotCheck, Flag
from bill_auditor.guardrails.verify import DroppedFlag
from bill_auditor.guardrails.wording import BANNED_WORDS

NOTICE = ("Research prototype. Not legal or financial advice. These are possible errors to verify: "
          "confirm every item with the billing office and your insurer before acting on it.")
_CONFIDENCE_ORDER = {"high": 0, "medium": 1, "low": 2}


class WordingViolation(ValueError):
    """A report would contain accusatory wording."""


class Report(BaseModel):
    bill_id: str | None
    status: Literal["completed", "rejected", "stopped"]
    notice: str = NOTICE
    model: str
    releases: dict[str, str] = {}
    errors: list[Flag] = []
    benchmarks: list[Flag] = []
    cannot_check: list[CannotCheck] = []
    dropped: list[DroppedFlag] = []
    problems: list[str] = []          # why a bill was rejected or a run stopped
    steps_used: int = 0
    explanations: dict[str, Explanation] = {}  # flag_id -> manual-grounded explanation (Milestone 3)

    @property
    def uses_synthetic_rules(self) -> bool:
        return any(e.synthetic for f in self.errors + self.benchmarks for e in f.evidence)


def build_report(bill_id, status, model, releases, flags, cannot, dropped, problems, steps_used,
                 explanations: dict[str, Explanation] | None = None) -> Report:
    errors = sorted((f for f in flags if f.kind == "error"),
                    key=lambda f: (_CONFIDENCE_ORDER[f.confidence], -f.amount_cents, f.bill_lines))
    benchmarks = sorted((f for f in flags if f.kind == "benchmark"), key=lambda f: -f.amount_cents)
    report = Report(bill_id=bill_id, status=status, model=model, releases=releases, errors=errors,
                    benchmarks=benchmarks, cannot_check=cannot, dropped=dropped, problems=problems,
                    steps_used=steps_used, explanations=explanations or {})
    text = " ".join([f.message + " " + f.ask for f in errors + benchmarks] + [c.reason for c in cannot]
                    + [e.text for e in report.explanations.values()])
    if BANNED_WORDS.search(text):
        raise WordingViolation(f"Report wording must stay neutral; found {BANNED_WORDS.search(text).group(0)!r}")
    return report


def _cannot_summary(items: list[CannotCheck]) -> list[str]:
    """Group per-line notes (e.g. 'no published limit') so the report stays readable."""
    grouped: dict[tuple, list[int]] = {}
    for c in items:
        key = (c.check, c.target, c.reason if c.target != "line" else re.sub(r"\b[A-Z0-9]{5}\b", "<code>", c.reason))
        grouped.setdefault(key, []).extend(c.bill_lines)
    lines = []
    for (check, target, reason), numbers in grouped.items():
        where = f"lines {', '.join(map(str, sorted(set(numbers))))}" if numbers else "whole bill"
        lines.append(f"- {check or 'all checks'} ({where}): {reason}")
    return lines


def render_markdown(report: Report) -> str:
    out = [f"# Bill audit: {report.bill_id or 'not accepted'}", "", f"> {report.notice}", ""]
    if report.status != "completed":
        out += [f"**Status: {report.status}.**", *[f"- {p}" for p in report.problems], ""]
    total = sum(f.amount_cents for f in report.errors)
    out += [f"## Possible errors to verify ({len(report.errors)}, {dollars(total)})", ""]
    if not report.errors:
        out.append("None found.")
    for i, f in enumerate(report.errors, 1):
        synthetic = " [SYNTHETIC rule table]" if any(e.synthetic for e in f.evidence) else ""
        out += [f"{i}. **{f.check}**, lines {', '.join(map(str, f.bill_lines)) or f.target}: "
                f"{dollars(f.amount_cents)} (confidence {f.confidence}){synthetic}",
                f"   - {f.message}", f"   - What to ask: {f.ask}"]
        explanation = report.explanations.get(f.flag_id)
        if explanation is not None and explanation.status == "explained":
            out.append(f"   - Why (NCCI Policy Manual, section {', '.join(explanation.sections)}): {explanation.text}")
            out += [f"     > “{q.text.strip()}” ({q.passage_id})" for q in explanation.quotes]
        elif explanation is not None:
            out.append("   - No verified manual passage for this finding.")
    out += ["", f"## Price benchmarks, not errors ({len(report.benchmarks)})", ""]
    if not report.benchmarks:
        out.append("None.")
    for f in report.benchmarks:
        out += [f"- line {f.bill_lines[0]}: {f.message}", f"  - {f.ask}"]
    out += ["", "## Could not check", ""] + (_cannot_summary(report.cannot_check) or ["Nothing."])
    out += ["", "## Sources", "", f"- Model: {report.model}"]
    out += [f"- {dataset}: {label}" for dataset, label in sorted(report.releases.items())]
    if report.dropped:
        out.append(f"- {len(report.dropped)} candidate finding(s) failed verification and were left out.")
    return "\n".join(out) + "\n"
