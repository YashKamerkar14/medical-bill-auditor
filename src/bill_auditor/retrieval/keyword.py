"""Keyword search over manual chunks with SQLite FTS5 and BM25 ranking.

BM25 rewards chunks that contain the query's rare terms. It is strong on exact
tokens such as code numbers ("96372") and modifier names ("modifier 59"),
which embeddings often blur.
"""

import re
import sqlite3

from bill_auditor.retrieval.models import SearchHit

# Section titles are short and topical, so a match there counts double.
TITLE_WEIGHT = 2.0
TEXT_WEIGHT = 1.0

# Common English words that carry no topic. With every word ORed, a plain-language
# question ("Can they bill ... the same day?") otherwise matches almost every chunk
# on "can", "they", "the". Milestone 4 measures search with and without removing them.
STOPWORDS = frozenset("""
a an and are as at be but by can could did do does for from had has have how i if in is it its me my no not
of on or our should so than that the their them then there these they this to too was we were what when
where which who why will with would you your
""".split())


def to_fts_query(query: str, drop_stopwords: bool = False, titles_only: bool = False) -> str | None:
    """Turn free text into a safe FTS5 query: every word quoted, joined with OR.

    Raw user text would break FTS5 syntax ("E&M", "modifier-25", quotes), and a
    model-written query must never be able to inject FTS5 operators.
    `titles_only` restricts matching to the section title column (an "is this just
    title lookup?" baseline).
    """
    terms = re.findall(r"[A-Za-z0-9]+", query)
    if drop_stopwords:
        terms = [t for t in terms if t.lower() not in STOPWORDS] or terms
    if not terms:
        return None
    expression = " OR ".join(f'"{term}"' for term in terms)
    return f"{{section_title}} : ({expression})" if titles_only else expression


def keyword_search(conn: sqlite3.Connection, query: str, k: int = 5, drop_stopwords: bool = False,
                   titles_only: bool = False) -> list[SearchHit]:
    match = to_fts_query(query, drop_stopwords, titles_only)
    if match is None:
        return []
    rows = conn.execute(
        f"""SELECT c.chunk_id, c.section_id, c.section_title, c.page_start, c.page_end, c.text,
                   bm25(manual_fts, {TITLE_WEIGHT}, {TEXT_WEIGHT}) AS bm25,
                   r.label AS release_label
            FROM manual_fts
            JOIN manual_chunks c ON c.chunk_rowid = manual_fts.rowid
            JOIN manual_sections s ON s.section_id = c.section_id
            JOIN releases r ON r.release_id = s.release_id
            WHERE manual_fts MATCH ?
            ORDER BY bm25
            LIMIT ?""",
        (match, k),
    ).fetchall()
    # SQLite's bm25() is negative, lower = better; flip it so higher = better.
    return [
        SearchHit(
            chunk_id=row["chunk_id"],
            section_id=row["section_id"],
            section_title=row["section_title"],
            page_start=row["page_start"],
            page_end=row["page_end"],
            text=row["text"],
            mode="keyword",
            rank=rank,
            score=-row["bm25"],
            release_label=row["release_label"],
        )
        for rank, row in enumerate(rows, start=1)
    ]
