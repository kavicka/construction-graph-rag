import os
from pathlib import Path

import numpy as np
import pytest

from app import db
from app.config import get_settings
from app.index.chunker import Chunk
from app.index.lancedb_store import replace_document_chunks
from app.index.visual import index_pages, maxsim, search_pages
from app.retrieve.pipeline import RetrievalConfig, rank_chunks
from eval.pdfgen import render_pages


class FakeVisual:
    """Strana = 4 patche; patch 0 nesie 'tému' strany podľa farby pixelu vľavo hore."""

    def embed_images(self, images: list[object]) -> list[np.ndarray]:
        out = []
        for img in images:
            dark = np.asarray(img).mean() < 250  # stránka s tabuľkou je tmavšia
            topic = np.array([1.0, 0.0, 0.0]) if dark else np.array([0.0, 1.0, 0.0])
            out.append(np.vstack([topic, np.eye(3)[2], np.eye(3)[2], np.eye(3)[2]]))
        return out

    def embed_query(self, text: str) -> np.ndarray:
        return np.array([[1.0, 0.0, 0.0]]) if "tabu" in text else np.array([[0.0, 1.0, 0.0]])


def test_maxsim() -> None:
    q = np.array([[1.0, 0.0], [0.0, 1.0]])
    page = np.array([[1.0, 0.0], [0.6, 0.8]])
    assert maxsim(q, page) == pytest.approx(1.0 + 0.8)


def _pdf(tmp: Path) -> Path:
    path = tmp / "v.pdf"
    render_pages(
        path,
        "Vizuál",
        [
            [("p", "Krátky text strany jeden.")],
            [
                (
                    "table",
                    [[f"stĺpec {i}" for i in range(6)]]
                    + [[str(r * c) for c in range(6)] for r in range(30)],
                )
            ],
        ],
    )
    return path


def test_index_and_search_pages(tmp_path: Path) -> None:
    get_settings().ensure_dirs()
    db.init_db()
    pdf = _pdf(tmp_path)
    assert index_pages("d1", pdf, FakeVisual()) == 2
    assert index_pages("d1", pdf, FakeVisual()) == 2  # idempotentné
    top = search_pages("kde je tá tabuľka?", k=1, embedder=FakeVisual())
    assert top[0][:2] == ("d1", 2)
    assert search_pages("tabuľka", doc_ids={"iny"}, embedder=FakeVisual()) == []


def test_rank_chunks_adds_visual_pages(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    get_settings().ensure_dirs()
    db.init_db()
    db.insert_document("d1", "v.pdf", "sha")
    replace_document_chunks(
        "d1",
        [
            Chunk("d1:t0", "d1", "úvod", "úvod", 1, 1, "", "text", None),
            Chunk("d1:t1", "d1", "čísla", "čísla", 2, 2, "", "text", None),
        ],
        [[0.0, 1.0], [0.0, 1.0]],
    )
    index_pages("d1", _pdf(tmp_path), FakeVisual())
    monkeypatch.setattr("app.index.visual.get_visual_embedder", FakeVisual)
    rows = rank_chunks(
        "tabuľka", k=1, cfg=RetrievalConfig(dense=False, fts=False, graph=False, visual=True)
    )
    assert [r["chunk_id"] for r in rows] == ["d1:t1"]


@pytest.mark.skipif(not os.environ.get("RUN_COLPALI"), reason="sťahuje model (RUN_COLPALI=1)")
def test_real_colpali_model(tmp_path: Path) -> None:
    get_settings().ensure_dirs()
    pdf = _pdf(tmp_path)
    assert index_pages("real", pdf) == 2
    assert search_pages("table with numbers", k=1)[0][:2] == ("real", 2)
