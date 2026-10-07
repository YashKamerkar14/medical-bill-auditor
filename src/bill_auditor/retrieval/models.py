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
