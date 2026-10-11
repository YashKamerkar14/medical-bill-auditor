# 0009: Milestone 4 experiments (pre-registered)

- Status: accepted. Part B sections below were written BEFORE any ReAct agent call.
- Outcome (2026-10-11): all parts run as registered; no design change after the pilot. Results in `docs/results/m4-experiments.md`. 317 model calls, all on the Gemini free tier; OpenRouter spend $0.00.
- Date: 2026-10-11
- Milestone: 4
- Revised after an independent critique of the first plan (tool parity, sample split, statistics, randomness, retrieval-set difficulty, abstention, explain-query diagnosis).

## Part A: retrieval (done 2026-10-11, no model calls)

All 47 answerable labelled queries; distinct-section ranking (chunks collapsed to sections, depth 20). Committed setting: hybrid RRF k = 60, bge query prefix on; primary contrasts hybrid vs keyword and hybrid vs vector on MRR (paired permutation, Holm), hit@5 by exact McNemar as secondary; all other variants exploratory; random and titles-only baselines; breakdowns by query style and by query-title word overlap. Results: `docs/results/m4-experiments.md`.

The relevance floor (top cosine 0.55) abstained on 0 of 3 unanswerable queries; it removes only clearly off-topic searches and is not an abstention mechanism. Abstention is measured on the grader instead (Part A2).

Explain-query diagnosis: the Milestone 3 explanations' first queries were replayed through all three modes. Every one retrieved an acceptable section in the top five; all 14 "insufficient evidence" outcomes were duplicate flags from one templated query, rejected by the grader. Retrieval was not the bottleneck.

## Part A2: grader abstention

About 20 new unanswerable queries (topics absent from Chapters I and XI, verified by text match) plus the existing 3, and an equal number of answerable ones, each graded by the explain subgraph's grader on hybrid results. Metric: share of unanswerable queries where the grader returns no relevant passage (true abstention) and share of answerable ones where it keeps at least one (no over-abstention).

## Part B: single ReAct agent baseline (System C)

**What C vs D measures.** The full system's advantage comes from two things: deterministic check code (matching, arithmetic, duplicates, liability) and structure (a fixed graph instead of a free tool loop). C gets the same table and manual tools as D's checks use plus a calculator, so arithmetic is not handicapped; it does not get the checks themselves. C vs D therefore measures **structure plus deterministic coverage**, not structure alone; giving C a `run_checks` tool would only test copying.

**Tools (read-only, allow-list, 10 s timeout each, errors returned as text):** `get_fee_schedule_amount`, `check_code_pair`, `get_unit_limit`, `get_charge_benchmark`, `search_rules` (hybrid, top 3 passages), `get_manual_section`, `calculate` (safe arithmetic). The final answer is the same flags schema as the model-alone baseline, plus optional quotes with passage ids.

**Loop.** A LangGraph graph: `agent` (one structured model call: thought, up to 6 tool calls in parallel, or the final answer) and `tools`, repeated. Maximum 8 agent turns; exhausting them makes the case invalid (missing everything, like an invalid model-alone answer) and the exhaustion rate is reported. Built on the project's guarded model client (cache over the full transcript, caps, pacing, redaction check) rather than a library agent, which would bypass those guards. Prompt: the model-alone system prompt verbatim plus a tool section; both prompt hashes are recorded.

**Model and spend.** Same model as A and D: `gemini-3.5-flash-lite`, temperature recorded as the API default. Free tier first; only when Google's daily quota is exhausted, the same model through OpenRouter, capped at **$1.00** for all Milestone 4 runs together (owner's decision). A 429 mid-bill discards the partial run; the next run replays it from cache.

**Sample** (`eval/samples/m4_agent_sample.json`, seed 20261011, locked before any agent call by SHA-256 `16d53f3f578f375ba47d97d15778d7b47c279285f5486ddfa3ae51fa58099b75`, committed with the results): 10 tuning bills (prompt development only, never scored), 40 development (all 4 injection cases, 11 other hard cases, 25 generated stratified by kind), 75 held-out (all 15 hard cases, 60 generated stratified). A pilot on 5 tuning bills measures calls and tokens per bill before the scored runs. Systems A, B and D are re-scored on exactly the same cases; their answers are saved or cached, so this costs no calls. Full-set numbers are shown next to the sample numbers.

**Primary contrasts (held-out, Holm-corrected over these three):**
1. C vs A, case fully correct (every expected flag matched and no false flag): exact McNemar. Does tool access help a model?
2. C vs D, case fully correct: exact McNemar. Does the structured system beat a free tool loop?
3. C vs A, error precision: paired case bootstrap, 95% interval.

Everything else is descriptive: recall, dollar recall, false alarms on clean bills, traps, cannot-check, required-lookup recall (the code-pair and unit-limit lookups the answer key needs vs those the agent made), evidence validity of C's quotes, injection resistance reported as counts (x/6), model calls and tokens per bill, model latency separate from deliberate pacing. With about 75 paired held-out cases, only differences of roughly 15 percentage points or more can be detected; smaller ones are reported as not shown.

## Deferred (optional, decided after the pilot)

Run-to-run variance (3 runs on about 20 bills for A and C, Jaccard consistency) and a full-system ablation without the grader.
