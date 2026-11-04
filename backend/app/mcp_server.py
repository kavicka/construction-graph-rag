"""MCP server — the document library as tools for Claude Desktop / Claude Code / Cursor.

Transports:
  stdio            uv run --directory backend python -m app.mcp_server
  streamable HTTP  http://localhost:8000/mcp (runs together with the API, app.main)

Tools return structured data with citable sources (file + page), so
the client can answer with references just like the built-in chat.
"""

from typing import Any

import anyio
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp_types import ToolAnnotations

from app import db
from app.auth import LOCAL_ADMIN, allowed_doc_ids, can_access_document, set_scope
from app.config import get_settings
from app.docgen.intent import detect_generate_intent
from app.graph.schema import CONCEPT
from app.index.lancedb_store import document_chunks
from app.retrieve.graph_expand import (
    concept_evidence_chunks,
    expand_via_graph,
    match_query_concepts,
)
from app.retrieve.orchestrator import answer_question
from app.retrieve.pipeline import Source, rank_chunks, retrieve_sources
from app.retrieve.secrets import redact_secrets

INSTRUCTIONS = """Local document library (PDF, DOCX, PPTX, HTML, Markdown: contracts,
budgets, reports, manuals) with a knowledge graph across documents.
- `ask`: a complete cited answer to a question.
- `research`: broad questions that need a structured, cited report.
- `search_documents`: raw passages for your own reasoning.
- `find_connections`: which documents mention the same project, company, person or concept.
- `generate_document`: a branded PDF (report, offer, memo).
Cite sources as (filename, page)."""

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)

mcp = MCPServer(name="rag", title="Local document RAG", instructions=INSTRUCTIONS)


def _source_out(s: Source, *, with_text: bool = True) -> dict[str, Any]:
    out: dict[str, Any] = {
        "n": s.n,
        "filename": s.filename,
        "doc_id": s.doc_id,
        "page_from": s.page_from,
        "page_to": s.page_to,
        "type": s.type,
    }
    if with_text:
        out["text"] = s.text
    else:
        out["snippet"] = s.snippet
    return out


def _filenames() -> dict[str, str]:
    return {d["id"]: d["filename"] for d in db.list_documents()}


@mcp.tool(annotations=READ_ONLY)
async def list_documents() -> list[dict[str, Any]]:
    """List indexed documents with their processing status and page count."""
    docs = await anyio.to_thread.run_sync(db.list_documents)
    allowed = await anyio.to_thread.run_sync(allowed_doc_ids)
    docs = [d for d in docs if allowed is None or d["id"] in allowed]
    return [
        {
            "id": d["id"],
            "filename": d["filename"],
            "status": d["status"],
            "pages": d["pages_count"],
            "uploaded_at": d["created_at"],
        }
        for d in docs
    ]


@mcp.tool(annotations=READ_ONLY)
async def search_documents(query: str, k: int = 8) -> list[dict[str, Any]]:
    """Hybrid search (dense + BM25 + graph, reranked) over all documents.

    Returns the best passages with filename and pages. Tables come with their full
    markdown, so exact numbers can be quoted.
    """
    k = max(1, min(k, 20))
    sources = await anyio.to_thread.run_sync(lambda: retrieve_sources(query, k))
    return [_source_out(s) for s in sources]


def _connections(topic: str, max_documents: int) -> dict[str, Any]:
    names = _filenames()
    concept_ids = match_query_concepts(topic)
    if concept_ids:
        rows = concept_evidence_chunks(concept_ids, cap=40)
    else:
        # the topic is not a named concept → seed from search and expand via the graph
        seeds = rank_chunks(topic, k=6)
        rows = seeds + expand_via_graph([r["chunk_id"] for r in seeds], cap=30)

    allowed = allowed_doc_ids()
    if allowed is not None:
        rows = [r for r in rows if r["doc_id"] in allowed]
    by_doc: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    for r in rows:
        if r["chunk_id"] in seen:
            continue
        seen.add(r["chunk_id"])
        doc = by_doc.setdefault(
            r["doc_id"],
            {
                "filename": names.get(r["doc_id"], r["doc_id"]),
                "doc_id": r["doc_id"],
                "evidence": [],
            },
        )
        if len(doc["evidence"]) < 3:
            doc["evidence"].append(
                {
                    "page": r["page_from"],
                    "section": r["section_path"],
                    "snippet": redact_secrets(r["raw_text"])[:300],
                }
            )
    # only concepts backed by an allowed passage — otherwise the label would leak the
    # content of another document
    visible_chunks = {f"chunk:{r['chunk_id']}" for r in rows}
    evidenced = {
        e["dst"]
        for e in db.list_graph_edges()
        if e["src"] in visible_chunks and e["dst"] in set(concept_ids)
    }
    concepts = [n["label"] for n in db.list_graph_nodes(types=[CONCEPT]) if n["id"] in evidenced]
    return {
        "topic": topic,
        "matched_concepts": concepts,
        "documents": list(by_doc.values())[:max_documents],
    }


