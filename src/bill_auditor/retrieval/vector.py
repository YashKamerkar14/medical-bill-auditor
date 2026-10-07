"""Vector search over manual chunks with Chroma (cosine similarity).

Embeddings capture meaning, so "billing two codes that should be one" can find
the unbundling section even without shared words. They are weaker on exact
codes and numbers, which is why keyword search exists too.
"""

from bill_auditor.ingest.index_manual import COLLECTION
from bill_auditor.retrieval.embeddings import Embedder
from bill_auditor.retrieval.models import SearchHit


class EmbeddingModelMismatch(RuntimeError):
    """The query embedder is not the model the index was built with."""


def vector_search(chroma_client, embedder: Embedder, query: str, k: int = 5) -> list[SearchHit]:
    collection = chroma_client.get_collection(COLLECTION)
    metadata = collection.metadata or {}
    if metadata.get("embedding_model") != embedder.model_name:
        raise EmbeddingModelMismatch(
            f"Index built with {metadata.get('embedding_model')!r}, query embedder is {embedder.model_name!r}"
        )

    result = collection.query(
        query_embeddings=[embedder.embed_query(query)],
        n_results=k,
        include=["documents", "metadatas", "distances"],
    )
    rows = zip(result["ids"][0], result["documents"][0], result["metadatas"][0], result["distances"][0])
    # Chroma returns cosine distance (0 = identical); report similarity instead.
    return [
        SearchHit(
            chunk_id=chunk_id,
            section_id=meta["section_id"],
            section_title=meta["section_title"],
            page_start=meta["page_start"],
            page_end=meta["page_end"],
            text=text,
            mode="vector",
            rank=rank,
            score=1.0 - distance,
            release_label=metadata["release_label"],
        )
        for rank, (chunk_id, text, meta, distance) in enumerate(rows, start=1)
    ]
