import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from app import db
from app.api import (
    attendance,
    auth,
    chat,
    company,
    documents,
    generated,
    graph,
    health,
    search,
    tables,
    task_import,
    traces,
    workforce,
)
from app.auth import (
    NO_ACCESS,
    SESSION_COOKIE,
    ensure_bootstrap_admin,
    reset_scope,
    resolve_token,
    set_scope,
)
from app.config import get_settings
from app.db import init_db
from app.ingest.inbox import watch_inbox
from app.ingest.service import schedule_ingest
from app.mcp_server import mcp, mcp_transport_security
from app.obs.tracing import shutdown_tracing
from app.workforce.runtime import WorkerGuard, init_workforce, scheduler_loop

logger = logging.getLogger(__name__)


def _recover_stuck_ingests() -> None:
    """Po reštarte servera dobehni dokumenty, ktorých ingest zomrel v strede."""
    for doc in db.list_documents():
        if doc["status"] not in ("done", "error"):
            logger.info("recovery: doc %s (%s) → re-ingest", doc["id"], doc["status"])
            schedule_ingest(doc["id"])


class _McpEndpoint:
    """MCP streamable HTTP na /mcp. Session manager MCP SDK sa dá spustiť len raz,
    preto lifespan pri každom štarte vytvorí novú ASGI appku a tu ju len odovzdáme."""

    app: ASGIApp | None = None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if self.app is None:
            raise RuntimeError("MCP endpoint ešte nebeží")
        await self.app(scope, receive, send)


mcp_endpoint = _McpEndpoint()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    get_settings().ensure_dirs()
    init_db()
    if get_settings().auth_enabled:
        ensure_bootstrap_admin()
    _recover_stuck_ingests()
    init_workforce()
    # stateless: každý MCP request beží v úlohe svojho HTTP requestu → nesie prístupové
    # práva TOHO requestu (nie toho, kto session otvoril)
    mcp_endpoint.app = mcp.streamable_http_app(
        streamable_http_path="/mcp", transport_security=mcp_transport_security(),
        stateless_http=True,
    )
    stop_inbox = asyncio.Event()
    inbox_task = (
        asyncio.create_task(watch_inbox(stop_inbox)) if get_settings().inbox_sync else None
    )
    scheduler_task = asyncio.create_task(scheduler_loop(stop_inbox))
    async with mcp.session_manager.run():
        yield
    stop_inbox.set()
    if inbox_task is not None:
        await inbox_task
    await scheduler_task
    mcp_endpoint.app = None
    shutdown_tracing()


PUBLIC_PATHS = {
    "/api/auth/login", "/api/auth/me", "/api/health",
    "/api/auth/workers", "/api/auth/pin",  # prihlásenie pracovníka PIN-om
}


def _host_allowed(host: str) -> bool:
    """Rovnaký zoznam hostov ako DNS-rebinding ochrana MCP — chráni aj /api: bez neho by
    cudzia stránka s rebindovanou doménou čítala knižnicu cez prehliadač používateľa."""
    for allowed in mcp_transport_security().allowed_hosts:
        if host == allowed or (allowed.endswith(":*") and host.startswith(allowed[:-2] + ":")):
            return True
    return False


class AuthMiddleware:
    """Čisté ASGI (nie BaseHTTPMiddleware) — scope v contextvar ostane platný aj počas
    streamovania SSE odpovede, ktoré beží v podúlohe."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        root = scope.get("root_path", "")
        if root and path.startswith(root):
            path = path[len(root):]  # --root-path / reverse proxy prefix
        request = Request(scope)
        if (path.startswith("/api/") or path == "/mcp") and not _host_allowed(
            request.headers.get("host", "")
        ):
            await JSONResponse({"detail": "neplatný Host"}, status_code=421)(scope, receive, send)
            return
        if not get_settings().auth_enabled:
            await self.app(scope, receive, send)
            return

        auth_header = request.headers.get("authorization", "")
        bearer = auth_header[7:] if auth_header.lower().startswith("bearer ") else None
        access = await run_in_threadpool(
            resolve_token, request.cookies.get(SESSION_COOKIE), bearer
        )
        if access is None and path not in PUBLIC_PATHS:
            await JSONResponse({"detail": "prihlás sa"}, status_code=401)(scope, receive, send)
            return
        # fail closed: s prihlasovaním nikdy nebeží request s implicitným lokálnym adminom
        token = set_scope(access or NO_ACCESS)
        try:
            await self.app(scope, receive, send)
        finally:
            reset_scope(token)


app = FastAPI(title="rag", version="0.1.0", lifespan=lifespan)
# poradie: posledný pridaný je vonkajší — WorkerGuard beží VO VNÚTRI AuthMiddleware,
# až keď je nastavený access scope
app.add_middleware(WorkerGuard)
app.add_middleware(AuthMiddleware)
app.include_router(auth.router, prefix="/api")
app.include_router(health.router, prefix="/api")
app.include_router(documents.router, prefix="/api")
app.include_router(tables.router, prefix="/api")
app.include_router(search.router, prefix="/api")
app.include_router(chat.router, prefix="/api")
app.include_router(graph.router, prefix="/api")
app.include_router(company.router, prefix="/api")
app.include_router(generated.router, prefix="/api")
app.include_router(traces.router, prefix="/api")
app.include_router(workforce.router, prefix="/api")
app.include_router(task_import.router, prefix="/api")
app.include_router(attendance.router, prefix="/api")
app.router.routes.append(Route("/mcp", endpoint=mcp_endpoint))
