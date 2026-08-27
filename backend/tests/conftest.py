from collections.abc import Iterator
from pathlib import Path

import pytest

from app.config import get_settings


def _clear_singletons() -> None:
    """Všetky lru_cache singletony viazané na settings/data_dir."""
    from app.index import embedder, lancedb_store

    get_settings.cache_clear()
    lancedb_store._connect.cache_clear()
    embedder.get_embedder.cache_clear()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def isolated_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Každý test má vlastný data_dir — žiadne zdieľané app.db ani LanceDB."""
    data_dir = tmp_path / "data"
    monkeypatch.setenv("RAG_DATA_DIR", str(data_dir))
    _clear_singletons()
    yield data_dir
    _clear_singletons()
