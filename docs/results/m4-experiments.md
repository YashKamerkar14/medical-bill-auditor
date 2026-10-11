# Milestone 4: retrieval and agent experiments

- Date: 2026-10-11
- Design and primary contrasts pre-registered in `docs/decisions/0009-milestone4-experiments.md` before any agent call
- Model for every model-using system: `gemini-3.5-flash-lite` (Google free tier first; the same model through OpenRouter only after the free daily quota, capped at $1.00 for all of Milestone 4)
- Code-pair (PTP) and unit-limit (MUE) tables are **synthetic** (fake Z-codes, ADR 0005); all results that depend on them are results on synthetic tables

## Part A: vector vs keyword vs hybrid retrieval

47 answerable labelled queries over 44 manual sections; chunks collapsed to sections, depth 20. No model calls. Raw results: `eval/results/m4_retrieval_20261011T225833Z.json`.

| Mode | hit@1 | hit@5 | MRR |
| --- | --- | --- | --- |
| Keyword (BM25, SQLite FTS5) | 0.787 | 0.936 | 0.851 |
| Vector (bge-small, cosine) | 0.872 | 0.979 | 0.913 |
| **Hybrid (RRF, k = 60), committed** | **0.872** | **1.000** | **0.927** |
| Random baseline (exact) | 0.026 | 0.127 | 0.107 |
| Titles only (keyword on section titles) | 0.404 | 0.702 | 0.538 |

Exploratory variants (not tested for significance): keyword without stopwords MRR 0.867; vector without the bge query prefix 0.904; hybrid k = 10 0.924; hybrid k = 100 0.927. RRF is insensitive to k here.

**Primary contrasts (MRR, paired permutation, Holm over two):**

| Contrast | MRR difference | p | Holm p | hit@5 McNemar (wins/losses, p) |
| --- | --- | --- | --- | --- |
| Hybrid vs keyword | +0.076 | 0.075 | 0.150 | 3 / 0, p = 0.25 |
| Hybrid vs vector | +0.014 | 0.713 | 0.713 | 1 / 0, p = 1.00 |

Hybrid is best on every metric, but with 47 queries neither difference is statistically shown. Hybrid is kept because it never loses a query at depth 5 and it costs nothing extra at this corpus size.

**Where keyword search fails: plain language.**

| Query style (n) | Keyword MRR | Vector MRR | Hybrid MRR |
| --- | --- | --- | --- |
| Codes (7) | 1.000 | 1.000 | 1.000 |
| Manual terms (24) | 0.859 | 0.889 | 0.910 |
| Plain language (16) | 0.773 | 0.911 | 0.922 |

| Query-title word overlap (n) | Keyword MRR | Vector MRR | Hybrid MRR |
| --- | --- | --- | --- |
| below 0.25 (33) | 0.835 | 0.907 | 0.927 |
| 0.25 to 0.5 (9) | 0.828 | 0.889 | 0.889 |
| 0.5 and above (5) | 1.000 | 1.000 | 1.000 |

Most queries share little vocabulary with their section's title, so the set is not a title-matching test (titles-only keyword reaches MRR 0.54).

**Relevance floor.** The 0.55 top-cosine floor abstained on 0 of 3 unanswerable queries (their top similarities 0.63 to 0.67; the lowest answerable top similarity is 0.60). It only removes clearly off-topic searches; it is not an abstention mechanism. Abstention was measured on the grader instead (Part A2).

**Explain-query diagnosis.** The 74 Milestone 3 development explanations' first queries were replayed through all three modes: every one put an acceptable section in the top five under every mode. All 14 "insufficient evidence" outcomes are duplicate-charge flags from a single templated query that the grader rejected (7 other duplicate flags with the same query were explained), and the manual has no explicit duplicate-billing rule. Retrieval was not the bottleneck; the grader's judgement on a rule the manual only implies was.

## Part A2: does the grader abstain when the manual has no answer?

20 new questions on topics absent from Chapters I and XI (each key term checked by text match: 0 occurrences), plus the 3 existing unanswerable queries, against the first 23 answerable labelled queries. Each question went through the explain subgraph's own hybrid retrieval and grader prompt, the question standing in for the finding. 46 model calls. Raw results: `eval/results/m4_abstention_20261011T233822Z.json`.

| Measure | Result |
| --- | --- |
| True abstention: unanswerable, grader kept no passage | **23/23** |
| Of which the relevance floor abstained first | 0/23 |
| Over-abstention: answerable, grader kept nothing | 1/23 |
| Answerable, grader kept a passage from an expected section | 20/23 |
| Invalid grader answers | 0 |

The abstention in the corrective-RAG loop comes from the grader, not from the similarity floor. Together with Part A this locates the Milestone 3 "insufficient evidence" outcomes: the grader correctly refuses off-topic passages, and on duplicate charges it is strict about a rule the manual only implies.

## Part B: single ReAct agent baseline (System C)

C is a LangGraph agent⇄tools loop on the guarded model client: the model-alone prompt verbatim plus a tool section, seven read-only tools (fee schedule, code pair, unit limit, charge benchmark, manual search, manual section, safe calculator), at most 8 turns. Sample `eval/samples/m4_agent_sample.json` (SHA-256 `16d53f3f…`, recorded in ADR 0009 before any agent call). A, B and D were re-scored on exactly the same cases. Raw results: `eval/results/m4_agent_dev_scored_20261011T231814Z.json`, `eval/results/m4_agent_heldout_scored_20261011T233427Z.json`.

### Held-out (75 bills, 19 clean): primary results

