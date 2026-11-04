"""Témy celej knižnice — komunity konceptov v grafe (LazyGraphRAG prístup).

Koncepty, ktoré sa vyskytujú v rovnakom úseku textu (alebo sú SAME_AS), tvoria graf;
Louvain ho rozdelí na komunity = témy naprieč dokumentmi. Na rozdiel od klasického
GraphRAG sa súhrny NErobia pri ingeste pre všetky komunity (drahé), ale až keď ich otázka
potrebuje — a cachujú sa podľa zloženia komunity.

Použitie: otázky na celý korpus („aké sú hlavné témy dokumentov?“), na ktoré top-k
retrieval nestačí, lebo odpoveď nie je v žiadnom jednom úseku.
"""

import hashlib
import logging
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from itertools import combinations
from typing import Any

import networkx as nx
from pydantic import BaseModel, Field, ValidationError

from app import db
from app.auth import allowed_doc_ids
from app.config import get_settings
from app.graph.schema import CONCEPT, DEFINES, MENTIONS, SAME_AS
from app.index.embedder import get_embedder
from app.index.lancedb_store import get_chunks_by_ids
from app.llm.ollama_client import OllamaError, generate_sync
from app.retrieve.injection import guard_source_text
from app.retrieve.secrets import redact_secrets

logger = logging.getLogger(__name__)

MIN_COMMUNITY_CONCEPTS = 3
# koncept vo väčšine dokumentov („zariadenie“, „Cena“) nesie málo informácie a zlepí
# nesúvisiace témy do jednej komunity — rovnaká logika ako IDF
HUB_DOC_SHARE = 0.35
# typy z extrakcie, ktoré malý model občas vráti ako „koncept“ — nenesú tému
GENERIC_LABELS = {
    "zariadenie",
    "zariadenia",
    "metoda",
    "metody",
    "material",
    "materialy",
    "koncept",
    "device",
    "devices",
    "method",
    "methods",
    "materials",
    "concept",
    "concepts",
}
TOP_CONCEPTS = 10
REPRESENTATIVE_CHUNKS = 4
# „hlavné témy všetkých dokumentov“ je podobné každej téme rovnako — rozhodne pokrytie
COVERAGE_WEIGHT = 0.15
GLOBAL_QUERY = re.compile(
    r"hlavn\w* tém|hlavn\w* oblast|o čom (sú|je|hovoria)|prehľad (celej|všetk\w*|knižnic)|"
    r"všetk\w* dokument\w*|celej knižnic|celého korpus|kľúčov\w* tém|"
    r"main (themes|topics)|key (themes|topics)|what are (the|these|all) documents about|"
    r"overview of (all|the) (documents|library)|summari[sz]e (all|the whole|everything)",
    re.IGNORECASE,
)


@dataclass
class Community:
    key: str
    concepts: list[str]  # štítky, zoradené podľa centrality
    concept_ids: list[str]
    documents: dict[str, int]  # doc_id → počet úsekov
    chunk_ids: list[str]  # reprezentatívne úseky
    title: str | None = None
    summary: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def size(self) -> int:
        return len(self.concept_ids)


def is_global_query(question: str) -> bool:
    return bool(GLOBAL_QUERY.search(question))


