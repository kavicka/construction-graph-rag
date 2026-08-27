from app.index.chunker import Chunk
from app.index.lancedb_store import (
    count_chunks,
    replace_document_chunks,
    search_dense,
    search_fts,
)


def _chunk(cid: str, doc: str, text: str, typ: str = "text") -> Chunk:
    return Chunk(
        chunk_id=cid,
        doc_id=doc,
        text=text,
        raw_text=text,
        page_from=1,
        page_to=1,
        section_path="Úvod",
        type=typ,
        table_id=None,
    )


def test_roundtrip_dense_fts_and_idempotency(isolated_data: object) -> None:
    chunks = [
        _chunk("d1:t0", "d1", "meranie prietoku chladiaceho média"),
        _chunk("d1:t1", "d1", "kalibrácia snímača tlaku"),
    ]
    vectors = [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0]]
    assert replace_document_chunks("d1", chunks, vectors) == 2
    assert count_chunks() == 2

    dense = search_dense([0.9, 0.1, 0.0, 0.0], k=2)
    assert dense[0]["chunk_id"] == "d1:t0"

    fts = search_fts("kalibrácia", k=5)
    assert fts and fts[0]["chunk_id"] == "d1:t1"

    # re-ingest toho istého dokumentu nesmie duplikovať
    assert replace_document_chunks("d1", chunks, vectors) == 2
    assert count_chunks("d1") == 2


def test_search_on_empty_store(isolated_data: object) -> None:
    assert search_dense([1.0, 0.0], k=3) == []
    assert search_fts("čokoľvek", k=3) == []
    assert count_chunks() == 0
