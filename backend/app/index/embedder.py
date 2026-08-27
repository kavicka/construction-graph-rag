"""Embeddingy podľa RAM profilu.

low  → fastembed (ONNX, bez PyTorch v procese pri query) — multilingual-e5-small
high → sentence-transformers na MPS — BAAI/bge-m3 (kvalitnejší, väčší)

E5 modely vyžadujú prefixy "query: " / "passage: "; bge-m3 nie — rieši sa tu,
aplikačný kód o prefixoch nevie.
"""

import logging
from functools import lru_cache
from typing import Protocol

from app.config import get_settings

logger = logging.getLogger(__name__)


class Embedder(Protocol):
    model_name: str

    def embed_passages(self, texts: list[str]) -> list[list[float]]: ...
    def embed_query(self, text: str) -> list[float]: ...


def _needs_e5_prefix(model_name: str) -> bool:
    return "e5" in model_name.lower()


class FastembedEmbedder:
    def __init__(self, model_name: str) -> None:
        from fastembed import TextEmbedding

        self.model_name = model_name
        self._model = TextEmbedding(model_name=model_name)
        self._prefix = _needs_e5_prefix(model_name)

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        if self._prefix:
            texts = [f"passage: {t}" for t in texts]
        return [v.tolist() for v in self._model.embed(texts, batch_size=16)]

    def embed_query(self, text: str) -> list[float]:
        if self._prefix:
            text = f"query: {text}"
        return list(next(iter(self._model.embed([text]))).tolist())


class SentenceTransformersEmbedder:
    def __init__(self, model_name: str) -> None:
        import torch
        from sentence_transformers import SentenceTransformer

        device = "mps" if torch.backends.mps.is_available() else "cpu"
        self.model_name = model_name
        self._model = SentenceTransformer(model_name, device=device)
        self._prefix = _needs_e5_prefix(model_name)

    def _encode(self, texts: list[str]) -> list[list[float]]:
        vecs = self._model.encode(
            texts, batch_size=32, normalize_embeddings=True, show_progress_bar=False
        )
        return [v.tolist() for v in vecs]

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        if self._prefix:
            texts = [f"passage: {t}" for t in texts]
        return self._encode(texts)

    def embed_query(self, text: str) -> list[float]:
        if self._prefix:
            text = f"query: {text}"
        return self._encode([text])[0]


def _fastembed_supports(model_name: str) -> bool:
    from fastembed import TextEmbedding

    names: set[str] = set()
    for m in TextEmbedding.list_supported_models():
        name = m.get("model") if isinstance(m, dict) else getattr(m, "model", None)
        if isinstance(name, str):
            names.add(name)
    return model_name in names


@lru_cache
def get_embedder() -> Embedder:
    settings = get_settings()
    model_name = settings.effective_embedding_model

    if settings.effective_profile == "low" and _fastembed_supports(model_name):
        logger.info("embedder: fastembed %s", model_name)
        return FastembedEmbedder(model_name)

    if settings.effective_profile == "low":
        logger.warning(
            "fastembed nepodporuje %s — padám na sentence-transformers (viac RAM)", model_name
        )
    else:
        logger.info("embedder: sentence-transformers %s (MPS ak dostupné)", model_name)
    return SentenceTransformersEmbedder(model_name)
