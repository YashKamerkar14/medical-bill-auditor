# 0002: Data licensing and storage

- Status: accepted
- Date: 2026-10-06
- Milestone: 0

## Context

CPT codes and descriptors are copyright of the American Medical Association. The CMS PTP and MUE downloads sit behind an AMA licence click-through that allows internal use only and forbids redistribution and derivative works. The fee schedule file shows no click-through, but it contains CPT codes, so it is treated the same way. The code folder is inside OneDrive, which syncs every file it contains to the cloud.

## Decision

1. **Data lives outside the repository**, under `DATA_DIR` (`C:\dev\bill-auditor-data`): `raw\` downloads, `bill_auditor.sqlite`, `chroma\`, `eval\`, and the virtual environment. `config.load_paths()` refuses a `DATA_DIR` inside the repository.
2. **The owner downloads licence-gated files personally** and accepts the licence. The assistant never clicks "Accept". Unlicensed public files (Policy Manual PDFs, PFREV fee schedule ZIPs) may be downloaded by the assistant with the owner's approval. *Amended by 0005: the owner chose not to use any licence-gated file; PTP and MUE are synthetic.*
3. **Nothing derived from licensed tables is committed**: no table rows, no CPT descriptors, no eval cases built from real rows. This includes the acceptance-test oracles (expected values for real codes), which are stored under `DATA_DIR\eval\`. Only the test code that reads them is committed.
4. **Unit tests use fake codes** (`ZZ001`, `ZZ002`, ...) in fixtures that copy the real file formats, so the test suite runs anywhere without CMS data.
5. **Every loaded file is recorded** with a release label, effective window, source file name and SHA-256 hash, so any flag can be traced to the exact file that produced it.
6. `.gitignore` also excludes `data/`, `*.sqlite`, `chroma/` and `*.zip` as a second line of defence.

## Consequences

- Anyone else running the project must download the CMS files themselves; the README explains how.
- Acceptance tests are skipped (not failed) when the data is not loaded, so CI can run without licensed files.
- This is cautious engineering practice, not legal advice.
