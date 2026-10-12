"""Text that looks like instructions to an AI (prompt injection), found before any model sees it.

All text on a bill or EOB is data (CLAUDE.md, Input). The prompts say so, but a
prompt is a request, not a guarantee. This detector adds a deterministic layer:
free text that matches an instruction-like pattern is reported to the person as
an input warning, and the full system never sends it to the model (lines whose
text is suspicious are left for code alone, which reports "cannot check" where
it cannot decide). False positives are therefore safe: they cost a "cannot
check", never a wrong flag.

Matching runs on NFKC-normalised, case-folded text with whitespace collapsed,
so full-width letters, ligatures and line breaks do not hide a phrase. It is a
heuristic, not a proof: obfuscated attacks that slip past it still meet the
data-only prompts and the code validation of every model proposal.
"""

import re
import unicodedata

PATTERNS = {
    "override": r"\b(ignore|disregard|forget|override|bypass)\b.{0,40}\b(instruction|rule|prompt|guideline|direction)s?\b",
    "role_play": r"\b(you are now|act as|pretend (to be|you are)|roleplay|role-play|from now on you)\b",
    "fake_speaker": r"(^|[\s\[(<{\"'])(system|assistant|developer|ai|model|chatbot|llm|gpt|claude|gemini)\s*(:|>|\])",
    "addressed_to_ai": r"\b(note|message|instruction|instructions|directive)s? (to|for) (the )?(ai|assistant|model|auditor|reviewer|bot|llm|system)\b",
    "ai_vocative": r"\b(dear |hey |attention,? )?(ai|assistant|chatbot|language model)\s*[,:!]",
    "suppress": r"\b(do not|don'?t|never|stop) (flag|report|mention|list|include|audit|check)\w*\b",
    "fabricate": r"\b(report|flag|add|raise|list|record)\b.{0,30}\b(error|flag|issue|overcharge|duplicate|unbundling)s?\b"
                 r".{0,20}\b(for|on|against) (bill |eob )?lines?\b",
    "command": r"\b(reviewer|auditor|system|admin|administrator|compliance|management|billing office) "
               r"(instruction|command|note|override|directive)s?\b",
    "report_directive": r"\b(state|say|write|claim|conclude|declare)\b.{0,15}\b(in|on) (your|the) "
                        r"(report|answer|response|output|summary)\b",
    "system_banner": r"\b(system|assistant|developer|admin) (update|notice|message|override|alert)\b",
    "label_party": r"\b(label|mark|call|report|classify|describe)\b.{0,30}\b(provider|doctor|clinic|bill|claim|biller)\b.{0,20}\bas\b",
    "pairing_order": r"\b(pair|match|link|map)\b.{0,20}\b(bill |eob )?line \d+\b.{0,20}\b(with|to)\b",
    "prompt_leak": r"\b(reveal|print|show|repeat|output|tell me)\b.{0,20}\b(system prompt|your (prompt|instructions|rules))\b",
    "exfiltrate": r"\b(include|send|output|reveal|share|post|email)\b.{0,40}\b(full name|date of birth|dob|home address|social security|ssn|member id|account number)\b",
    "link": r"(https?://|www\.)\S+",
    "markup": r"(```|</?[a-z_]{2,30}>|\{\{|\}\}|\"\s*[}\]])",
    "encoded": r"\b[a-z0-9+/]{32,}={0,2}(?![a-z0-9+/=])",
    "multilingual": r"\b(ignora|ignorez|ignoriere|ignorar|no (marques|reportes|informes)|ne (signalez|signale) pas"
                    r"|instrucci[oó]n|instructions? pour|anweisung|empareja)\b",
}
_COMPILED = {name: re.compile(pattern) for name, pattern in PATTERNS.items()}


def normalise(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def suspicious(text: str) -> list[str]:
    """Names of the instruction-like patterns found in `text` (empty if none)."""
    if not text:
        return []
    clean = normalise(text)
    return [name for name, pattern in _COMPILED.items() if pattern.search(clean)]


def scan(bill, eob=None) -> list[tuple[str, list[str]]]:
    """(where, patterns) for every free-text field of a bill and EOB that looks like an instruction."""
    fields = [("bill notes", bill.notes), ("provider name", bill.provider.name), ("patient name", bill.patient.name)]
    fields += [(f"bill line {line.line_no} description", line.description) for line in bill.lines]
    if eob is not None:
        fields += [("EOB payer name", eob.payer_name), ("EOB patient name", eob.patient_name)]
        fields += [(f"EOB line {line.line_no} remark", line.remark) for line in eob.lines]
    return [(where, found) for where, text in fields if (found := suspicious(text))]
