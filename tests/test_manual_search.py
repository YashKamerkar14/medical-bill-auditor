"""Indexing and search tests on the fake chapter, with a fake embedder (no model download)."""

import math
import re
import zlib
from datetime import date

import pytest

from bill_auditor.ingest.index_manual import index_manual, open_chroma
from bill_auditor.ingest.releases import ReleaseSpec
from bill_auditor.retrieval.keyword import keyword_search, to_fts_query
from bill_auditor.retrieval.vector import EmbeddingModelMismatch, vector_search
from fake_manual import FAKE_CHAPTER

SPEC = ReleaseSpec("manual", "NCCI Policy Manual (test)", date(2026, 1, 1), date(2026, 12, 31))


class HashingEmbedder:
    """Deterministic bag-of-words vectors: texts sharing words get similar vectors."""

    def __init__(self, model_name: str = "fake-hashing-64"):
        self.model_name = model_name

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * 64
        for word in re.findall(r"[a-z0-9]+", text.lower()):
            vector[zlib.crc32(word.encode()) % 64] += 1.0
        norm = math.sqrt(sum(x * x for x in vector)) or 1.0
        return [x / norm for x in vector]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)


@pytest.fixture
def pdf_path(tmp_path):
    path = tmp_path / "09-fake-chapter.pdf"
    path.write_bytes(b"stands in for a PDF; read_pages below returns the fake chapter")
    return path


@pytest.fixture
def chroma(tmp_path):
    return open_chroma(tmp_path / "chroma")


@pytest.fixture
def indexed(empty_db, chroma, pdf_path):
    summary = index_manual(
        empty_db, chroma, {"09": pdf_path}, SPEC, HashingEmbedder(), read_pages=lambda _: FAKE_CHAPTER
    )
    return empty_db, chroma, summary


def test_index_stores_sections_and_non_empty_chunks(indexed):
    conn, chroma, summary = indexed
    assert (summary.sections, summary.chunks) == (4, 2)
    assert conn.execute("SELECT COUNT(*) FROM manual_sections").fetchone()[0] == 4
    assert chroma.get_collection("ncci_manual").count() == 2


def test_release_records_the_source_file(indexed):
    conn, _, _ = indexed
    row = conn.execute("SELECT part, file_name, rows_loaded FROM release_files").fetchone()
    assert tuple(row) == ("09", "09-fake-chapter.pdf", 2)


def test_keyword_search_finds_exact_code(indexed):
    conn, _, _ = indexed
    hits = keyword_search(conn, "ZZ002", k=3)
    assert hits[0].section_id == "IX.A"
    assert hits[0].mode == "keyword" and hits[0].rank == 1
    assert hits[0].release_label == "NCCI Policy Manual (test)"


def test_keyword_search_stems_words(indexed):
    conn, _, _ = indexed
    assert keyword_search(conn, "principle")[0].section_id == "IX.A"  # text says "principles"


def test_keyword_scores_are_higher_for_better_matches(indexed):
    conn, _, _ = indexed
    hits = keyword_search(conn, "section C body page", k=5)
    assert [h.rank for h in hits] == list(range(1, len(hits) + 1))
    assert all(a.score >= b.score for a, b in zip(hits, hits[1:]))


@pytest.mark.parametrize("query", ['E&M (modifier-25)?', 'NEAR("x" OR', "  ", "***"])
def test_keyword_search_survives_hostile_or_odd_queries(indexed, query):
    conn, _, _ = indexed
    keyword_search(conn, query)  # must not raise an FTS5 syntax error


def test_fts_query_quotes_every_term():
    assert to_fts_query('modifier-25 "OR" x') == '"modifier" OR "25" OR "OR" OR "x"'
    assert to_fts_query("!!!") is None


def test_vector_search_returns_nearest_chunk_with_cosine_score(indexed):
    _, chroma, _ = indexed
    hits = vector_search(chroma, HashingEmbedder(), "More of section C on the next page", k=2)
    assert hits[0].section_id == "IX.C"
    assert hits[0].mode == "vector"
    assert -1.0 <= hits[1].score <= hits[0].score <= 1.0
    assert hits[0].page_end == "IX-4"


def test_vector_search_refuses_a_different_embedding_model(indexed):
    _, chroma, _ = indexed
    with pytest.raises(EmbeddingModelMismatch):
        vector_search(chroma, HashingEmbedder(model_name="another-model"), "anything")


def test_reindexing_the_same_files_is_a_no_op(indexed, pdf_path):
    conn, chroma, _ = indexed
    again = index_manual(conn, chroma, {"09": pdf_path}, SPEC, HashingEmbedder(), read_pages=lambda _: FAKE_CHAPTER)
    assert again is None


def test_missing_vector_index_is_rebuilt(indexed, pdf_path):
    conn, chroma, _ = indexed
    chroma.delete_collection("ncci_manual")
    again = index_manual(conn, chroma, {"09": pdf_path}, SPEC, HashingEmbedder(), read_pages=lambda _: FAKE_CHAPTER)
    assert again is not None
    assert chroma.get_collection("ncci_manual").count() == 2
    assert conn.execute("SELECT COUNT(*) FROM manual_chunks").fetchone()[0] == 2


def test_changed_embedding_model_triggers_rebuild(indexed, pdf_path):
    conn, chroma, _ = indexed
    other = HashingEmbedder(model_name="fake-hashing-64-v2")
    assert index_manual(conn, chroma, {"09": pdf_path}, SPEC, other, read_pages=lambda _: FAKE_CHAPTER) is not None
    assert vector_search(chroma, other, "ZZ001")  # the new model is now the one on record


def test_chroma_telemetry_is_disabled(chroma):
    assert chroma.get_settings().anonymized_telemetry is False
