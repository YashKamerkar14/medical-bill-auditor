"""Index NCCI Policy Manual chapters for keyword (SQLite FTS5) and vector (Chroma) search.

Write order matters. Embeddings are computed first (slowest, most likely to
fail), then the Chroma collection is replaced, then SQLite is written in one
transaction that records the release last. If anything fails part-way, the
release is not recorded, so the next build redoes everything instead of
trusting a half-built index.
"""

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from bill_auditor.ingest.chunk_manual import Chunk, Section, parse_chapter, read_pdf_pages
from bill_auditor.ingest.releases import ReleaseSpec, is_already_loaded, record_file, source_file, start_release
from bill_auditor.retrieval.embeddings import Embedder

COLLECTION = "ncci_manual"


@dataclass(frozen=True)
class ManualIndexSummary:
    sections: int
    chunks: int


def open_chroma(chroma_dir: Path):
    """Persistent local Chroma client with anonymous telemetry switched off."""
    import chromadb
    from chromadb.config import Settings

    return chromadb.PersistentClient(path=str(chroma_dir), settings=Settings(anonymized_telemetry=False))


def embedding_text(section: Section, chunk: Chunk) -> str:
    """Contextual chunk header: what gets embedded, not what gets quoted.

    A chunk from the middle of a section often never names its topic; prefixing
    the chapter and section title puts it back. The stored text stays verbatim.
    """
    return f"{section.chapter_title}. Section {section.section_id}: {section.title}\n\n{chunk.text}"


def _collection_matches(chroma_client, spec: ReleaseSpec, embedder: Embedder, expected_chunks: int) -> bool:
    try:
        collection = chroma_client.get_collection(COLLECTION)
    except Exception:  # Chroma raises different errors for "not found" across versions
        return False
    metadata = collection.metadata or {}
    return (
        metadata.get("release_label") == spec.label
        and metadata.get("embedding_model") == embedder.model_name
        and collection.count() == expected_chunks
    )


def index_manual(
    conn: sqlite3.Connection,
    chroma_client,
    pdf_paths: dict[str, Path],
    spec: ReleaseSpec,
    embedder: Embedder,
    read_pages: Callable[[Path], list[list[str]]] = read_pdf_pages,
) -> ManualIndexSummary | None:
    """Index the given chapter PDFs as one release; None if already indexed from the same files."""
    files = [source_file(part, path) for part, path in pdf_paths.items()]
    if is_already_loaded(conn, spec, files):
        stored = conn.execute("SELECT COUNT(*) FROM manual_chunks").fetchone()[0]
        if _collection_matches(chroma_client, spec, embedder, stored):
            return None

    chapters = [(file, *parse_chapter(read_pages(file.path))) for file in files]
    sections = {s.section_id: s for _, chapter_sections, _ in chapters for s in chapter_sections}
    chunks = [chunk for _, _, chapter_chunks in chapters for chunk in chapter_chunks]
    if len(sections) != sum(len(s) for _, s, _ in chapters):
        raise ValueError("Two chapters produced the same section id")

    # 1. Embeddings.
    vectors = embedder.embed_documents([embedding_text(sections[c.section_id], c) for c in chunks])

    # 2. Replace the Chroma collection.
    try:
        chroma_client.delete_collection(COLLECTION)
    except Exception:
        pass  # did not exist yet
    collection = chroma_client.create_collection(
        COLLECTION,
        configuration={"hnsw": {"space": "cosine"}},
        metadata={"release_label": spec.label, "embedding_model": embedder.model_name},
        embedding_function=None,  # we always pass vectors ourselves
    )
    collection.add(
        ids=[c.chunk_id for c in chunks],
        embeddings=vectors,
        documents=[c.text for c in chunks],
        metadatas=[
            {
                "section_id": c.section_id,
                "section_title": sections[c.section_id].title,
                "page_start": c.page_start,
                "page_end": c.page_end,
            }
            for c in chunks
        ],
    )

    # 3. SQLite: sections, chunks, keyword index and the release record.
    with conn:
        release_id = start_release(conn, spec)
        conn.executemany(
            """INSERT INTO manual_sections
               (section_id, release_id, chapter, chapter_title, title, page_start, page_end, text)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (s.section_id, release_id, s.chapter, s.chapter_title, s.title, s.page_start, s.page_end, s.text)
                for s in sections.values()
            ],
        )
        conn.executemany(
            """INSERT INTO manual_chunks
               (chunk_id, section_id, section_title, ordinal, page_start, page_end, text)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            [
                (c.chunk_id, c.section_id, sections[c.section_id].title, c.ordinal, c.page_start, c.page_end, c.text)
                for c in chunks
            ],
        )
        conn.execute("INSERT INTO manual_fts (manual_fts) VALUES ('rebuild')")
        for file, _, chapter_chunks in chapters:
            record_file(conn, release_id, file, len(chapter_chunks))

    return ManualIndexSummary(sections=len(sections), chunks=len(chunks))
