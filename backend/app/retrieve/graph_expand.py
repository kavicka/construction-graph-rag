"""Graph expansion of retrieval.

Seed chunks → their entities (MENTIONS/DEFINES/MEASURES) → SAME_AS cluster
→ chunks that support those entities elsewhere (typically another document/page).

Score: decay by seed rank × edge weight × hub penalty
(a concept mentioned everywhere carries no information about a specific link).
"""

import logging
import math
import re
from typing import Any

from rapidfuzz import fuzz

from app import db
from app.graph.schema import CONCEPT, DEFINES, MEASURES, MENTIONS, SAME_AS
from app.index.lancedb_store import get_chunks_by_ids

logger = logging.getLogger(__name__)

ENTITY_EDGE_TYPES = (MENTIONS, DEFINES, MEASURES)
RELATIONAL_HINTS = re.compile(
    r"súvis|vzťah|vztah|prepojen|spoločn|spolocn|medzi|porovna|"
    r"relation|between|common|connect|compare|link",
    re.IGNORECASE,
)


def is_relational_query(query: str) -> bool:
    return bool(RELATIONAL_HINTS.search(query))


def _edges_index() -> tuple[dict[str, list[dict[str, Any]]], dict[str, int]]:
    """→ (edges by node, degree of entity nodes)"""
    by_node: dict[str, list[dict[str, Any]]] = {}
    degree: dict[str, int] = {}
    for e in db.list_graph_edges():
        by_node.setdefault(e["src"], []).append(e)
        by_node.setdefault(e["dst"], []).append(e)
        for node in (e["src"], e["dst"]):
            degree[node] = degree.get(node, 0) + 1
    return by_node, degree


def _hub_penalty(degree: int) -> float:
    return 1.0 / math.log2(2 + degree)


def expand_via_graph(seed_chunk_ids: list[str], *, cap: int = 10) -> list[dict[str, Any]]:
    """Returns lancedb rows of expanded chunks, ordered by graph score."""
    if not seed_chunk_ids:
        return []
    by_node, degree = _edges_index()
    seed_nodes = {f"chunk:{cid}" for cid in seed_chunk_ids}

    scores: dict[str, float] = {}
    for rank, cid in enumerate(seed_chunk_ids):
        seed_node = f"chunk:{cid}"
        seed_decay = 1.0 / (1 + rank)
        # seed entities
        entities: dict[str, float] = {}
        for e in by_node.get(seed_node, []):
            if e["type"] in ENTITY_EDGE_TYPES and e["src"] == seed_node:
                entities[e["dst"]] = max(entities.get(e["dst"], 0), e["weight"])
        # SAME_AS klaster (1 hop)
        for ent, w in list(entities.items()):
            for e in by_node.get(ent, []):
                if e["type"] == SAME_AS:
                    other = e["dst"] if e["src"] == ent else e["src"]
                    entities[other] = max(entities.get(other, 0), w * e["weight"])
        # chunks that support these entities
        for ent, ent_w in entities.items():
            penalty = _hub_penalty(degree.get(ent, 0))
            for e in by_node.get(ent, []):
                if e["type"] in ENTITY_EDGE_TYPES and e["dst"] == ent:
                    target = e["src"]
                    if target in seed_nodes or not target.startswith("chunk:"):
                        continue
                    target_cid = target.removeprefix("chunk:")
                    add = seed_decay * ent_w * e["weight"] * penalty
                    scores[target_cid] = scores.get(target_cid, 0) + add

    top_ids = [cid for cid, _ in sorted(scores.items(), key=lambda kv: kv[1], reverse=True)][:cap]
    rows = {r["chunk_id"]: r for r in get_chunks_by_ids(top_ids)}
    return [{**rows[cid], "graph_score": scores[cid]} for cid in top_ids if cid in rows]


def match_query_concepts(query: str, *, threshold: float = 85.0) -> list[str]:
    """Find graph concepts mentioned directly in the question (graph-first mode)."""
    matched: list[str] = []
    for node in db.list_graph_nodes(types=[CONCEPT]):
        score = fuzz.partial_ratio(node["label"].casefold(), query.casefold())
        if score >= threshold:
            matched.append(node["id"])
    return matched


def concept_evidence_chunks(concept_ids: list[str], *, cap: int = 8) -> list[dict[str, Any]]:
    """Chunks supporting the given concepts (including the SAME_AS cluster) — across documents."""
    if not concept_ids:
        return []
    by_node, degree = _edges_index()

    cluster = set(concept_ids)
    for con in concept_ids:
        for e in by_node.get(con, []):
            if e["type"] == SAME_AS:
                cluster.add(e["dst"] if e["src"] == con else e["src"])

    chunk_scores: dict[str, float] = {}
    for ent in cluster:
        penalty = _hub_penalty(degree.get(ent, 0))
        for e in by_node.get(ent, []):
            if e["type"] in ENTITY_EDGE_TYPES and e["dst"] == ent and e["src"].startswith("chunk:"):
                cid = e["src"].removeprefix("chunk:")
                chunk_scores[cid] = chunk_scores.get(cid, 0) + e["weight"] * penalty

    top_ids = [c for c, _ in sorted(chunk_scores.items(), key=lambda kv: kv[1], reverse=True)][:cap]
    rows = {r["chunk_id"]: r for r in get_chunks_by_ids(top_ids)}
    return [{**rows[cid], "graph_score": chunk_scores[cid]} for cid in top_ids if cid in rows]
