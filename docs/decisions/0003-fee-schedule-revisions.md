# 0003: Fee schedule revisions and effective window

- Status: accepted
- Date: 2026-10-06
- Milestone: 0

## Context

The 2026 PFS National Payment Amount File comes as four ZIPs: PFREV26A (posted 2025-12-29) and revisions B (2026-03-10), C (2026-06-30) and D (2026-08-25). The layout PDF says revisions "are not overlay files". Neither the files nor the CMS pages give an effective date for a revision.

Measured on the real nonQP files (2026-10-06), keyed by (carrier, locality, code, modifier):

| Part | Rows | Codes | Replaces a key in A | New keys |
| --- | --- | --- | --- | --- |
| A | 1,035,391 | | | all |
| B | 4,033 | 37 | 1,635 (only the two amount columns change) | 2,398 |
| C | 1,635 | 15 | 1,635 (values identical to A) | 0 |
| D | 654 | 6 | 0 | 654 |

No key appears in more than one revision, and no revision removes a key. Each file ends with four `TRL-` copyright rows.

## Decision

1. Apply parts in order as **upserts** into one release, `PFS 2026 nonQP A-D`. Each row stores `source_part`, the part that last wrote it. Each part is staged first, so a repeated key inside a part is an error and the loader can report how many keys it replaced and added.
2. All four parts must be present; a missing revision is an error, not a smaller table.
3. The release is used only for dates of service **2026-10-01 to 2026-12-31**, after all four postings. Because effective dates are unknown, using it for earlier dates could apply a revision before it took effect. Earlier dates return `no_release` ("cannot check").
4. Amounts are stored as integer cents, parsed from text without floating point.
5. Only modifiers 26, TC and 53 have their own rows. Any other modifier is priced from the global (blank) row, and the result reports the modifier actually used for pricing.

## Consequences

- When CMS posts PFREV26E or the 2027 file, `ingest/sources.py` changes and the window decision is revisited.
- Re-running the load with unchanged files is a no-op (hashes match); changed files replace the release in one transaction.
