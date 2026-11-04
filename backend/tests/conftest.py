from collections.abc import Iterator
from pathlib import Path

import pytest

from app.config import get_settings

# fixtures of the Firma module (workers, attendance, task import)
pytest_plugins = ["tests.wf_fixtures"]


def _clear_singletons() -> None:
    """All lru_cache singletons tied to settings/data_dir."""
    from app.index import embedder, lancedb_store
    from app.obs import tracing

    for fn in (get_settings, lancedb_store._connect, embedder.get_embedder, tracing._provider):
        cache_clear = getattr(fn, "cache_clear", None)
        if cache_clear is not None:  # a monkeypatched fake cache has none
            cache_clear()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def isolated_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Each test gets its own data_dir — no shared app.db or LanceDB."""
    data_dir = tmp_path / "data"
    monkeypatch.setenv("RAG_DATA_DIR", str(data_dir))
    # heavy/LLM parts of the pipeline are off in tests; tests that exercise them turn them on
    monkeypatch.setenv("RAG_RERANK", "false")
    monkeypatch.setenv("RAG_CONTEXTUAL_CHUNKS", "false")
    monkeypatch.setenv("RAG_INBOX_SYNC", "false")
    # the LLM scope classifier would call Ollama in tests; gate tests enable it with a fake LLM
    monkeypatch.setenv("RAG_SCOPE_CLASSIFIER", "false")
    # TestClient sends Host "testserver" — the production allowlist (rightly) does not know it
    monkeypatch.setenv("RAG_MCP_ALLOWED_HOSTS", "testserver")
    _clear_singletons()
    yield data_dir
    _clear_singletons()
