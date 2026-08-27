from collections.abc import AsyncIterator

import pytest

from app.api.chat import strip_think


async def _gen(parts: list[str]) -> AsyncIterator[str]:
    for p in parts:
        yield p


async def _collect(parts: list[str]) -> str:
    return "".join([t async for t in strip_think(_gen(parts))])


@pytest.mark.anyio
async def test_strips_think_block() -> None:
    parts = ["<think>", "hlboká ", "úvaha", "</think>\n", "Odpoveď ", "je 42."]
    assert await _collect(parts) == "Odpoveď je 42."


@pytest.mark.anyio
async def test_passthrough_without_think() -> None:
    parts = ["Prietok ", "je ", "12.4 l/min."]
    assert await _collect(parts) == "Prietok je 12.4 l/min."


@pytest.mark.anyio
async def test_short_answer_flushed() -> None:
    assert await _collect(["OK"]) == "OK"
    assert await _collect(["<t", "hi"]) == "<thi"


@pytest.mark.anyio
async def test_think_split_across_tokens() -> None:
    parts = ["<th", "ink>úvaha</th", "ink>", "Výsledok."]
    assert await _collect(parts) == "Výsledok."
