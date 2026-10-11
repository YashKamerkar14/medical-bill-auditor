# Milestone 3: full system vs rules only

- Date: 2026-10-08
- System D, full system: the LangGraph audit graph plus `gemini-3.5-flash-lite` (Google free tier) for ambiguous bill/EOB matching and corrective-RAG explanations (ADR 0008)
- System B, rules only: the same graph with no model (ADR 0007)
- Cases: development 243, held-out 105 (300 generated + 48 hand-written hard cases, including 4 new ambiguous-matching cases)
- Raw results: `eval/results/m3_full_system_dev_20261009T001312Z.json`, `eval/results/m3_full_system_heldout_20261009T002127Z.json`, `eval/results/m2_rules_only_dev_20261009T002237Z.json`, `eval/results/m2_rules_only_heldout_20261009T002247Z.json`

## Done when

- **Evidence validity 100% on the development set**: every explanation in a final report has every quote verified verbatim against the manual, re-checked independently by the evaluator (57/57 development, 29/29 held-out).
- **Full system vs rules-only comparison recorded**: below.

## Detection

| Metric | Dev: rules only | Dev: full | Held-out: rules only | Held-out: full |
| --- | --- | --- | --- | --- |
| Error recall, all checks | 0.992 (128/129) | **1.000** (129/129) | 0.985 (64/65) | **1.000** (65/65) |
| Error precision | 1.000 | 1.000 | 1.000 | 1.000 |
| Recall, provider liability | 0.970 (32/33) | **1.000** (33/33) | 0.929 (13/14) | **1.000** (14/14) |
| Amount accuracy | 1.000 | 1.000 | 1.000 | 1.000 |
| Dollar recall | 0.996 | **1.000** | 0.968 | **1.000** |
| Correct "cannot check" | 1.000 (17/17) | 1.000 (17/17) | 1.000 (6/6) | 1.000 (6/6) |
| Trap pass rate | 1.000 | 1.000 | 1.000 | 1.000 |
| False alarms, clean bills | 0.000 | 0.000 | 0.000 | 0.000 |

The only difference is matching. The rules-only system abstains on the two cases where only the text can pair bill and EOB lines (`hard-m09`, right vs left forearm; `hard-m11`, chest vs abdomen) and so misses their overcharges; the model pairs them from the descriptions and remarks, code validates the pairs, and the liability check finds both. On `hard-m12`, where nothing in the text says which EOB line is which, the model returned "cannot tell" and both systems correctly report "cannot check". On `hard-m10` (same as m09 but clean) neither raises a flag. The model cannot add, remove or resize a flag; precision and false alarms are unchanged.

## Explanations (corrective RAG)

| Metric | Development | Held-out |
| --- | --- | --- |
| Flags sent to the explain subgraph (code pairs, unit limits, duplicates) | 74 | 38 |
| Explained with verified quotes | 57 | 29 |
| Insufficient evidence (no relevant passage after 2 query rewrites) | 14 | 9 |
| Explanation dropped after one regeneration (flag kept, "no verified manual passage") | 3 | 0 |
| Raw quote accuracy (verified / proposed, before the guardrail) | 0.83 | 0.93 |
| **Final evidence validity** | **1.00** | **1.00** |
| Explanations citing a section where the rule is set out | 0.91 | 0.83 |

The guardrail matters: about 1 in 6 quotes the model proposed on the development set did not match the manual verbatim; none of them reached a report. "Insufficient evidence" is reported rather than an explanation built from loosely related text.

## Operations

- Every run within the step budget (22): 243/243 and 105/105; at most 13 node executions.
- Model calls on the free tier: the evaluation spanned two days because the free tier allows 500 requests per day for this model (ADR 0008); all answers are cached, so re-scoring costs nothing. Final development pass: 34 new calls, 169 from cache; held-out: 99 new, 17 cached.
- No money spent.

## Caveats

- The generated bills plant errors defined by the same rules the checks implement; detection results show correctness, and the comparison isolates what the model adds (matching), not a general claim that rules beat models.
- Small model (`gemini-3.5-flash-lite`); explanation quality would likely improve with a larger one.
- Section match uses a fixed list of acceptable sections per check type plus the section named in the synthetic rule's rationale; it is an indicator, not a graded judgment of explanation quality.
- Code-pair and unit-limit rules are synthetic (ADR 0005); explanations describe the general rule from the real manual and are told not to claim the manual names the synthetic codes.
