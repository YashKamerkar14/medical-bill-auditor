# Medical Bill Auditor

Audits professional (physician) medical bills against the insurer's EOB, public Medicare tables and the NCCI Policy Manual. Every flag comes from a deterministic rule, cites a table row or a quoted manual passage, and is re-checked by code before it is reported.

> Research prototype. Not legal or financial advice. Confirm any possible error with the billing office and the insurer. Synthetic bills only.

Status: Milestones 0-2 complete (data, test set with a model-alone baseline, deterministic LangGraph core); Milestone 3 (model-assisted matching and corrective-RAG explanations) built, evaluation in progress. See `PLAN.md`, results in `docs/results/` and design decisions in `docs/decisions/`.

| Development set (240 bills) | Model alone (Gemini 3.5 Flash-Lite) | Rules only (LangGraph, no model) |
| --- | --- | --- |
| Error precision | 0.31 | 1.00 |
| False alarms on clean bills | 0.59 | 0.00 |
| Dollar recall | 0.44 | 1.00 |

The rules-only scores are expected on generated bills built from the same written rules; see `docs/results/m2-rules-only.md` for how to read them.

## Licence notice

CPT codes and descriptors are copyright of the American Medical Association. This repository contains **no** CMS table rows, CPT descriptors or anything derived from them; you download the public CMS files yourself. No licence-gated file is used: the code-pair (PTP) and unit-limit (MUE) tables are **synthetic** (made-up rules over fake `Z` codes, see `synthetic_tables/`), so results for the unbundling and quantity checks are on synthetic rules, not Medicare's.

## Setup (Windows, Python 3.11 via uv)

1. Install [uv](https://docs.astral.sh/uv/). Create a data folder **outside** this repository and outside any synced folder, e.g. `C:\dev\bill-auditor-data`.
2. Copy `.env.example` to `.env` and set `DATA_DIR` to that folder. Set `UV_PROJECT_ENVIRONMENT` to `<DATA_DIR>\.venv` so the virtual environment lives there too.
3. `uv sync`
4. Download into `<DATA_DIR>\raw\`:
   - `pfs\`: PFREV26A, B, C and D ZIPs from the CMS [PFS National Payment Amount File](https://www.cms.gov/medicare/payment/fee-schedules/physician/national-payment-amount-file) page.
   - `manual\`: 2026 NCCI Policy Manual Chapter I and Chapter XI PDFs from the CMS [NCCI Policy Manual](https://www.cms.gov/medicare/coding-billing/national-correct-coding-initiative-ncci-edits/medicare-ncci-policy-manual) page (file names are listed in `src/bill_auditor/ingest/sources.py`).
   - `charges\`: `MUP_PHY_R26_P05_V10_D24_Geo.csv` from the CMS [Physician & Other Practitioners - by Geography and Service](https://data.cms.gov/provider-summary-by-type-of-service/medicare-physician-other-practitioners/medicare-physician-other-practitioners-by-geography-and-service) page (data year 2024).
5. `uv run python scripts/build_data.py` (the first run downloads the ~67 MB local embedding model).
6. `uv run pytest`

Design decisions are recorded in `docs/decisions/`.
