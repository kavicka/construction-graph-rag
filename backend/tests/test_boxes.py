from types import SimpleNamespace

from app import db
from app.config import get_settings
from app.index.chunker import MAX_BOXES_PER_CHUNK, item_boxes


class _BBox:
    def __init__(self, l: float, t: float, r: float, b: float) -> None:  # noqa: E741
        self.l, self.t, self.r, self.b = l, t, r, b

    def to_top_left_origin(self, page_height: float) -> "_BBox":
        # Docling: BOTTOMLEFT → TOPLEFT (y sa preklopí)
        return _BBox(self.l, page_height - self.t, self.r, page_height - self.b)


def _doc() -> SimpleNamespace:
    return SimpleNamespace(pages={2: SimpleNamespace(size=SimpleNamespace(width=600, height=800))})


def _item(page: int, bbox: _BBox) -> SimpleNamespace:
    return SimpleNamespace(prov=[SimpleNamespace(page_no=page, bbox=bbox)])


def test_item_boxes_relative_top_left() -> None:
    boxes = item_boxes(_doc(), [_item(2, _BBox(60, 760, 300, 740))])
    assert boxes == [[2.0, 0.1, 0.05, 0.5, 0.075]]


def test_item_boxes_skips_unknown_pages_and_caps() -> None:
    assert item_boxes(_doc(), [_item(9, _BBox(0, 10, 10, 0))]) == []
    many = [_item(2, _BBox(0, 800, 600, 0)) for _ in range(40)]
    assert len(item_boxes(_doc(), many)) == MAX_BOXES_PER_CHUNK


def test_chunk_boxes_roundtrip() -> None:
    get_settings().ensure_dirs()
    db.init_db()
    db.replace_chunk_boxes("d1", {"d1:t0": [[1, 0.1, 0.1, 0.5, 0.2]], "d1:t1": []})
    assert db.get_chunk_boxes(["d1:t0", "d1:t1", "x"]) == {"d1:t0": [[1, 0.1, 0.1, 0.5, 0.2]]}
    db.replace_chunk_boxes("d1", {})
    assert db.get_chunk_boxes(["d1:t0"]) == {}
