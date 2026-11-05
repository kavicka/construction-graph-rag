"""Eval entrypoints run retrieval in-process (no request) — with login on they must still see
the whole corpus instead of failing closed."""

import sys
from pathlib import Path
from typing import Any

import pytest

from app.auth import LOCAL_ADMIN, NO_ACCESS, allowed_doc_ids, current_scope
from app.config import get_settings
from eval import harness


def test_harness_main_runs_retrieval_as_local_admin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RAG_AUTH_ENABLED", "true")
    get_settings.cache_clear()
    assert current_scope() is NO_ACCESS
    seen: list[Any] = []

    def fake_eval_retrieval(ablation: harness.Ablation, questions: list[Any]) -> dict[str, Any]:
        seen.append((current_scope(), allowed_doc_ids()))
        metrics = ("recall@5", "recall@10", "mrr", "ndcg@10", "p50_ms")
        return {"name": ablation.name, "summary": dict.fromkeys(metrics, 0.0)}

    monkeypatch.setattr(harness, "ingest_variant", lambda variant, force=False: None)
    monkeypatch.setattr(harness, "eval_retrieval", fake_eval_retrieval)
    monkeypatch.setattr(harness, "_source_hash", lambda: "test")
    monkeypatch.setattr(harness, "_merge_previous", lambda results: results)
    monkeypatch.setattr(harness, "write_report", lambda results: None)
    monkeypatch.setattr(harness, "RESULTS_PATH", tmp_path / "results.json")
    monkeypatch.setattr(sys, "argv", ["harness", "--configs", "dense", "--limit", "1"])

    assert harness.main() == 0
    assert seen == [(LOCAL_ADMIN, None)]  # None = unrestricted
    assert current_scope() is NO_ACCESS  # the scope does not leak past main()
