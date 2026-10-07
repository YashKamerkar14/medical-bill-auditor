# 0008: The model where it helps: ambiguous matching and corrective-RAG explanations

- Status: accepted
- Date: 2026-10-07
- Milestone: 3

## Context

The rules-only graph (ADR 0007) is exact on every check but cannot read text. It abstains on bill lines that only descriptions or EOB remarks can pair, and its flags carry table evidence but no explanation from the NCCI manual. The model is used only for those two language tasks; it never decides whether something is an error.

## Decision

**Graph.** `build_graph(db_path)` is unchanged rules-only. With a model client it adds `ai_match` (only when matching left ambiguous lines) and, after `verify`, a conditional edge returning one `Send("explain", {"flag": f})` per flag the manual covers (code pairs, unit limits, duplicates; at most 8 per bill), then `verify_quotes` (fan-in) and `report`. Explanations merge by flag id through a `merge_dicts` reducer. Budgets: 22 node executions (worst case 20), recursion limit 12 supersteps; the explain subgraph has its own limit of 16. The `Send` + inner-subgraph + reducer pattern was prototyped on a toy graph before use.

**Matching fix found while designing.** The "ordered" round paired groups of identical-looking lines in claim order. Lines with the same code, date, modifiers and units but different descriptions or providers ("chest" vs "abdomen") could be crossed, giving a wrong flag. "Ordered" now requires truly identical lines (description, provider and charge too); anything else is ambiguous.

**Model-assisted matching.** The model sees only the ambiguous bill lines and their candidate EOB lines (redacted), and returns a pair or null ("never guess") for each. Code validates every proposal: same code and date (a candidate), EOB line not already used, billed amount equal to the line charge. Invalid proposals and nulls leave the line ambiguous, so its patient balance stays "cannot check".

**Explain subgraph (corrective RAG).** `make_query` (from the flag: rule, rationale, indicator) -> `retrieve` (hybrid) -> `grade` (model returns the ids of passages that state the rule; invented ids ignored) -> `write` if any, else `rewrite` the query (at most 2 per flag, the CLAUDE.md retry cap) -> after 2 rewrites, "insufficient evidence". `write` must return 2-4 neutral sentences and 1-2 quotes copied exactly from the provided passages, each with its passage id. `check_quotes` verifies each quote; on failure the explanation is regenerated once with the reason, then dropped ("unverified") while the flag is kept (owner's decision a; CLAUDE.md updated). Arithmetic, EOB-based and price flags keep their code-written explanations.

**Hybrid retrieval.** Reciprocal Rank Fusion (k = 60) of BM25 and vector rankings, depth 20, top 5. Relevance floor: top cosine similarity 0.55. Measured on development queries, unanswerable queries scored 0.633 and 0.667 and answerable ones as low as 0.596, so no similarity threshold separates them; the floor only removes clearly off-topic results and relevance is decided by the grader. Hybrid vs each mode alone is measured in Milestone 4.

**Quote verification.** Verbatim after normalising Unicode compatibility forms (ligatures), quote marks, dashes, hyphens, case and whitespace; an ellipsis splits a quote into pieces that must appear in order; quotes under 20 characters are rejected; the passage must be one of those provided to the writer. `verify_quotes` in the main graph re-checks every quote against the database before reporting (defence in depth), and the evaluator re-checks again independently.

**Free tier.** All model calls go through `GuardedLLM`: an on-disk cache of valid answers (re-runs are free), a per-run cap (default 300 new calls) and a per-day counter (default 450: the free tier allowed 500 requests per day for this model, measured 2026-10-07 when a run hit that limit), at least 5 s between calls shared across the parallel explain tasks (they run in threads, under a lock), patient retries on 5xx and none on 429. A quota or availability failure stops the evaluation before scoring; the next run resumes from the cache. Prompts contain no personal details: flags and matching prompts are built from redacted views.

## Consequences

- Detection can only change through matching: the model cannot add, remove or resize a flag.
- Explanation quality is measured (explained rate, raw quote accuracy, final evidence validity, section match) and reported next to detection.
- The model sees manual text and synthetic codes only; it is told Z-codes are synthetic and must not claim the manual names them.
