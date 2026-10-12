# Milestone 7: letter, PDF bills, app and README

- Date: 2026-10-11
- Design: `docs/decisions/0012-letter-pdf-ui.md`

## Done when: someone else can run it from the README

Tested by following the README literally in a fresh environment, with nothing carried over:

- only the files a clone contains (211, exported with `git ls-files`), in a new folder outside OneDrive;
- a new data folder and a new virtual environment;
- every inherited setting cleared (`UV_PROJECT_ENVIRONMENT`, `DATA_DIR`, API keys).

| README step | Result |
| --- | --- |
| 2. Data folder and `.env` from `.env.example` | done |
| 3. `uv sync` | new environment built from `uv.lock` |
| 4. Download the CMS files | simulated by copying the same public files (this step is manual on purpose; see below) |
| 5. `uv run python scripts/build_data.py` | all tables and indexes built in 36 s, including the embedding-model download |
| 6. `uv run pytest` | **435 passed**, 3 skipped (they need the owner's private lookup-oracle file, never committed) |
| 7. Three example commands | the right findings: duplicate $300 (with the injection warning), provider liability $90, and a draft letter written |
| 8. `uv run streamlit run src/bill_auditor/ui/app.py` | server up, health check `ok` |

**Not tested:** downloading the CMS files from the CMS web pages. That step stays manual because some CMS pages ask the user to accept a licence, and accepting one is the user's decision. The links and file names in the README are the ones used to build the data in Milestone 0.

## Dispute letter

Assembled by code from verified, high-confidence findings; no model writes it.

| Guardrail | How it is enforced | Test |
| --- | --- | --- |
| Human review first | `reviewed` must be true: a checkbox in the app (the button stays disabled), `--i-have-reviewed` on the command line | refused without it, in the app and the command line |
| Only verified, high-confidence errors | eligibility is `confidence == "high"` on a verified error; benchmarks and model-matched (medium) findings excluded | a medium flag is refused |
| No unverified law or regulation | the letter's own text may not match a legal-citation pattern; only verified manual quotes are cited | an unverified explanation's quote never appears |
| Neutral wording | the wording check from Milestone 6 | a "fraud" message is refused |
| Draft and notice | "DRAFT - REVIEW BEFORE SENDING" banner and the research-prototype notice | checked |
| Synthetic rules labelled | `[SYNTHETIC]` per item and a warning when any item uses synthetic tables | checked both ways |
| Traceability | every item is one flag (`Letter.flag_ids`) | 100% by construction |

## PDF bills

`uv run python -m bill_auditor.evaluation.run_eval pdf-accuracy` renders every test bill and EOB as a PDF, parses it back, and compares field by field (`eval/results/m7_pdf_accuracy_20261012T040813Z.json`).

| Measure | Result | Target (PLAN.md) |
| --- | --- | --- |
| Line-item accuracy, bills | **1,433 / 1,433** | 0.98 |
| Bills identical after the round trip | 348 / 348 | |
| EOB line accuracy | 335 / 335 | |
| EOBs identical | 93 / 93 | |

The round trip found one real bug: a table split across pages lost its second half. Fixed by merging tables that share a heading. Parsed PDFs go through the same validation, injection scan and redaction as JSON: an instruction rendered into a PDF is reported as an input warning (tested).

## App and command line

- `uv run streamlit run src/bill_auditor/ui/app.py`. Choose an example or upload a synthetic bill (JSON or PDF), pick rules only or the full system, read the report (metrics, input warnings, the full report), then draft a letter after the review step. It runs locally only and has no switch for real bills.
- `uv run python -m bill_auditor.audit BILL [--eob EOB] [--full] [--letter FILE --i-have-reviewed]`.
- Tested headlessly: Streamlit `AppTest` drives example, audit, metrics, letter refused before review, then drafted; the command line is tested on PDF input, letter refusal and approval, an out-of-scope bill and an unsupported file.

## Tests

450 pass on the owner's machine (431 unit tests on fake fixtures, 19 acceptance tests on real data). The fresh environment passed 435, with 3 skipped as above.
