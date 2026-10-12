# 0012: Dispute letter, PDF bills, app and command line

- Status: accepted
- Date: 2026-10-11
- Milestone: 7

## Decision 1: the letter is assembled by code, not written by the model

The plan allowed the model to write the letter. The guardrails for letters are strict: only verified, high-confidence flags; no law cited unless verified; neutral wording; a human-review step; personal details re-filled locally. A template filled by code meets all of them by construction:

- Every sentence is fixed and tested.
- Every item is one flag (traceability 100% by construction: `Letter.flag_ids`).
- The only sources cited are each flag's table release and NCCI manual quotes that already passed verbatim verification.
- No personal detail leaves the machine, because no model is involved.

What the model would add is warmer prose; what it would cost is a new place for hallucinated claims or citations, and a new evaluation (the model-as-judge on 15 hand-graded letters in the plan). Not worth it for a draft the person edits anyway. If a model-written version is added later, it must pass the same checks: wording, citation and traceability.

Rules enforced in `letter/letter.py`:

- `reviewed` must be true: the app gets it from a checkbox the person ticks, the command line from `--i-have-reviewed`;
- only `confidence == "high"` errors are eligible, so price benchmarks and model-matched liability findings (medium) are excluded;
- a "DRAFT" banner, the research-prototype notice, and `[SYNTHETIC]` marks with a warning for items on synthetic tables;
- no legal citation pattern (§, U.S.C., C.F.R., statute, regulation, law, Act) in the letter's own text. Verified manual quotes are inserted after that check.

## Decision 2: PDFs in our own synthetic layout, read with pdfplumber

Real bills are out of scope, so the PDF reader targets the project's own "synthetic itemized statement" and "synthetic EOB" layouts. They are rendered with `fpdf2` 2.8.8 (pinned, at least two weeks old) and read back with `pdfplumber`, which was already a dependency.

- Every section is a bordered table: header fields, line items, totals and notes. pdfplumber's table extraction is reliable on ruled tables, much more so than parsing free-flowing text.
- Tables split by a page break repeat their heading, and the parser merges them. A real bug, found by the round trip.
- The parser returns plain dicts that go through the same validation, injection scan and redaction as JSON. PDF text is data. pdfplumber also extracts text a person cannot see, so hidden instructions land in a field the detector checks.
- Standard PDF fonts cover Latin-1 only. All test bills are Latin-1; other text raises a clear error rather than rendering wrongly.

Measured: line-item accuracy 1,433/1,433 on all 348 test bills (target 0.98), 335/335 EOB lines, every bill and EOB identical after the round trip.

## Decision 3: Streamlit app, local only, plus a command line

- `streamlit` 1.64.0 (pinned, at least two weeks old). It runs on the person's machine; there is no hosted demo, by design.
- The app has no switch to accept a non-synthetic bill. Uploads are `.json` or `.pdf` only, and "rules only" mode needs no key.
- The letter tab is the human-review step. Nothing can be drafted until the person ticks items and confirms they checked them; the "Draft letter" button stays disabled until then.
- The command line (`python -m bill_auditor.audit`) shares its loading and running code with the app (`ui/core.py`).
- Both are tested headlessly with Streamlit's `AppTest` and by calling the command line's `main`. These are acceptance tests, since they need the real tables.

## Consequences

- Two new pinned dependencies (`streamlit`, `fpdf2`).
- Examples in `examples/` come from hand-written hard cases only (fake Z-codes, fake people), so they are safe to commit; `scripts/make_examples.py` regenerates them.