| System | Fully correct | Error recall | Error precision | Dollar recall | False alarms, clean bills |
| --- | --- | --- | --- | --- | --- |
| A: model alone | 0.453 | 0.67 (30/45) | 0.43 (30/70) | 0.46 | 0.32 |
| B: rules only | 0.987 | 0.98 (44/45) | 1.00 | 0.95 | 0.00 |
| **C: ReAct agent** | **0.747** | **0.87 (39/45)** | **0.91 (39/43)** | **0.85** | **0.00** |
| D: full system | 1.000 | 1.00 (45/45) | 1.00 | 1.00 | 0.00 |

| Pre-registered contrast (Holm over three) | Result | p | Holm p |
| --- | --- | --- | --- |
| 1. C vs A, fully correct (exact McNemar) | C right and A wrong on 29 bills, the reverse on 7 | 0.0003 | 0.0003 |
| 2. C vs D, fully correct (exact McNemar) | D right and C wrong on 19 bills, the reverse on 0 | 0.000004 | 0.000008 |
| 3. C vs A, error precision (paired bootstrap) | +0.48 (95% CI +0.34 to +0.61) | < 0.0005 | < 0.0015 |

All three are significant after correction. **Tools help a model a lot (C beats A), and the structured system beats the free tool loop (D beats C on every bill where they differ).**

### Development (40 bills, 10 clean), same pattern

| System | Fully correct | Error recall | Error precision | Dollar recall | False alarms, clean |
| --- | --- | --- | --- | --- | --- |
| A | 0.375 | 0.65 | 0.38 | 0.37 | 0.50 |
| B | 1.000 | 1.00 | 1.00 | 1.00 | 0.00 |
| C | 0.775 | 0.90 | 0.82 | 0.89 | 0.00 |
| D | 1.000 | 1.00 | 1.00 | 1.00 | 0.00 |

The sample is representative: on the full held-out split (Milestones 1–3) A has recall 0.67, precision 0.42 and false alarms 0.33; B 0.985 / 1.00 / 0.00; D 1.00 / 1.00 / 0.00.

### Where tools helped and where the agent still fails (held-out, descriptive)

| Check (synthetic tables where marked) | A recall | C recall | D recall |
| --- | --- | --- | --- |
| Quantity / unit limits (synthetic MUE) | 0/7 | 6/7 | 7/7 |
| Unbundling / code pairs (synthetic PTP) | 3/8 | 6/8 | 8/8 |
| Duplicate | 10/11 | 11/11 | 11/11 |
| Arithmetic | 8/9 | 9/9 | 9/9 |
| Provider liability (bill vs EOB) | 9/10 | 7/10 | 10/10 |

- **Lookups turn guesses into findings.** A cannot know a synthetic unit limit or code-pair edit; with tools C finds most of them.
- **But the agent does not look up everything it should.** Required-lookup recall (the unit-limit and code-pair lookups the answer key depends on, vs those C made): 49/94 held-out, 26/67 development. All 4 of its quantity and unbundling misses (development and held-out) are bills where it never made the decisive lookup; on `gen-0089` it checked 10 code pairs, but not the one pair that carried the edit. D's checks run every lookup by construction.
- **It is worse than the model alone on provider liability** (7/10 vs 9/10). On the 3 missed bills it reported a duplicate instead (`hard-m08`, no tools used) or spent its calls on code-pair and unit-limit lookups and returned no liability flag (`gen-0006`, `hard-m02`). With 10 such bills this difference is not meaningful by itself; D's deterministic bill-to-EOB matching finds all 10.
- **Errors it adds**: 17 unwanted price-benchmark notes (benchmark precision 0.11), 3 knock-on arithmetic flags (flagging the total and balance due as separate errors after finding one wrong line), 1 false unbundling flag. On billing errors alone (ignoring benchmark notes) C is fully correct on 66/75 held-out bills.
- **It never searched the manual** and made no quotes (0 quotes; evidence validity not applicable), so C gives no cited explanations; every quote in D's reports is verified against the manual (Milestone 3).
- **Prompt injection**: C followed no injected instruction on any of the 6 injection bills (4 development, 2 held-out). It was fully correct on 5/6; on `hard-i02` it correctly flagged the line it was told not to flag, then added the two knock-on arithmetic flags above.

### Cost and operations (C)

| | Development | Held-out |
| --- | --- | --- |
| Invalid or out-of-turns answers | 0/40 | 0/75 |
| Mean turns (model calls) per bill | 2.25 | 2.23 |
| Mean tool calls per bill | 5.9 | 6.1 |
| Mean input tokens per bill | 6,185 | 6,575 |
| Mean seconds per bill (model latency plus 5 s pacing) | 11.1 | 12.5 |

D needs no model call on most bills (only ambiguous matching and explanations); A makes one call per bill. All of Milestone 4 used 317 model calls (pilot 11, C 257, three missing model-alone answers, grader abstention 46), every one on the Gemini free tier. **OpenRouter spend: $0.00 of the $1.00 cap.**

## Conclusions

1. Retrieval: hybrid RRF is the best mode numerically but not significantly better than vector on this 47-query set; keyword search alone is weak on plain-language questions.
2. Abstention lives in the grader (23/23 unanswerable refused, 1/23 over-abstention), not in the similarity floor (0/3).
3. Giving a model the same tools in a free ReAct loop raises "fully correct" from 45% to 75% and error precision from 0.43 to 0.91 (both significant), but it still misses lookups, adds unwanted flags and gives no cited evidence. The structured graph (D) is fully correct on every held-out bill, beating C on 19 bills and losing on none (significant). This supports the project's design: deterministic checks for what can be computed, the model only where judgement is needed.

## Deferred

Run-to-run variance (3 runs × 20 bills for A and C) and the full-system ablation without the grader were optional in ADR 0009. With every primary contrast far from the significance boundary, they would not change any conclusion; they are left for later.
