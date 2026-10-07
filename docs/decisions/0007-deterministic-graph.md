# 0007: The deterministic audit graph (rules only)

- Status: accepted
- Date: 2026-10-07
- Milestone: 2

## Context

PLAN.md makes every error decision deterministic code; the model is used only for language tasks (Milestone 3). The model-alone baseline (Milestone 1) noticed real-rule errors well (recall 0.93) but had precision 0.31-0.42, mostly from mis-added totals, and was prompt-injected once. Milestone 2 builds the rules-only system on LangGraph 1.2.14 (ADR 0001) and scores it on the same cases.

## Decision

**Graph.** `intake -> match -> six checks in parallel -> verify -> report`, with a conditional edge from intake straight to report when the bill is rejected or out of scope. The six checks are separate nodes with edges from `match`, so they run in one superstep; `verify` has an edge from each, so it runs once after all six (fan-in, confirmed on a toy graph against the docs). Fields written by several checks in the same step (`candidate_flags`, `cannot_check`, `steps_used`) have `operator.add` reducers; all other fields have one writer.

**Budgets.** A run uses 10 node executions (`STEPS_PER_RUN`), against a budget of 12 (`STEP_BUDGET`). The graph is invoked with an explicit `recursion_limit` of 8 supersteps (it needs 5); LangGraph's default since 1.0.6 is 1,000, far too loose for a guardrail. If the limit is hit, `run_audit` returns a "stopped" report instead of raising. Evaluation checks every run against the budget.

**Database access.** Each node opens its own short-lived SQLite connection: the parallel checks run in threads and a connection is not shared across threads. All access is read-only lookups.

**Intake.** Schema validation with clear messages; synthetic-only unless `allow_non_synthetic`; EOB must answer this bill; institutional bills are out of scope; dates with no loaded release give "cannot check" (whole bill if no date is covered, else per line); unknown codes (not in the fee schedule, the CMS charge data, or the synthetic code catalogue, now a versioned table loaded with the PTP release) give "cannot check" for their line; insured without an EOB gives "cannot check" for provider liability.

**Matching.** Most certain first: exact (code, date, modifiers in any order, units; one candidate each side); ordered (identical groups of equal size paired in claim order, which matches a denied repeat line); code + date when unique; leftovers unmatched or ambiguous. Ambiguous and unmatched lines give "cannot check" for provider liability until model-assisted matching (Milestone 3).

**Checks.** One pure function each, returning flags and "cannot check" notes. Every flag carries its rule id, rule-assigned confidence (`guardrails/confidence.py`), evidence (the cited table row with release label and synthetic marker, and/or the bill and EOB figures), a neutral message and what to ask. Code-pair confidence drops to medium when the insurer paid both lines. Price compares the stated unit price with Medicare (ADR 0006) and attaches the CMS average billed charge as context; it is always a benchmark.

**Verify.** Every flag must (1) be reproduced by re-running its check, (2) have every cited table row read back from the database under the stated release with matching values and synthetic marker, and (3) have its amount recomputed from the evidence by a separate formula. Failures are dropped and recorded. Manual-quote checks come in Milestone 3.

**Report.** Errors (high confidence first, then by amount), then price benchmarks, then "cannot check" (grouped in the rendering). Fixed notice, table releases, model ("none (rules only)"), synthetic labels, count of dropped flags. A report containing accusatory words ("fraud", "illegal", ...) raises instead of being produced.

**Tracing.** Local JSON-lines files, one event per node update (`stream_mode=["updates", "values"]`), with flag ids, rules, amounts, reasons, counts and timings, never the bill. A scan of 104 real traces found none of 547 personal values. Free and private; no tracing service.

## Consequences

- The rules-only system scores 1.00 on recall, precision, amount accuracy, dollar recall, traps and cannot-check on both splits, with no false alarms and every run within budget. This is expected on generated bills, which are built from the same written rules; the meaningful results are the comparison with the model alone and the hard cases (also all correct).
- Adding a model in Milestone 3 (ambiguous matching, explanations) cannot change which errors are reported, only how they are matched and explained.
