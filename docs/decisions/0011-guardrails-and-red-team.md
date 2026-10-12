# 0011: Guardrails and red team

- Status: accepted
- Date: 2026-10-11
- Milestone: 6

## Context

Milestone 6 closes the gaps between the guardrails in `CLAUDE.md` and the code, then attacks the system. Done when: the adversarial suite passes. An audit at the start found most guardrails already in place: schema validation, synthetic-only input, "cannot check" for unknown codes, dates and bill types, verify, quote checks, the report notice, the step budget and recursion limit, read-only pinned MCP tools, timeouts, PII-free traces, and per-run and daily spending caps. It also found these gaps:

- input had no size limits (lines, text length, units, amounts), no character rules for codes, and allowed invisible characters;
- prompt injection was handled only by "text is data" in the prompts;
- the PII check before sending ran only in the model-alone baseline, and redaction missed personal data typed in other formats;
- the wording rule covered accusation words but not opinions on medical necessity or legal advice;
- the matching, grading and query-rewrite steps did not regenerate a malformed answer.

## Decisions

1. **Input limits** (`parsing/schemas.py`). At most 50 lines; maximum lengths on every text field (description 300, remark 500, notes 2,000, names 100); codes are 5 letters or digits, modifiers 2; units at most 9,999; amounts at most $10 million; ids follow a strict pattern. Invisible control (Cc) and formatting (Cf) characters are rejected, because zero-width spaces or right-to-left overrides can hide text from a person reviewing the bill. The limits sit far above any real professional bill: all 348 test cases still validate.
2. **Injection detector** (`guardrails/injection.py`). It applies deterministic patterns to NFKC-normalised, case-folded text. A match is reported to the person as an input warning, and the full system never sends that text to the model: the line is left to code, which says "cannot check" where it cannot decide. A false positive costs a "cannot check", never a wrong flag. The detector is a heuristic, so the layers behind it still matter: data-only prompts, and code validation of every model proposal.
3. **Personal data, in layers** (`parsing/redaction.py`, `guardrails/pii.py`). Redaction now also covers the date of birth in other formats, the street line, the first name (matched only capitalised), and member and account ids with any spacing. Generic patterns (SSN, phone, email, card number) are scrubbed from free text. A pre-send gate runs on every model call inside the guarded client (generic patterns), and callers that hold the bill also check its own values. If anything is found, nothing is sent (fail closed).
4. **Neutral wording** (`guardrails/wording.py`). Three kinds of wording are refused: accusations (fraud, upcoding, kickback, deliberate ...), opinions on whether care was necessary, and legal advice (sue, attorney). A model-written explanation that breaks the rule is regenerated once, told why, then dropped. Its flag is kept, marked "no verified manual passage". The report drops such an explanation as a last line of defence. Deterministic text that breaks the rule is a bug and fails loudly.
5. **Regenerate once.** Matching, grading and query rewriting regenerate a malformed answer once, as the write step already did.
6. **Model-matched flags are never high confidence.** The red team showed a residual risk. When two candidate EOB lines bill the same amount, code cannot tell a correct pairing from one a lie talked the model into. A provider-liability flag that rests on a model-made pairing is therefore "medium" confidence, so it is never used for a letter, and it tells the person to confirm which EOB line answers the bill line.

## The adversarial suite

`evaluation/adversarial.py` holds 30 synthetic cases, written before the detector was run on them:

- 10 attacks on matching, where the model reads the text: lies, instructions, full-width letters, Spanish, base64, JSON breaking, fake delimiters and social engineering;
- 6 text attacks the model never sees;
- 2 PII-smuggling cases;
- 12 malformed or abusive inputs.

Pass rules: no wrong error flag; each expected error is found with its exact amount, or (fail-safe cases) its lines are "cannot check"; the report is produced with the notice and neutral wording; no personal value reaches any prompt; rejected input is rejected with a message naming the problem.

Two halves:

- `tests/test_adversarial.py` is deterministic, with a deliberately malicious fake model. It proposes wrong pairings, returns invalid answers, and writes accusations, care opinions and legal advice. It also tests the judge, so a lenient judge cannot fake a pass.
- `run_eval red-team` runs the same cases through the full system with the real model (free tier, cached), recording every prompt, plus the model-alone baseline for contrast.

## Measured, not assumed

- Detector, held out: 10 of the 16 text attacks were caught on the first run. Tuning on the suite then raised it to 14 of 16, which is not a held-out number. The two remaining misses are plain lies with no instruction words, which a pattern cannot catch.
- Detector false positives: none on the 342 non-injection test cases.
- All 348 model-alone prompts are byte-identical before and after the redaction changes, so stronger redaction cost nothing on ordinary bills.

## Consequences

- A suspected injection on an ambiguous line turns that line into "cannot check". That is safe, but it can hide a real error until a person looks.
- Model-matched provider-liability findings drop from high to medium confidence. Scores are unaffected (they do not use confidence); letters (Milestone 7) will not use these findings automatically.