def detect_communities() -> list[Community]:
    nodes = {
        n["id"]: n
        for n in db.list_graph_nodes(types=[CONCEPT])
        if (n["norm_key"] or "").replace("_", " ") not in GENERIC_LABELS
    }
    chunk_concepts: dict[str, set[str]] = defaultdict(set)
    same_as: list[tuple[str, str]] = []
    allowed = allowed_doc_ids()
    for e in db.list_graph_edges():
        if e["type"] in (MENTIONS, DEFINES) and e["dst"] in nodes and e["src"].startswith("chunk:"):
            chunk_id = e["src"].removeprefix("chunk:")
            if allowed is not None and chunk_id.split(":", 1)[0] not in allowed:
                continue  # témy len z dokumentov, ktoré používateľ smie vidieť
            chunk_concepts[chunk_id].add(e["dst"])
        elif e["type"] == SAME_AS and e["src"] in nodes and e["dst"] in nodes:
            same_as.append((e["src"], e["dst"]))

    if allowed is not None:
        # pojmy len z povolených dokumentov — inak by štítok tému cudzieho dokumentu prezradil
        visible = {c for concepts in chunk_concepts.values() for c in concepts}
        nodes = {k: v for k, v in nodes.items() if k in visible}
        same_as = [(a, b) for a, b in same_as if a in nodes and b in nodes]
    g = nx.Graph()
    g.add_nodes_from(nodes)
    for concepts in chunk_concepts.values():
        for a, b in combinations(sorted(concepts), 2):
            w = g[a][b]["weight"] + 1 if g.has_edge(a, b) else 1
            g.add_edge(a, b, weight=w)
    for a, b in same_as:
        g.add_edge(a, b, weight=2.0)
    concept_chunks: dict[str, set[str]] = defaultdict(set)
    for chunk_id, concepts in chunk_concepts.items():
        for c in concepts:
            concept_chunks[c].add(chunk_id)
    chunk_docs = {cid: cid.split(":", 1)[0] for cid in chunk_concepts}
    n_docs = max(1, len(set(chunk_docs.values())))
    hubs = [
        c
        for c, chunks in concept_chunks.items()
        if len({chunk_docs[x] for x in chunks}) > max(2, HUB_DOC_SHARE * n_docs)
    ]
    g.remove_nodes_from(hubs)
    g.remove_nodes_from([n for n in list(g.nodes) if g.degree(n) == 0])
    if g.number_of_nodes() == 0:
        return []

    communities: list[Community] = []
    for members in nx.community.louvain_communities(g, weight="weight", seed=42):
        if len(members) < MIN_COMMUNITY_CONCEPTS:
            continue
        sub = g.subgraph(members)
        ranked = sorted(members, key=lambda n: sub.degree(n, weight="weight"), reverse=True)
        chunk_score: Counter[str] = Counter()
        for c in members:
            for chunk_id in concept_chunks[c]:
                chunk_score[chunk_id] += 1
        documents: Counter[str] = Counter()
        for chunk_id in chunk_score:
            documents[chunk_docs[chunk_id]] += 1
        # kľúč cache = pojmy + dokumenty, z ktorých súhrn vznikol: rovnaká množina pojmov
        # u iného používateľa (iné povolené dokumenty) nesmie dostať cudzí súhrn
        scope_docs = "|".join(sorted(documents))
        key = hashlib.sha256(("|".join(sorted(members)) + "#" + scope_docs).encode()).hexdigest()[
            :12
        ]
        communities.append(
            Community(
                key=key,
                concepts=[nodes[c]["label"] for c in ranked[:TOP_CONCEPTS]],
                concept_ids=sorted(members),
                documents=dict(documents.most_common()),
                chunk_ids=[c for c, _ in chunk_score.most_common(REPRESENTATIVE_CHUNKS)],
            )
        )
    communities.sort(key=lambda c: (len(c.documents), c.size), reverse=True)
    for community in communities:
        cached = db.get_community_summary(community.key)
        if cached:
            community.title, community.summary = cached
    return communities


class _Summary(BaseModel):
    title: str = Field(min_length=2, max_length=80)
    summary: str = Field(min_length=10, max_length=900)


