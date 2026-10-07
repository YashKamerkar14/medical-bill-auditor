# Synthetic NCCI tables

**These are made-up rules over made-up codes. They are not Medicare data.**

The real CMS PTP (code-pair) and MUE (unit-limit) tables require accepting the AMA CPT licence, so this project uses its own small tables instead (decision: `docs/decisions/0005-synthetic-ncci-tables.md`).

- `codes.csv`: the fake code catalogue. Every code is `Z` + 4 digits; no real HCPCS/CPT code starts with Z. Every code used in the other two files must be listed here.
- `ptp_edits.csv`: code pairs that should not be billed together. An edit applies when `effective_date <= date of service < deletion_date` (blank deletion date = still active). `modifier_indicator`: 0 = modifiers cannot bypass the edit, 1 = an NCCI PTP-associated modifier may bypass it, 9 = not applicable (only for edits deleted on their effective date).
- `mue_limits.csv`: maximum units per code. `mai` (MUE adjudication indicator): 1 = claim line edit, 2 = per-day edit based on policy, 3 = per-day edit based on clinical benchmarks. A code not listed has no published limit ("cannot check").

The meanings of the indicators and the list of modifiers come from the real, public NCCI Policy Manual (Chapter I, sections E and V). Each row exists to exercise one branch of the rules; the rationale column says which.

Every report that uses these tables says so: lookups carry `synthetic: true` and a release label starting with `SYNTHETIC`.
