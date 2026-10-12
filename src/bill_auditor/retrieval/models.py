"""The shape every search mode returns, so modes can be compared and fused."""

from typing import Literal

from pydantic import BaseModel


class SearchHit(BaseModel):
    chunk_id: str
    section_id: str
    section_title: str
    page_start: str
    page_end: str
    text: str
    mode: Literal["keyword", "vector", "hybrid"]
    rank: int     # 1 = best within this mode
    score: float  # BM25, cosine similarity or RRF score (higher is better); not comparable across modes
    release_label: str


class SearchResult(BaseModel):
    """What the search_rules tool returns: ranked passages, or "insufficient evidence"."""
    query: str
    mode: Literal["keyword", "vector", "hybrid"]
    k: int
    hits: list[SearchHit]
    top_similarity: float | None  # best cosine similarity (vector and hybrid modes); None for keyword
    insufficient: bool            # True: nothing relevant enough; report "insufficient evidence"


class ManualSection(BaseModel):
    """One whole manual section, for quoting and verification."""
    status: Literal["found", "not_found"]
    section_id: str
    chapter: str | None = None
    chapter_title: str | None = None
    title: str | None = None
    page_start: str | None = None
    page_end: str | None = None
    text: str | None = None
    release_label: str | None = None
    message: str
