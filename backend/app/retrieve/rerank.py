"""Cross-encoder reranking of fused candidates.

RRF merges rankings but does not see the question–text pair together; a cross-encoder does,
so it orders the top candidates more precisely (especially for similar documents of two projects).
Model by RAM profile, both multilingual (SK question over an EN document and vice versa):
  low  → cross-encoder/mmarco-mMiniLMv2-L12-H384-v1 (small, Apache-2.0)
  high → BAAI/bge-reranker-v2-m3 (pairs with bge-m3)
Models that fastembed knows run via ONNX; others via sentence-transformers (MPS).
"""

import logging
from functools import lru_cache
from typing import Any, Protocol

from app.config import get_settings

logger = logging.getLogger(__name__)

MAX_PASSAGE_CHARS = (
    1200  # ~300 tokens; the cross-encoder (max 512) would truncate longer text anyway
)


class Reranker(Protocol):
    model_name: str

    def score(self, query: str, passages: list[str]) -> list[float]: ...


class FastembedReranker:
    def __init__(self, model_name: str) -> None:
        from fastembed.rerank.cross_encoder import TextCrossEncoder

        self.model_name = model_name
        self._model = TextCrossEncoder(model_name=model_name)

    def score(self, query: str, passages: list[str]) -> list[float]:
        return [float(s) for s in self._model.rerank(query, passages, batch_size=16)]


class SentenceTransformersReranker:
    def __init__(self, model_name: str) -> None:
        import torch
        from sentence_transformers import CrossEncoder

        device = "mps" if torch.backends.mps.is_available() else "cpu"
        self.model_name = model_name
        self._model = CrossEncoder(model_name, device=device, max_length=384)
        if device == "mps":
            self._model.model.half()  # fp16 on Apple GPU — ~2× faster, same ranking

    def score(self, query: str, passages: list[str]) -> list[float]:
        scores = self._model.predict(
            [(query, p) for p in passages], batch_size=16, show_progress_bar=False
        )
        return [float(s) for s in scores]


def _fastembed_supports(model_name: str) -> bool:
    from fastembed.rerank.cross_encoder import TextCrossEncoder

    return any(m.get("model") == model_name for m in TextCrossEncoder.list_supported_models())


@lru_cache
def get_reranker() -> Reranker:
    settings = get_settings()
    model = settings.effective_reranker_model
    if _fastembed_supports(model):
        logger.info("reranker: fastembed %s", model)
        return FastembedReranker(model)
    logger.info("reranker: sentence-transformers %s", model)
    return SentenceTransformersReranker(model)


def rerank_rows(query: str, rows: list[dict[str, Any]], *, top_k: int) -> list[dict[str, Any]]:
    if len(rows) <= 1:
        return rows[:top_k]
    scores = get_reranker().score(query, [r["text"][:MAX_PASSAGE_CHARS] for r in rows])
    ranked = sorted(zip(rows, scores, strict=True), key=lambda rs: rs[1], reverse=True)
    return [{**row, "rerank_score": score} for row, score in ranked[:top_k]]
