"""Build every reference table and index under DATA_DIR.

Safe to re-run: a dataset whose source files are unchanged (same SHA-256) is
skipped; a changed file replaces its release in one transaction.

    uv run python scripts/build_data.py
"""

import time

from bill_auditor.config import load_paths
from bill_auditor.db import connect
from bill_auditor.ingest.index_manual import index_manual, open_chroma
from bill_auditor.ingest.load_charges import load_charges
from bill_auditor.ingest.load_pfs import load_pfs
from bill_auditor.ingest.load_synthetic_ncci import load_synthetic_mue, load_synthetic_ptp
from bill_auditor.ingest.schema import create_schema
from bill_auditor.ingest.sources import (
    CHARGES_2024,
    CHARGES_FILE,
    MANUAL_2026,
    MANUAL_FILES,
    PFS_2026,
    PFS_PARTS,
    PFS_YEAR,
    SYNTHETIC_MUE,
    SYNTHETIC_PTP,
    SYNTHETIC_TABLE_DIR,
)
from bill_auditor.retrieval.embeddings import FastEmbedEmbedder


def report(tag: str, label: str, result, started: float, describe) -> None:
    if result is None:
        print(f"[{tag}] {label}: unchanged, skipped")
    else:
        print(f"[{tag}] {label}: {describe(result)} in {time.perf_counter() - started:.1f}s")


def main() -> None:
    paths = load_paths()
    conn = connect(paths.sqlite_path)
    create_schema(conn)

    started = time.perf_counter()
    parts = load_pfs(conn, paths.raw_dir / "pfs", PFS_2026, PFS_YEAR, PFS_PARTS)
    report("pfs", PFS_2026.label, parts, started,
           lambda ps: "; ".join(f"{p.part}: {p.rows:,} rows ({p.replaced:,} replaced, {p.added:,} added)" for p in ps))

    started = time.perf_counter()
    charges = load_charges(conn, paths.raw_dir / "charges" / CHARGES_FILE, CHARGES_2024)
    report("charges", CHARGES_2024.label, charges, started,
           lambda s: f"{s.rows:,} rows ({s.skipped_blank_state} rows without a state skipped)")

    started = time.perf_counter()
    report("ptp", SYNTHETIC_PTP.label, load_synthetic_ptp(conn, SYNTHETIC_TABLE_DIR, SYNTHETIC_PTP), started,
           lambda n: f"{n} edits")
    report("mue", SYNTHETIC_MUE.label, load_synthetic_mue(conn, SYNTHETIC_TABLE_DIR, SYNTHETIC_MUE), started,
           lambda n: f"{n} limits")

    started = time.perf_counter()
    embedder = FastEmbedEmbedder(paths.models_dir)
    pdfs = {part: paths.raw_dir / "manual" / name for part, name in MANUAL_FILES.items()}
    summary = index_manual(conn, open_chroma(paths.chroma_dir), pdfs, MANUAL_2026, embedder)
    report("manual", MANUAL_2026.label, summary, started, lambda s: f"{s.sections} sections, {s.chunks} chunks")

    conn.close()


if __name__ == "__main__":
    main()
