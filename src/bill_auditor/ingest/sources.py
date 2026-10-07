"""The real CMS releases this project loads, and the dates of service they cover.

Changing a release (a new quarter, a new revision) is a deliberate edit here,
followed by a rebuild and a rerun of the acceptance tests and evaluation.
"""

from datetime import date

from bill_auditor.config import PROJECT_ROOT
from bill_auditor.ingest.releases import ReleaseSpec

# PFS National Payment Amount File, 2026, nonQP: base file A plus revisions
# B, C and D applied in order. CMS posts the revision files (B 2026-03-10,
# C 2026-06-30, D 2026-08-25) but neither the files nor the download pages give
# an effective date. The combined table is therefore only used for the window
# after all four postings, which is the project's test window (2026 Q4). Earlier
# dates of service get "cannot check: no release loaded".
PFS_YEAR = 2026
PFS_PARTS = "ABCD"
PFS_2026 = ReleaseSpec(
    dataset="pfs",
    label="PFS 2026 nonQP A-D",
    effective_start=date(2026, 10, 1),
    effective_end=date(2026, 12, 31),
)

# NCCI Policy Manual for Medicare Services, 2026 edition. Every page carries
# "Revision Date (Medicare): 1/1/2026"; the edition applies to calendar 2026.
# Keys are part labels recorded in release_files; values are files in raw/manual.
MANUAL_FILES = {
    "I": "01-chapter1-ncci-medicare-policy-manual-2026-final.pdf",
    "XI": "11-chapter11a-ncci-medicare-policy-manual-2026-final.pdf",
}
MANUAL_2026 = ReleaseSpec(
    dataset="manual",
    label="NCCI Policy Manual 2026 (rev. 2026-01-01)",
    effective_start=date(2026, 1, 1),
    effective_end=date(2026, 12, 31),
)

# Code-pair and unit-limit tables: our own made-up rules over fake Z-codes,
# committed in synthetic_tables/. Used instead of the licence-gated CMS PTP and
# MUE files (docs/decisions/0005-synthetic-ncci-tables.md).
SYNTHETIC_TABLE_DIR = PROJECT_ROOT / "synthetic_tables"
SYNTHETIC_PTP = ReleaseSpec(
    dataset="ptp",
    label="SYNTHETIC code-pair edits v1 (not Medicare data)",
    effective_start=date(2026, 10, 1),
    effective_end=date(2026, 12, 31),
    synthetic=True,
)
SYNTHETIC_MUE = ReleaseSpec(
    dataset="mue",
    label="SYNTHETIC unit limits v1 (not Medicare data)",
    effective_start=date(2026, 10, 1),
    effective_end=date(2026, 12, 31),
    synthetic=True,
)

# Real average charges, data year 2024 (the latest published, May 2026). These
# are historical averages used as a benchmark for the project's test window.
CHARGES_FILE = "MUP_PHY_R26_P05_V10_D24_Geo.csv"
CHARGES_2024 = ReleaseSpec(
    dataset="charges",
    label="Medicare Physician & Other Practitioners by Geography and Service, data year 2024",
    effective_start=date(2026, 10, 1),
    effective_end=date(2026, 12, 31),
)
