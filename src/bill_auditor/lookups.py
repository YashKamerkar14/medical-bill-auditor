"""The evidence lookups, behind one interface (Milestone 5, docs/decisions/0010).

Every check, the explain subgraph and the ReAct agent get their evidence from a
`Lookups` object and never know where it comes from:

- `DirectLookups` calls the table and search functions on the local SQLite
  database (opened read-only) and Chroma index;
- `McpLookups` (bill_auditor.mcp_client) makes the same calls over MCP to our
  own server, which wraps a `DirectLookups`.

Both return the same Pydantic models, so swapping one for the other cannot
change behaviour; Milestone 5 proves the outputs are byte-identical.

Not behind this interface, on purpose: intake (input validation) and the verify
step, which re-read the source tables directly so the verifier never trusts the
channel that produced the evidence.
"""

import re
from contextlib import closing
from datetime import date
from pathlib import Path
from typing import Iterable, Literal, Protocol

from bill_auditor.db import connect_readonly
from bill_auditor.retrieval import hybrid
from bill_auditor.retrieval.hybrid import hybrid_search
from bill_auditor.retrieval.keyword import keyword_search
from bill_auditor.retrieval.models import ManualSection, SearchResult
from bill_auditor.retrieval.vector import vector_search
from bill_auditor.tables.charges import ChargeBenchmarkResult, get_charge_benchmark
from bill_auditor.tables.code_pairs import CodePairResult, check_code_pair
from bill_auditor.tables.fee_schedule import FeeScheduleResult, Setting, get_fee_schedule_amount
from bill_auditor.tables.unit_limits import UnitLimitResult, get_unit_limit

SearchMode = Literal["keyword", "vector", "hybrid"]
MAX_K = 20
SECTION_ID = re.compile(r"^[A-Z0-9]{1,5}\.[A-Z0-9]{1,3}$")
CHAPTER_ID = re.compile(r"^[A-Z0-9]{1,5}$")


class LookupUnavailable(RuntimeError):
    """A lookup could not be answered (server down, timeout, tool error). Callers report "cannot check"."""


class Lookups(Protocol):
    def get_fee_schedule_amount(self, code: str, modifier: str | None, locality_id: str, setting: Setting,
                                date_of_service: date) -> FeeScheduleResult: ...

    def check_code_pair(self, code_a: str, code_b: str, date_of_service: date, modifiers_a: Iterable[str] = (),
                        modifiers_b: Iterable[str] = ()) -> CodePairResult: ...

    def get_unit_limit(self, code: str, date_of_service: date) -> UnitLimitResult: ...

    def get_charge_benchmark(self, code: str, setting: Setting, date_of_service: date) -> ChargeBenchmarkResult: ...

    def search_rules(self, query: str, mode: SearchMode = "hybrid", k: int = 5) -> SearchResult: ...

    def get_manual_section(self, section_id: str) -> ManualSection: ...


class DirectLookups:
    """Lookups on the local database and index. Each call opens its own read-only connection (thread-safe)."""

    def __init__(self, db_path: Path, chroma=None, embedder=None):
        self.db_path, self.chroma, self.embedder = Path(db_path), chroma, embedder

    def _conn(self):
        return closing(connect_readonly(self.db_path))

    def get_fee_schedule_amount(self, code, modifier, locality_id, setting, date_of_service) -> FeeScheduleResult:
        with self._conn() as conn:
            return get_fee_schedule_amount(conn, code, modifier, locality_id, setting, date_of_service)

    def check_code_pair(self, code_a, code_b, date_of_service, modifiers_a=(), modifiers_b=()) -> CodePairResult:
        with self._conn() as conn:
            return check_code_pair(conn, code_a, code_b, date_of_service, list(modifiers_a), list(modifiers_b))

    def get_unit_limit(self, code, date_of_service) -> UnitLimitResult:
        with self._conn() as conn:
            return get_unit_limit(conn, code, date_of_service)

    def get_charge_benchmark(self, code, setting, date_of_service) -> ChargeBenchmarkResult:
        with self._conn() as conn:
            return get_charge_benchmark(conn, code, setting, date_of_service)

    def search_rules(self, query: str, mode: SearchMode = "hybrid", k: int = 5) -> SearchResult:
        """Manual passages. The relevance floor applies wherever a cosine similarity exists (vector, hybrid)."""
        if not 1 <= k <= MAX_K:
            raise ValueError(f"k must be between 1 and {MAX_K}")
        if mode != "keyword" and (self.chroma is None or self.embedder is None):
            raise LookupUnavailable("the vector index is not loaded; only keyword search is available")
        with self._conn() as conn:
            if mode == "keyword":
                hits = keyword_search(conn, query, k)
                return SearchResult(query=query, mode=mode, k=k, hits=hits, top_similarity=None, insufficient=not hits)
            if mode == "vector":
                hits = vector_search(self.chroma, self.embedder, query, k)
                top = hits[0].score if hits else None
                insufficient = top is None or top < hybrid.MIN_TOP_SIMILARITY
                return SearchResult(query=query, mode=mode, k=k, hits=[] if insufficient else hits,
                                    top_similarity=top, insufficient=insufficient)
            found = hybrid_search(conn, self.chroma, self.embedder, query, k)
            return SearchResult(query=query, mode=mode, k=k, hits=found.hits, top_similarity=found.top_similarity,
                                insufficient=found.insufficient)

    def get_manual_section(self, section_id: str) -> ManualSection:
        if not SECTION_ID.match(section_id):
            return ManualSection(status="not_found", section_id=section_id,
                                 message="Section ids look like 'I.E' (chapter I, section E).")
        with self._conn() as conn:
            row = conn.execute(
                """SELECT s.*, r.label AS release_label FROM manual_sections s
                   JOIN releases r USING (release_id) WHERE s.section_id = ?""", (section_id,)).fetchone()
        if row is None:
            return ManualSection(status="not_found", section_id=section_id, message=f"No section {section_id!r}.")
        return ManualSection(status="found", section_id=section_id, chapter=row["chapter"],
                             chapter_title=row["chapter_title"], title=row["title"], page_start=row["page_start"],
                             page_end=row["page_end"], text=row["text"], release_label=row["release_label"],
                             message=f"Section {section_id}: {row['title']}.")

    # --- for MCP resources (whole chapters and the section list) ---------------------------

    def section_index(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute("SELECT section_id, chapter, title FROM manual_sections ORDER BY rowid").fetchall()
        return [dict(r) for r in rows]

    def chapter_text(self, chapter: str) -> str | None:
        if not CHAPTER_ID.match(chapter):
            return None
        with self._conn() as conn:
            rows = conn.execute("SELECT section_id, title, text FROM manual_sections WHERE chapter = ? ORDER BY rowid",
                                (chapter,)).fetchall()
        return "\n\n".join(f"## {r['section_id']} {r['title']}\n\n{r['text']}" for r in rows) if rows else None
