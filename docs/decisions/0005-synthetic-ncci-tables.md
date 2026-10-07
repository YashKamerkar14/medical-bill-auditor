# 0005: Synthetic PTP/MUE tables and real charge benchmarks

- Status: accepted
- Date: 2026-10-06
- Milestone: 0
- Supersedes: the PTP and MUE rows of PLAN.md "Data sources" (v2.1)

## Context

The CMS practitioner PTP (code-pair) and MUE (unit-limit) tables sit behind the AMA CPT licence click-through. The project owner decided not to accept that licence. No licence-free copy of these tables exists, and skipping the licence page (e.g. a direct file link) would not remove the licence, so that is not an option either. A search of Hugging Face, GitHub, Kaggle and CMS (2026-10-06) found no public dataset of real itemized patient bills or EOBs (they are protected health information) and no labelled billing-error data.

## Decision

1. **Synthetic PTP and MUE tables**, hand-written in `synthetic_tables/` and committed:
   - Fake codes only, `Z` + 4 digits. No real HCPCS/CPT code starts with Z (checked against all 1,038,443 fee schedule rows and all codes in the charges file).
   - Rule semantics follow the public NCCI Policy Manual 2026: modifier indicators 0/1/9 and the PTP-associated modifier list (Chapter I, Section E), MUE adjudication indicators 1/2/3 (Chapter I, Section V).
   - Every row exercises one branch: indicator 0, 1 and 9; a deleted edit; an edit added mid-quarter; an indicator change mid-quarter; MAI 1, 2 and 3; a code with no limit.
   - Convention (ours): an edit applies when `effective_date <= DOS < deletion_date`.
   - The loader validates every row (fake codes only, codes listed in `codes.csv`, indicator 9 if and only if deleted on its effective date, no overlapping edits for a pair in either order) and refuses to load them under a non-synthetic release.
2. **`synthetic` flag on every release.** Lookup results carry `synthetic: true`, labels start with `SYNTHETIC`, and messages say so, so no report can present made-up rules as Medicare rules.
3. **Real charge benchmarks added**: CMS "Medicare Physician & Other Practitioners - by Geography and Service", data year 2024 (published May 2026; catalogued as public on data.cms.gov, no licence click-through). National and state averages of submitted charge, allowed amount and payment per code and setting. No patient or provider identifiers. Used to make synthetic bills realistic and as price context; never as evidence of an error.

## Consequences

- All six checks are still built and tested. Four use real CMS data (provider liability via the EOB, duplicates and arithmetic on the bill, price benchmark via the fee schedule and charges); unbundling and quantity run on synthetic rules.
- Synthetic bills will mix real codes (priced from the fee schedule) with Z-codes (for code-pair and unit-limit errors). A Z-code has no Medicare price, so the price check reports "cannot check" for it.
- The "model alone" baseline cannot know rules for invented codes, so its unbundling and quantity scores measure something different from the other checks. Those two checks are reported separately in every comparison.
- The README states that unbundling and quantity results are on synthetic rules. A licensed user could add a loader for the real CMS files later; nothing else would change.
- The charges data are historical averages (2024) used as a benchmark for the 2026 Q4 test window; reports label them with their data year.
