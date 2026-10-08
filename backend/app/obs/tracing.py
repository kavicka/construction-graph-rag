"""OpenTelemetry tracing s GenAI sémantickými konvenciami.

Každá otázka = jeden trace: rag.query → rag.route → rag.retrieve (dense / bm25 / graph /
rerank) → LLM volania (`chat <model>` s gen_ai.* atribútmi a počtom tokenov).

Spany idú do vlastnej SQLite tabuľky (vstavaný prehliadač v UI, žiadna externá služba)
a voliteľne cez OTLP do Langfuse / Phoenix / Jaeger (RAG_OTLP_ENDPOINT).
Vlastný TracerProvider (nie globálny) — testy a eval si ho môžu znova inicializovať.
"""

import json
import logging
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from functools import lru_cache
from typing import Any

from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SimpleSpanProcessor,
    SpanExporter,
    SpanExportResult,
)
from opentelemetry.trace import Span, Status, StatusCode, Tracer

from app import db
from app.config import get_settings

logger = logging.getLogger(__name__)

# GenAI semantic conventions (https://opentelemetry.io/docs/specs/semconv/gen-ai/)
GEN_AI_OPERATION = "gen_ai.operation.name"
GEN_AI_PROVIDER = "gen_ai.provider.name"
GEN_AI_REQUEST_MODEL = "gen_ai.request.model"
GEN_AI_REQUEST_TEMPERATURE = "gen_ai.request.temperature"
GEN_AI_USAGE_INPUT = "gen_ai.usage.input_tokens"
GEN_AI_USAGE_OUTPUT = "gen_ai.usage.output_tokens"
GEN_AI_FINISH = "gen_ai.response.finish_reasons"

MAX_ATTR_CHARS = 2000


class SqliteSpanExporter(SpanExporter):
    """Ukladá dokončené spany do `trace_spans` — data_dir sa číta pri každom zápise."""

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        rows = []
        for s in spans:
            ctx = s.get_span_context()
            if ctx is None:
                continue
            attrs = {k: v if not isinstance(v, tuple) else list(v)
                     for k, v in (s.attributes or {}).items()}
            rows.append((
                format(ctx.trace_id, "032x"),
                format(ctx.span_id, "016x"),
                format(s.parent.span_id, "016x") if s.parent else None,
                s.name,
                s.start_time or 0,
                s.end_time or 0,
                s.status.status_code.name,
                json.dumps(attrs, ensure_ascii=False, default=str),
            ))
        try:
            db.insert_spans(rows)
        except Exception:
            logger.exception("zápis spanov zlyhal")
            return SpanExportResult.FAILURE
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        pass


@lru_cache
def _provider() -> TracerProvider:
    settings = get_settings()
    provider = TracerProvider(resource=Resource.create({"service.name": "rag"}))
    provider.add_span_processor(SimpleSpanProcessor(SqliteSpanExporter()))
    if settings.otlp_endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        headers = dict(
            h.split("=", 1) for h in settings.otlp_headers.split(",") if "=" in h
        )
        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.otlp_endpoint, headers=headers))
        )
        logger.info("OTLP export → %s", settings.otlp_endpoint)
    return provider


def get_tracer() -> Tracer:
    if not get_settings().tracing:
        return trace.NoOpTracer()
    return _provider().get_tracer("rag")


def shutdown_tracing() -> None:
    if _provider.cache_info().currsize:
        _provider().shutdown()
        _provider.cache_clear()


def _clean(value: Any) -> Any:
    if isinstance(value, str):
        return value[:MAX_ATTR_CHARS]
    if isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, (list, tuple)):
        return [str(v)[:200] for v in value]
    return str(value)[:MAX_ATTR_CHARS]


def set_attrs(span: Span, **attrs: Any) -> None:
    for key, value in attrs.items():
        if value is not None:
            span.set_attribute(key.replace("__", "."), _clean(value))


@contextmanager
def span(name: str, *, parent: Context | None = None, **attrs: Any) -> Iterator[Span]:
    """Child span (aktuálny alebo explicitný parent). Kľúče s `__` → bodka:
    span("x", gen_ai__request__model="qwen") → atribút gen_ai.request.model."""
    with get_tracer().start_as_current_span(name, context=parent) as s:
        set_attrs(s, **attrs)
        try:
            yield s
        except Exception as exc:
            s.set_status(Status(StatusCode.ERROR, str(exc)[:200]))
            s.record_exception(exc)
            raise


def start_root(name: str, **attrs: Any) -> tuple[Span, Context]:
    """Root span pre async generátor — nesmie byť „current“ cez yield, preto sa
    rodičovský kontext podáva deťom explicitne."""
    root = get_tracer().start_span(name, context=Context())
    set_attrs(root, **attrs)
    return root, trace.set_span_in_context(root)


def trace_id_of(s: Span) -> str | None:
    ctx = s.get_span_context()
    return format(ctx.trace_id, "032x") if ctx.is_valid else None


def record_llm_usage(s: Span, data: dict[str, Any]) -> None:
    """Ollama vracia prompt_eval_count / eval_count v poslednom (done) chunku."""
    set_attrs(
        s,
        **{
            GEN_AI_USAGE_INPUT: data.get("prompt_eval_count"),
            GEN_AI_USAGE_OUTPUT: data.get("eval_count"),
            GEN_AI_FINISH: [data["done_reason"]] if data.get("done_reason") else None,
        },
    )
