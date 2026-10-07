"""Text embeddings behind a small interface.

Indexing and querying must use the same model, or the vectors live in
different spaces and search returns noise without any error. The model name is
stored with the Chroma collection and checked at query time.

Tests use a fake embedder that satisfies the same interface, so they run
without downloading a model.
"""

from pathlib import Path
from typing import Protocol

DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"

# The bge-v1.5 model card recommends this instruction for short retrieval
# queries (passages are embedded without it). fastembed does not add it, so we
# do, explicitly. Whether it helps on our queries is tested in Milestone 4.
BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


class Embedder(Protocol):
    model_name: str

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class FastEmbedEmbedder:
    """Local ONNX embeddings via fastembed; no API key, nothing leaves the machine."""

    def __init__(self, cache_dir: Path, model_name: str = DEFAULT_MODEL, query_prefix: str = BGE_QUERY_PREFIX):
        from fastembed import TextEmbedding  # heavy import, only when a real model is needed

        self.model_name = model_name
        self.query_prefix = query_prefix
        self._model = TextEmbedding(model_name=model_name, cache_dir=str(cache_dir))

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [vector.tolist() for vector in self._model.embed(texts)]

    def embed_query(self, text: str) -> list[float]:
        return next(iter(self._model.embed([self.query_prefix + text]))).tolist()
