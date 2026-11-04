"""Entity resolution — SAME_AS edges between concepts across documents.

1. Exact norm_key match → the same node (handled already when building the ID).
2. rapidfuzz over norm_key ≥ FUZZY_THRESHOLD → SAME_AS (typos, declension).
3. Embedding cosine of labels ≥ EMB_THRESHOLD → cross-lingual SAME_AS (SK ↔ EN
   works thanks to the multilingual space).

Key rule (empirically verified on a sample corpus): SAME_AS is created ONLY
between concepts from DISJOINT sets of documents. Within a single document
SAME_AS is pointless (the document is already linked by its structure) and co-mentioned terms
(„zariadenie" ↔ „materiál") give falsely high similarities. The 0.70 threshold is
calibrated on label-only embeddings: a true SK↔EN pair ~0.75, false cross-doc
pairs < 0.6.
"""

import json
import logging

from rapidfuzz import fuzz

from app import db
from app.graph.schema import CONCEPT, DEFINES, MENTIONS, SAME_AS
from app.index.embedder import get_embedder

logger = logging.getLogger(__name__)

FUZZY_THRESHOLD = 90.0
EMB_THRESHOLD = 0.70
MAX_EMB_CANDIDATES = 400  # guard for a large graph


def _cosine(a: list[float], b: list[float]) -> float:
    num = sum(x * y for x, y in zip(a, b, strict=True))
    da = sum(x * x for x in a) ** 0.5
    db_ = sum(y * y for y in b) ** 0.5
    return num / (da * db_) if da and db_ else 0.0


def _concept_doc_sets() -> dict[str, set[str]]:
    """concept_id → set of doc_ids where it has evidence (via MENTIONS/DEFINES)."""
    docs: dict[str, set[str]] = {}
    for e in db.list_graph_edges():
        if e["type"] in (MENTIONS, DEFINES) and e["evidence_chunk_id"]:
            doc_id = e["evidence_chunk_id"].split(":", 1)[0]
            docs.setdefault(e["dst"], set()).add(doc_id)
    return docs


def resolve_same_as(new_node_ids: list[str]) -> int:
    """Compare new concepts with existing ones, write SAME_AS edges. Returns the count."""
    all_concepts = [n for n in db.concept_nodes() if n["type"] == CONCEPT]
    new_set = set(new_node_ids)
    new_nodes = [n for n in all_concepts if n["id"] in new_set]
    old_nodes = [n for n in all_concepts if n["id"] not in new_set]
    if not new_nodes or not old_nodes:
        return 0

    doc_sets = _concept_doc_sets()

    def disjoint(a: str, b: str) -> bool:
        return not (doc_sets.get(a, set()) & doc_sets.get(b, set()))

    created = 0
    matched: set[tuple[str, str]] = set()

    for nn in new_nodes:
        for on in old_nodes:
            if not disjoint(nn["id"], on["id"]):
                continue
            score = fuzz.token_sort_ratio(nn["norm_key"], on["norm_key"])
            if score >= FUZZY_THRESHOLD:
                pair = (min(nn["id"], on["id"]), max(nn["id"], on["id"]))
                if pair in matched:
                    continue
                matched.add(pair)
                db.upsert_graph_edge(
                    pair[0],
                    pair[1],
                    SAME_AS,
                    weight=score / 100.0,
                    props_json=json.dumps({"method": "fuzzy"}),
                )
                created += 1

    if len(all_concepts) <= MAX_EMB_CANDIDATES:
        embedder = get_embedder()
        new_vecs = embedder.embed_passages([n["label"] for n in new_nodes])
        old_vecs = embedder.embed_passages([n["label"] for n in old_nodes])
        for nn, nv in zip(new_nodes, new_vecs, strict=True):
            for on, ov in zip(old_nodes, old_vecs, strict=True):
                if not disjoint(nn["id"], on["id"]):
                    continue
                pair = (min(nn["id"], on["id"]), max(nn["id"], on["id"]))
                if pair in matched:
                    continue
                sim = _cosine(nv, ov)
                if sim >= EMB_THRESHOLD:
                    matched.add(pair)
                    db.upsert_graph_edge(
                        pair[0],
                        pair[1],
                        SAME_AS,
                        weight=sim,
                        props_json=json.dumps({"method": "embedding"}),
                    )
                    created += 1
    else:
        logger.info(
            "resolve: %s konceptov nad limitom — embedding krok preskočený", len(all_concepts)
        )
    return created
