"""Reciprocal Rank Fusion — combines rankings from multiple searches without tuning weights.

RRF score = Σ 1/(K + rank). Default instead of a neural reranker (zero RAM);
the reranker is opt-in for big machines (config, phase 5).
"""

from typing import Any

RRF_K = 60


def rrf_fuse(
    result_lists: list[list[dict[str, Any]]],
    *,
    key: str = "chunk_id",
    top_k: int = 8,
    weights: list[float] | None = None,
) -> list[dict[str, Any]]:
    """`weights` per list (default 1.0) — a weaker signal (graph) must not outrank dense/BM25."""
    scores: dict[str, float] = {}
    best_row: dict[str, dict[str, Any]] = {}
    for i, results in enumerate(result_lists):
        weight = weights[i] if weights else 1.0
        for rank, row in enumerate(results):
            item_id = row[key]
            scores[item_id] = scores.get(item_id, 0.0) + weight / (RRF_K + rank + 1)
            if item_id not in best_row:
                best_row[item_id] = row
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:top_k]
    return [{**best_row[item_id], "rrf_score": score} for item_id, score in ranked]
