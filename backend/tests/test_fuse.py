from app.retrieve.fuse import rrf_fuse


def _rows(ids: list[str]) -> list[dict[str, str]]:
    return [{"chunk_id": i, "text": f"text {i}"} for i in ids]


def test_rrf_overlap_wins() -> None:
    dense = _rows(["a", "b", "c"])
    fts = _rows(["b", "d"])
    fused = rrf_fuse([dense, fts], top_k=4)
    ids = [r["chunk_id"] for r in fused]
    assert ids[0] == "b"  # v oboch zoznamoch → najvyššie RRF
    assert set(ids) == {"a", "b", "c", "d"}
    assert fused[0]["rrf_score"] > fused[-1]["rrf_score"]


def test_rrf_top_k_and_empty() -> None:
    assert rrf_fuse([[], []], top_k=5) == []
    fused = rrf_fuse([_rows(["a", "b", "c", "d", "e"])], top_k=2)
    assert [r["chunk_id"] for r in fused] == ["a", "b"]
