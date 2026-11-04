"""SQLite connection: one short-lived connection per unit of work."""

import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager

from app.config import get_settings


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    settings = get_settings()
    conn = sqlite3.connect(settings.sqlite_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# Older SQLite builds cap a statement at 999 bound parameters; long IN (...) lists are
# split into batches of this size.
MAX_IN_PARAMS = 500


def batched(ids: Iterable[str]) -> Iterator[list[str]]:
    """Unique ids in batches small enough for one `IN (...)` clause each."""
    unique = list(dict.fromkeys(ids))
    for i in range(0, len(unique), MAX_IN_PARAMS):
        yield unique[i : i + MAX_IN_PARAMS]
