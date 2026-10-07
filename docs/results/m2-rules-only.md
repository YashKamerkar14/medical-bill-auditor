# Milestone 2: rules only vs model alone

- Date: 2026-10-07
- System B, rules only: the LangGraph audit graph with deterministic checks, no model (ADR 0007)
- System A, model alone: `gemini-3.5-flash-lite` with the bill and EOB in the prompt (Milestone 1)
- Same cases: 240 development and 104 held-out (generated + hand-written hard cases)
- Raw results: `eval/results/m2_rules_only_dev_20261007T215430Z.json`, `eval/results/m2_rules_only_heldout_20261007T215635Z.json`

95% Wilson intervals; quantity and unbundling use synthetic rules over fake Z-codes (ADR 0005).

## Development set (240 cases)

| Metric | Model alone | Rules only |
| --- | --- | --- |
| Error recall, real-rule checks | 0.93 [0.85-0.97] | **1.00** [0.95-1.00] |
| Error recall, synthetic-rule checks | 0.11 [0.05-0.23] | **1.00** [0.93-1.00] |
| Error precision, all checks | 0.31 [0.26-0.38] | **1.00** [0.97-1.00] |
| False-alarm rate, clean bills | 0.59 [0.46-0.71] | **0.00** [0.00-0.06] |
| Amount accuracy | 0.88 [0.79-0.94] | **1.00** [0.97-1.00] |
| Dollar recall | 0.44 [0.33-0.55] | **1.00** |
| Price-benchmark recall | 0.00 [0.00-0.12] | **1.00** [0.88-1.00] |
| Correct "cannot check" | 0.87 [0.62-0.96] | **1.00** [0.80-1.00] |
| Trap pass rate | 0.96 [0.82-0.99] | **1.00** [0.88-1.00] |

## Held-out set (104 cases)

| Metric | Model alone | Rules only |
| --- | --- | --- |
| Error recall, all checks | 0.67 [0.55-0.77] | **1.00** [0.94-1.00] |
| Error precision, all checks | 0.42 [0.33-0.52] | **1.00** [0.94-1.00] |
| False-alarm rate, clean bills | 0.33 [0.19-0.52] | **0.00** [0.00-0.12] |
| Dollar recall | 0.43 [0.29-0.61] | **1.00** |

## Operations

- Every run within the step budget: 240/240 and 104/104 (10 node executions each; budget 12; out-of-scope bills use 2).
- Verify dropped no flags (every check output re-confirmed); tampered flags are dropped in unit tests.
- About 0.1 s per bill, no model calls, no cost.
- The prompt-injection case that fooled the model alone (`hard-i01`) changes nothing for the rules-only system.

## How to read this

The rules-only system's perfect scores are expected: the generated bills plant errors defined by the same written rules the checks implement, and the hard cases were written against those rules too. They show the implementation is correct and complete for the defined checks, not that rules beat models in general. What the comparison does show: on tasks that are arithmetic and table lookups, a model alone is the wrong tool (precision 0.31-0.42, unable to apply rules it does not have), and deterministic checks are exact. Milestone 3 adds the model only where language matters (ambiguous matching, explanations from the manual) and must keep these numbers while adding grounded explanations.
