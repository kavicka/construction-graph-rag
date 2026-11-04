import json

import pytest

from app import db
from app.config import get_settings
from app.graph import communities as cm
from app.graph.communities import (
    detect_communities,
    global_context,
    is_global_query,
    summarize_community,
)
from app.index.chunker import Chunk
from app.index.lancedb_store import replace_document_chunks


def _seed() -> None:
    """Two documents about concrete, one about a crane; 'zariadenie' is everywhere (hub)."""
    get_settings().ensure_dirs()
    db.init_db()
    topics = {
        "a": ["betón", "pevnosť", "kocky", "zariadenie"],
        "b": ["betón", "pevnosť", "trhlina", "zariadenie"],
        "c": ["žeriav", "prehliadka", "zaťaženie", "zariadenie"],
    }
    for doc, concepts in topics.items():
        db.insert_document(doc, f"{doc}.pdf", f"sha-{doc}")
        cid = f"{doc}:t0"
        replace_document_chunks(
            doc,
            [
                Chunk(
                    cid,
                    doc,
                    f"text {doc}",
                    f"úsek {doc} " + " ".join(concepts),
                    1,
                    1,
                    "",
                    "text",
                    None,
                )
            ],
            [[1.0, 0.0]],
        )
        db.upsert_graph_node(f"chunk:{cid}", "chunk", cid, doc_id=doc)
        for c in concepts:
            db.upsert_graph_node(f"concept:{c}", "concept", c, norm_key=c)
            db.upsert_graph_edge(f"chunk:{cid}", f"concept:{c}", "MENTIONS", evidence_chunk_id=cid)


def test_global_query_detection() -> None:
    assert is_global_query("Aké sú hlavné témy všetkých dokumentov?")
    assert is_global_query("What are the main themes across the library?")
    assert not is_global_query("Aká je cena diela Agátová?")


def test_communities_split_topics_and_drop_hubs() -> None:
    _seed()
    found = detect_communities()
    concept_sets = [set(c.concepts) for c in found]
    assert {"betón", "pevnosť", "kocky", "trhlina"} in concept_sets
    assert {"žeriav", "prehliadka", "zaťaženie"} in concept_sets
    assert all("zariadenie" not in s for s in concept_sets)  # generic hub gone
    concrete = next(c for c in found if "betón" in c.concepts)
    assert set(concrete.documents) == {"a", "b"}
    assert found[0] is concrete  # more documents → ranked higher


def test_summary_is_cached_and_used(monkeypatch: pytest.MonkeyPatch) -> None:
    _seed()
    calls: list[str] = []

    def fake(*a: object, **k: object) -> str:
        calls.append("x")
        return json.dumps({"title": "Kvalita betónu", "summary": "Skúšky pevnosti kociek."})

    monkeypatch.setattr(cm, "generate_sync", fake)
    concrete = next(c for c in detect_communities() if "betón" in c.concepts)
    summarize_community(concrete)
    again = next(c for c in detect_communities() if "betón" in c.concepts)
    assert (again.title, again.summary) == ("Kvalita betónu", "Skúšky pevnosti kociek.")
    summarize_community(again)
    assert len(calls) == 1  # second time from cache

    sources, messages = global_context("Hlavné témy?", [again])
    assert {s.filename for s in sources} == {"a.pdf", "b.pdf"}
    assert "Kvalita betónu" in messages[1]["content"]
    assert "[1]" in messages[1]["content"]
