"""Graf API pre vizualizáciu (react-force-graph) a inšpekciu uzlov."""

import json
from typing import Any

import networkx as nx
from fastapi import APIRouter, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from app import db

router = APIRouter()

DEFAULT_NODE_LIMIT = 300


class GraphNode(BaseModel):
    id: str
    type: str
    label: str
    doc_id: str | None
    degree: int


class GraphLink(BaseModel):
    source: str
    target: str
    type: str
    weight: float


class GraphOut(BaseModel):
    nodes: list[GraphNode]
    links: list[GraphLink]
    total_nodes: int


class EvidenceOut(BaseModel):
    edge_type: str
    doc_id: str | None
    filename: str | None
    page_from: int | None
    snippet: str | None


class NodeDetail(BaseModel):
    id: str
    type: str
    label: str
    props: dict[str, Any]
    neighbors: list[GraphNode]
    evidence: list[EvidenceOut]


def _build_nx() -> nx.MultiDiGraph:
    g = nx.MultiDiGraph()
    for n in db.list_graph_nodes():
        g.add_node(n["id"], **n)
    for e in db.list_graph_edges():
        if g.has_node(e["src"]) and g.has_node(e["dst"]):
            g.add_edge(e["src"], e["dst"], type=e["type"], weight=e["weight"])
    return g


def _graph_payload(
    types: list[str] | None, doc_id: str | None, limit: int
) -> GraphOut:
    g = _build_nx()

    if doc_id:
        doc_nodes = {n for n, d in g.nodes(data=True) if d.get("doc_id") == doc_id}
        # + zdieľané uzly napojené na dokument (koncepty, veličiny, referencie)
        shared = {
            nbr
            for n in doc_nodes
            for nbr in list(g.successors(n)) + list(g.predecessors(n))
            if g.nodes[nbr].get("doc_id") is None
        }
        keep = doc_nodes | shared
        g = g.subgraph(keep).copy()

    if types:
        allowed = {n for n, d in g.nodes(data=True) if d.get("type") in types}
        g = g.subgraph(allowed).copy()

    total = g.number_of_nodes()
    degree = dict(g.degree())
    top = sorted(g.nodes, key=lambda n: degree.get(n, 0), reverse=True)[:limit]
    sub = g.subgraph(top)

    nodes = [
        GraphNode(
            id=n,
            type=sub.nodes[n]["type"],
            label=sub.nodes[n]["label"],
            doc_id=sub.nodes[n].get("doc_id"),
            degree=degree.get(n, 0),
        )
        for n in sub.nodes
    ]
    links = [
        GraphLink(source=u, target=v, type=d["type"], weight=d.get("weight", 1.0))
        for u, v, d in sub.edges(data=True)
    ]
    return GraphOut(nodes=nodes, links=links, total_nodes=total)


@router.get("/graph")
async def get_graph(
    types: str | None = Query(default=None, description="csv typov uzlov"),
    doc_id: str | None = None,
    limit: int = Query(default=DEFAULT_NODE_LIMIT, le=2000),
) -> GraphOut:
    type_list = [t.strip() for t in types.split(",") if t.strip()] if types else None
    return await run_in_threadpool(_graph_payload, type_list, doc_id, limit)


def _node_detail(node_id: str) -> NodeDetail | None:
    node = db.get_graph_node(node_id)
    if node is None:
        return None

    edges = db.node_edges(node_id)
    neighbor_ids = {e["dst"] if e["src"] == node_id else e["src"] for e in edges}
    neighbors = []
    for nid in sorted(neighbor_ids):
        n = db.get_graph_node(nid)
        if n:
            neighbors.append(
                GraphNode(id=n["id"], type=n["type"], label=n["label"],
                          doc_id=n["doc_id"], degree=0)
            )

    evidence: list[EvidenceOut] = []
    for e in edges:
        if not e["evidence_chunk_id"]:
            continue
        chunk_node = db.get_graph_node(f"chunk:{e['evidence_chunk_id']}")
        has_props = chunk_node is not None and chunk_node["props_json"]
        props = json.loads(chunk_node["props_json"]) if has_props else {}  # type: ignore[index]
        chunk_doc_id = chunk_node["doc_id"] if chunk_node else None
        doc = db.get_document(chunk_doc_id) if chunk_doc_id else None
        evidence.append(
            EvidenceOut(
                edge_type=e["type"],
                doc_id=chunk_doc_id,
                filename=doc["filename"] if doc else None,
                page_from=props.get("page_from"),
                snippet=props.get("snippet"),
            )
        )

    return NodeDetail(
        id=node["id"],
        type=node["type"],
        label=node["label"],
        props=json.loads(node["props_json"]) if node["props_json"] else {},
        neighbors=neighbors,
        evidence=evidence,
    )


@router.get("/graph/node/{node_id:path}")
async def get_node(node_id: str) -> NodeDetail:
    detail = await run_in_threadpool(_node_detail, node_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="uzol neexistuje")
    return detail
