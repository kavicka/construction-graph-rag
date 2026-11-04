"""Helpers shared by the store modules."""

import uuid
from typing import Any


def new_id() -> str:
    return uuid.uuid4().hex


def rows_as_dicts(rows: list[Any]) -> list[dict[str, Any]]:
    return [dict(r) for r in rows]
