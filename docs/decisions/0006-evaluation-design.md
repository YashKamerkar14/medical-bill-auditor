# 0006: Evaluation design and the free-tier model

- Status: accepted
- Date: 2026-10-07
- Milestone: 1

## Test sets

| Set | Size | Where | What it measures |
| --- | --- | --- | --- |
| Generated | 300 cases (210 dev / 90 held-out), seed 20261007 | `DATA_DIR/eval/generated/` (real codes and amounts, never committed) | The six checks at volume: 75 clean, 25 traps, 15 cannot-check, 185 with one planted error (20 with a second) |
| Hard | 44 cases (30 dev / 14 held-out) | `eval/hard_cases/cases.jsonl` (Z-codes only, committed) | Matching, traps and anti-traps, cannot-check, prompt injection, arithmetic edges, manual-principle scenarios |
| Retrieval | 50 queries (38 dev / 12 held-out), 3 unanswerable | `eval/retrieval_queries/queries.json` | Search quality (Milestone 4) and "insufficient evidence" (Milestone 3) |

Held-out cases are scored only at milestone ends.

### Generated-set scope

Provider-liability errors are planted on insured bills; all other errors on self-pay bills, so planted errors never interact with EOB adjustments. Interactions (a denied duplicate billed to the patient, splits across lines, EOB in a different order) are in the hard set. Clean bills use real, frequently billed physician codes (status A, no professional/technical split, at least $10 everywhere) priced at 1.3-2.5x Medicare, and at most one Z-code, so no code-pair edit can occur by accident.

The answer key is checked by `tests/test_generator.py`, which re-implements each rule independently and requires that everything wrong is in the key and everything in the key is wrong, for every case.

## Answer key and amounts

Each expected flag has a check, a kind (error / benchmark / cannot_check), a target (line, total_charges, balance_due, bill), bill lines and an amount in cents. Amounts are defined once per check:

| Check | amount_cents |
| --- | --- |
| provider_liability | patient balance on the line minus the EOB patient responsibility (sum of PR adjustments) |
| duplicate | charge of the repeated line |
| arithmetic | absolute difference between the stated and recomputed figure |
| quantity | units over the limit x unit price (for per-day edits, units are summed over the lines of that code and date) |
| unbundling | charge of the column-2 line |
| price_benchmark | charge minus Medicare amount x units; a benchmark, never an error |

The price check compares the stated unit price (not charge / units) with Medicare, threshold 3x (`checks/settings.py`), self-pay bills only.

Cannot-check entries in the key: bill-level (institutional bill, dates outside 2026-10-01 to 2026-12-31), unknown codes, and for insured bills a missing EOB or EOB line. "No published limit" and "no Medicare price" for individual lines are not scored: with synthetic PTP/MUE tables they apply to every real code (ADR 0005) and would swamp the metric.

## Scoring

- A predicted flag matches an expected one when check, kind and target agree and, for line flags, the bill lines overlap; matching is one-to-one, preferring exact line sets.
- Amount accuracy is scored separately on matched flags (exact equality).
- Metrics: error recall and precision (overall, real-rule checks, synthetic-rule checks, per check), amount accuracy, dollar recall, price-benchmark recall/precision, correct cannot-check rate, trap pass rate, false-alarm rate on clean cases (no expected flag and no trap), share of valid outputs.
- 95% Wilson intervals for rates; case-level bootstrap (1,000 resamples) for dollar recall. Flags within one case are not independent, so flag-level intervals are somewhat too narrow; case-level rates are not affected.
- Quantity and unbundling run on synthetic rules a model cannot know, so they are always reported separately (ADR 0005).

## Model-alone baseline

- Prompt: the same task definition the system follows (six checks, amount rules, cannot-check rules, test window), the instruction that document text is data, and the redacted bill and EOB as JSON. No tables, manual or tools. Prompt version = first 12 hex characters of its SHA-256, recorded with every answer.
- Output must match a Pydantic schema; an invalid answer is regenerated once, then recorded as invalid (counts as missing everything).
- Model: `gemini-3.5-flash-lite`, pinned by exact name (not a `-latest` alias), via `google-genai` 2.28.0 (owner's choice). History on 2026-10-07: `gemini-3.8-flash`, `gemini-3.7-flash` and then `gemini-3.6-flash` all returned 503 "high demand" on full bill requests for hours (one 3.6 answer got through), even though tiny test prompts sometimes worked; `gemini-2.5-flash` is closed to new users and `gemini-3.1-pro-preview` returned 429 on the free tier. A one-bill probe found the Lite models answering in about a second: `gemini-3.1-flash-lite` correctly, `gemini-3.5-flash-lite` with a weak answer (one sample). The owner chose `gemini-3.5-flash-lite`. It is a small model, so the baseline is a small-model baseline; results say so. The full system (Milestone 3) uses the same model. A Flash re-run can be added later as a free extra comparison if Flash becomes available.
- The prompt tells the model that Z-codes are valid synthetic codes (without their rules). The first test answer called every Z-code "not a valid code", which would measure unfamiliarity with our fake codes rather than auditing ability; our system knows the codes exist (`codes.csv`), so the baseline is told too.
- **Quantity and unbundling apply only to Z-codes, for every system.** The first real answer flagged two real codes (an eye exam and an office visit) as unbundled "under NCCI". That may well be true of the real NCCI tables, which this project does not use (ADR 0005), so the answer key could not count it as right; scoring it as wrong would penalise a possibly correct answer. Both the system and the baseline are therefore told that these two checks cover Z-codes only, and a quantity or unbundling flag on a real code is an error. Stated limitation: the real codes on generated bills may have real code-pair or unit-limit issues that no system here can check.
- Answers are stored with the prompt version; answers to an older prompt are re-asked and never scored. Provider-specific code is in `llm/gemini.py` only; the model is the `LLM_MODEL` setting.

## Free tier only

The owner uses no paid services. Consequences, all enforced in code:
- The key is a Google AI Studio key on the free tier; billing is never enabled. The project has no paid fallback.
- SDK retries are off (its default is 5 attempts). A 429 (quota used up) stops the run at once and is never retried. A 5xx (e.g. 503 "high demand", frequent on 2026-10-07) is retried after waiting 60, 120, 240 and 480 seconds, at most four times per run, then the run stops cleanly. Every answer is saved per case, so the next run resumes where the last stopped.
- At most `--max-requests` requests per run (default 25) and at least `--min-interval` seconds between them (default 13).
- Cases run in a fixed pseudo-random order (hash of the case id), so a partly finished run is still a fair sample; the score reports how many cases were answered.
- Free-tier content may be used by Google to improve its products, so only synthetic bills are sent, after redaction and a check that no personal value survived.

## Repository layout note

Evaluation code lives in `src/bill_auditor/evaluation/` (importable and testable) rather than `eval/` as sketched in PLAN.md; `eval/` holds committed data (hard cases, queries, results).
