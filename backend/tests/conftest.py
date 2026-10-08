from collections.abc import Iterator
from pathlib import Path

import pytest

from app.config import get_settings

# fixtures modulu Firma (pracovníci, dochádzka, import úloh)
pytest_plugins = ["tests.wf_fixtures"]


def _clear_singletons() -> None:
    """Všetky lru_cache singletony viazané na settings/data_dir."""
    from app.index import embedder, lancedb_store
    from app.obs import tracing

    for fn in (get_settings, lancedb_store._connect, embedder.get_embedder, tracing._provider):
        cache_clear = getattr(fn, "cache_clear", None)
        if cache_clear is not None:  # monkeypatchnutý fake cache nemá
            cache_clear()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def isolated_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Každý test má vlastný data_dir — žiadne zdieľané app.db ani LanceDB."""
    data_dir = tmp_path / "data"
    monkeypatch.setenv("RAG_DATA_DIR", str(data_dir))
    # ťažké/LLM časti pipeline sú v testoch vypnuté; testy, ktoré ich overujú, ich zapnú
    monkeypatch.setenv("RAG_RERANK", "false")
    monkeypatch.setenv("RAG_CONTEXTUAL_CHUNKS", "false")
    monkeypatch.setenv("RAG_INBOX_SYNC", "false")
    # LLM klasifikátor rozsahu by v testoch volal Ollamu; testy brány ho zapnú s fake LLM
    monkeypatch.setenv("RAG_SCOPE_CLASSIFIER", "false")
    # TestClient posiela Host „testserver“ — produkčný allowlist ho (správne) nepozná
    monkeypatch.setenv("RAG_MCP_ALLOWED_HOSTS", "testserver")
    _clear_singletons()
    yield data_dir
    _clear_singletons()