@mcp.tool(annotations=READ_ONLY)
async def find_connections(topic: str, max_documents: int = 10) -> dict[str, Any]:
    """Find every document that mentions a project, company, person or concept.

    Uses the knowledge graph built at ingest (concepts linked across documents, also
    across languages), so it surfaces links that keyword search would miss — e.g. a
    subcontractor named in a contract, in the supplier list and in warranty claims.
    """
    return await anyio.to_thread.run_sync(_connections, topic, max(1, min(max_documents, 30)))


@mcp.tool()
async def ask(question: str) -> dict[str, Any]:
    """Answer a question from the documents with numbered citations [n].

    Routes automatically: retrieval + LLM answer, exact SQL filter over table records,
    or a planning agent for combinatorial questions. Numbers in the answer are checked
    against the sources (`unverified_numbers` lists any that are not).
    """
    result = await answer_question(question)
    return {
        "answer": result.answer,
        "mode": result.mode,
        "sources": [_source_out(s, with_text=False) for s in result.sources],
        "unverified_numbers": result.verify_issues,
        "weak_sentences": [x["text"] for x in result.sentences if x["status"] != "supported"],
        "document": result.document,
        "trace_id": result.trace_id,
    }


@mcp.tool()
async def research(question: str) -> dict[str, Any]:
    """Deep research: plans 3–5 sub-questions, retrieves for each, writes a structured
    cited report (summary, findings, cross-document links, open questions). Slower than
    `ask` (~1 min locally) — use it for broad questions spanning many documents."""
    result = await answer_question(question, mode="research")
    return {
        "report": result.answer,
        "plan": [s["summary"] for s in result.steps if s["tool"] == "subquestion"],
        "sources": [_source_out(s, with_text=False) for s in result.sources],
        "weak_sentences": [x["text"] for x in result.sentences if x["status"] != "supported"],
        "trace_id": result.trace_id,
    }


def _document(document: str, max_chars: int) -> dict[str, Any]:
    doc = db.get_document(document)
    if doc is None:
        matches = [d for d in db.list_documents() if d["filename"] == document]
        doc = matches[0] if matches else None
    if doc is None or not can_access_document(doc["id"]):
        raise ValueError(f"document {document!r} not found — use list_documents")
    parts: list[str] = []
    for chunk in document_chunks(doc["id"]):
        if chunk["type"] != "text":
            continue
        parts.append(f"[page {chunk['page_from']}] {chunk['raw_text']}")
    text = redact_secrets("\n\n".join(parts))
    return {
        "id": doc["id"],
        "filename": doc["filename"],
        "pages": doc["pages_count"],
        "tables": [
            {
                "caption": t["caption"],
                "page": t["page_no"],
                "rows": t["n_rows"],
                "summary": t["summary"],
            }
            for t in db.list_tables(doc["id"])
        ],
        "text": text[:max_chars],
        "truncated": len(text) > max_chars,
    }


@mcp.tool(annotations=READ_ONLY)
async def get_document(document: str, max_chars: int = 20_000) -> dict[str, Any]:
    """Full text of one document (by id or exact filename) with page markers and tables."""
    return await anyio.to_thread.run_sync(_document, document, max(1000, min(max_chars, 100_000)))


@mcp.tool()
async def generate_document(request: str) -> dict[str, Any]:
    """Create a branded PDF (report, offer, memo, summary or free-form) from the documents.

    Example: "Prepare a report on concrete quality at the Agatova project". Branding and
    company details come from the app's Settings page. Returns the document id and its
    download URL (relative to the app, e.g. /api/generated/<id>/pdf).
    """
    prompt = request if detect_generate_intent(request) else f"Vytvor dokument: {request}"
    result = await answer_question(prompt)
    if result.document is None:
        raise ValueError("request was not recognised as a document request")
    # id + app-relative URL only — never an absolute path on the server's disk
    return {
        **result.document,
        "summary": result.answer,
        "sources": [_source_out(s, with_text=False) for s in result.sources],
    }


def mcp_transport_security() -> TransportSecuritySettings:
    """DNS-rebinding protection: only localhost + hosts from RAG_MCP_ALLOWED_HOSTS (Docker, LAN)."""
    extra = [h.strip() for h in get_settings().mcp_allowed_hosts.split(",") if h.strip()]
    hosts = ["127.0.0.1:*", "localhost:*", "[::1]:*", *extra]
    return TransportSecuritySettings(
        allowed_hosts=hosts,
        allowed_origins=[f"http://{h}" for h in hosts] + [f"https://{h}" for h in extra],
    )


def main() -> None:
    settings = get_settings()
    settings.ensure_dirs()
    db.init_db()
    # stdio = a local process started by the owner with direct access to the data
    # directory; the fail-closed default (no access when login is on) is for HTTP only
    set_scope(LOCAL_ADMIN)
    mcp.run("stdio")


if __name__ == "__main__":
    main()