def summarize_community(community: Community) -> Community:
    """LLM súhrn témy (lenivo, cache podľa zloženia komunity)."""
    if community.summary:
        return community
    rows = get_chunks_by_ids(community.chunk_ids)
    names = {d["id"]: d["filename"] for d in db.list_documents()}
    excerpts = "\n\n".join(
        f"({names.get(r['doc_id'], r['doc_id'])}, str. {r['page_from']}): {r['raw_text'][:700]}"
        for r in rows
    )
    prompt = (
        "Tieto pojmy spolu súvisia naprieč firemnými dokumentmi. Pomenuj tému (title, "
        "2–6 slov) a v 2–3 vetách zhrň, čo o nej dokumenty hovoria (summary) — konkrétne, "
        "s názvami projektov a číslami z úryvkov. Píš po slovensky.\n\n"
        f"Pojmy: {', '.join(community.concepts)}\n\nÚryvky:\n{excerpts}"
    )
    try:
        raw = generate_sync(
            get_settings().answer_model,
            prompt,
            json_schema=_Summary.model_json_schema(),
            temperature=0.2,
            num_ctx=8192,
            timeout=180,
        )
        parsed = _Summary.model_validate_json(raw)
        community.title, community.summary = parsed.title.strip(), parsed.summary.strip()
    except (OllamaError, ValidationError) as exc:
        logger.warning("súhrn komunity %s zlyhal: %s", community.key, exc)
        community.title = ", ".join(community.concepts[:3])
        community.summary = "Pojmy: " + ", ".join(community.concepts)
        return community  # fallback sa necachuje — skúsi sa nabudúce
    db.set_community_summary(community.key, community.title, community.summary)
    return community


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def rank_communities(question: str, communities: list[Community], top: int) -> list[Community]:
    """Bez súhrnov (lazy): poradie podľa podobnosti otázky so štítkami konceptov,
    vážené počtom pokrytých dokumentov — široké témy sú pre globálne otázky dôležitejšie."""
    if len(communities) <= top:
        return communities
    embedder = get_embedder()
    q = embedder.embed_query(question)
    vecs = embedder.embed_passages([", ".join(c.concepts) for c in communities])
    scored = [
        (_cosine(q, v) + COVERAGE_WEIGHT * math.log1p(len(c.documents)), c)
        for c, v in zip(communities, vecs, strict=True)
    ]
    return [c for _, c in sorted(scored, key=lambda sc: sc[0], reverse=True)[:top]]


GLOBAL_SYSTEM = """Odpovedáš na otázku o CELEJ knižnici dokumentov. Dostaneš témy (komunity
pojmov naprieč dokumentmi) so súhrnmi a ukážkové zdroje. Pravidlá:
- Štruktúruj odpoveď podľa tém (markdown, odrážky alebo ## nadpisy).
- Pri každej téme uveď, ktorých dokumentov sa týka, a citáciu [n] na ukážkový zdroj.
- Nevymýšľaj nič, čo nie je v súhrnoch ani zdrojoch. Píš v jazyku otázky."""


def global_context(question: str, top: list[Community]) -> tuple[list[Any], list[dict[str, str]]]:
    """Zdroje (1–2 reprezentatívne úseky na tému) + správy pre LLM."""
    from app.retrieve.pipeline import Source

    names = {d["id"]: d["filename"] for d in db.list_documents()}
    rows = {r["chunk_id"]: r for r in get_chunks_by_ids([c for t in top for c in t.chunk_ids])}
    boxes = db.get_chunk_boxes(list(rows))
    sources: list[Source] = []
    theme_lines: list[str] = []
    for theme in top:
        refs: list[int] = []
        for chunk_id in theme.chunk_ids[:2]:
            row = rows.get(chunk_id)
            if row is None:
                continue
            sources.append(
                Source(
                    n=len(sources) + 1,
                    chunk_id=chunk_id,
                    doc_id=row["doc_id"],
                    filename=names.get(row["doc_id"], row["doc_id"]),
                    page_from=row["page_from"],
                    page_to=row["page_to"],
                    type=row["type"],
                    table_id=row["table_id"],
                    text=guard_source_text(redact_secrets(row["raw_text"])[:1200])[0],
                    snippet=redact_secrets(row["raw_text"])[:200],
                    boxes=boxes.get(chunk_id, []),
                )
            )
            refs.append(len(sources))
        docs = ", ".join(names.get(d, d) for d in list(theme.documents)[:5])
        theme_lines.append(
            f"### {theme.title}\nSúhrn: {theme.summary}\nDokumenty: {docs}\n"
            f"Ukážkové zdroje: {', '.join(f'[{n}]' for n in refs) or '—'}"
        )
    blocks = "\n\n".join(f"[{s.n}] ({s.filename}, str. {s.page_from}):\n{s.text}" for s in sources)
    user = "Témy:\n\n" + "\n\n".join(theme_lines) + f"\n\nZdroje:\n{blocks}\n\nOtázka: {question}"
    messages = [{"role": "system", "content": GLOBAL_SYSTEM}, {"role": "user", "content": user}]
    return sources, messages
