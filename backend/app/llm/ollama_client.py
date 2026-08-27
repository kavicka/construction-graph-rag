"""Tenký klient na Ollama API.

Sync varianta pre indexovacie thready (tabuľkové sumáre, extrakcia entít),
async streaming chat pre odpovede.
"""

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.config import get_settings


class OllamaError(RuntimeError):
    pass


def generate_sync(
    model: str,
    prompt: str,
    *,
    system: str | None = None,
    json_schema: dict[str, Any] | None = None,
    temperature: float = 0.2,
    num_ctx: int = 4096,
    timeout: float = 120.0,
    think: bool = False,
) -> str:
    """Jeden non-stream generate. Qwen3: think=False vypína reasoning režim (rýchlosť)."""
    settings = get_settings()
    payload: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "think": think,
        "keep_alive": "5m",
        "options": {"temperature": temperature, "num_ctx": num_ctx},
    }
    if system is not None:
        payload["system"] = system
    if json_schema is not None:
        payload["format"] = json_schema

    try:
        resp = httpx.post(
            f"{settings.ollama_base_url}/api/generate", json=payload, timeout=timeout
        )
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPError as exc:
        raise OllamaError(f"ollama generate zlyhal ({model}): {exc}") from exc

    response = data.get("response")
    if not isinstance(response, str):
        raise OllamaError(f"ollama vrátil neočakávanú odpoveď: {data!r:.200}")
    return response.strip()


async def chat_stream(
    model: str,
    messages: list[dict[str, str]],
    *,
    temperature: float = 0.3,
    num_ctx: int = 8192,
    think: bool = False,
) -> AsyncIterator[str]:
    """Streamuje tokeny odpovede. Qwen3: think=False — reasoning vypnutý pre latenciu."""
    settings = get_settings()
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": True,
        "think": think,
        "keep_alive": "10m",
        "options": {"temperature": temperature, "num_ctx": num_ctx},
    }
    timeout = httpx.Timeout(300.0, connect=5.0)
    try:
        async with (
            httpx.AsyncClient(timeout=timeout) as client,
            client.stream(
                "POST", f"{settings.ollama_base_url}/api/chat", json=payload
            ) as resp,
        ):
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.strip():
                    continue
                data = json.loads(line)
                if err := data.get("error"):
                    raise OllamaError(f"ollama chat: {err}")
                chunk = data.get("message", {}).get("content", "")
                if chunk:
                    yield chunk
                if data.get("done"):
                    return
    except httpx.HTTPError as exc:
        raise OllamaError(f"ollama chat zlyhal ({model}): {exc}") from exc
