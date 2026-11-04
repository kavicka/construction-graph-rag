"""Stavba grafu jedného dokumentu. Beží v ingest threade po embeddingu.

Idempotentné: najprv sa zmaže starý doc-viazaný podgraf.
"""

import json
import logging

from app import db
from app.graph import schema as gs
from app.graph.extract import (
    extract_concepts_llm,
    extract_quantities,
    extract_references,
    normalize_key,
)
from app.graph.resolve import resolve_same_as
from app.index.lancedb_store import document_chunks

logger = logging.getLogger(__name__)

TABLE_QUANTITY_LINES = 12  # hlavička + prvé riadky — reprezentatívne jednotky


def build_document_graph(doc_id: str) -> dict[str, int]:
    db.delete_document_graph(doc_id)
    doc = db.get_document(doc_id)
    if doc is None:
        raise ValueError(f"dokument {doc_id} neexistuje")

    doc_node = gs.doc_node_id(doc_id)
    db.upsert_graph_node(doc_node, gs.DOCUMENT, doc["filename"], doc_id=doc_id)

    for p in db.get_pages(doc_id):
        pid = gs.page_node_id(doc_id, p["page_no"])
        db.upsert_graph_node(pid, gs.PAGE, f"str. {p['page_no']}", doc_id=doc_id)
        db.upsert_graph_edge(doc_node, pid, gs.CONTAINS)

    counts = {"chunks": 0, "concepts": 0, "quantities": 0, "references": 0, "same_as": 0}
    new_concept_ids: list[str] = []
    prev_chunk_node: str | None = None

    for row in document_chunks(doc_id):
        cid = gs.chunk_node_id(row["chunk_id"])
        label = row["section_path"] or row["raw_text"][:60]
        props = json.dumps(
            {
                "snippet": row["raw_text"][:200],
                "page_from": row["page_from"],
                "page_to": row["page_to"],
                "section_path": row["section_path"],
                "chunk_type": row["type"],
            },
            ensure_ascii=False,
        )
        db.upsert_graph_node(cid, gs.CHUNK, label, doc_id=doc_id, props_json=props)
        db.upsert_graph_edge(gs.page_node_id(doc_id, row["page_from"]), cid, gs.CONTAINS)
        if prev_chunk_node is not None:
            db.upsert_graph_edge(prev_chunk_node, cid, gs.NEXT)
        prev_chunk_node = cid
        counts["chunks"] += 1

        if row["type"] != "text":
            continue
        text = row["raw_text"]
        chunk_ref = row["chunk_id"]

        for key, qlabel, unit in extract_quantities(text):
            qid = gs.quantity_node_id(key)
            db.upsert_graph_node(
                qid,
                gs.QUANTITY,
                qlabel,
                norm_key=key,
                props_json=json.dumps({"unit": unit}),
            )
            db.upsert_graph_edge(cid, qid, gs.MEASURES, evidence_chunk_id=chunk_ref)
            counts["quantities"] += 1

        for key, rlabel, rtype in extract_references(text):
            rid = gs.reference_node_id(key)
            db.upsert_graph_node(
                rid,
                gs.REFERENCE,
                rlabel,
                norm_key=key,
                props_json=json.dumps({"ref_type": rtype}),
            )
            db.upsert_graph_edge(doc_node, rid, gs.CITES, evidence_chunk_id=chunk_ref)
            counts["references"] += 1

        for concept in extract_concepts_llm(text):
            key = normalize_key(concept.name)
            if not key:
                continue
            con_id = gs.concept_node_id(key)
            db.upsert_graph_node(
                con_id,
                gs.CONCEPT,
                concept.name,
                norm_key=key,
                props_json=json.dumps({"concept_type": concept.type}, ensure_ascii=False),
            )
            edge_type = gs.DEFINES if concept.defined_here else gs.MENTIONS
            db.upsert_graph_edge(cid, con_id, edge_type, evidence_chunk_id=chunk_ref)
            new_concept_ids.append(con_id)
            counts["concepts"] += 1

    # tabuľky: uzol + jednotky z hlavičky a prvých riadkov
    for t in db.list_tables(doc_id):
        tid = gs.table_node_id(t["id"])
        db.upsert_graph_node(
            tid,
            gs.TABLE,
            t["caption"] or f"Tabuľka {t['n_rows']}×{t['n_cols']}",
            doc_id=doc_id,
            props_json=json.dumps({"page_no": t["page_no"], "table_id": t["id"]}),
        )
        db.upsert_graph_edge(doc_node, tid, gs.HAS_TABLE)
        from pathlib import Path

        md_path = Path(t["markdown_path"])
        if md_path.exists():
            lines = md_path.read_text(encoding="utf-8").splitlines()
            head = "\n".join(lines[:TABLE_QUANTITY_LINES])
            for key, qlabel, unit in extract_quantities(head):
                qid = gs.quantity_node_id(key)
                db.upsert_graph_node(
                    qid,
                    gs.QUANTITY,
                    qlabel,
                    norm_key=key,
                    props_json=json.dumps({"unit": unit}),
                )
                db.upsert_graph_edge(tid, qid, gs.MEASURES)
                counts["quantities"] += 1

    counts["same_as"] = resolve_same_as(sorted(set(new_concept_ids)))
    logger.info("graf doc %s: %s", doc_id, counts)
    return counts
