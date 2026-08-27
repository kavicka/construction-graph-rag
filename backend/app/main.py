import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import db
from app.api import chat, documents, graph, health, search, tables
from app.config import get_settings
from app.db import init_db
from app.ingest.service import schedule_ingest

logger = logging.getLogger(__name__)


def _recover_stuck_ingests() -> None:
    """Po reštarte servera dobehni dokumenty, ktorých ingest zomrel v strede."""
    for doc in db.list_documents():
        if doc["status"] not in ("done", "error"):
            logger.info("recovery: doc %s (%s) → re-ingest", doc["id"], doc["status"])
            schedule_ingest(doc["id"])


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    get_settings().ensure_dirs()
    init_db()
    _recover_stuck_ingests()
    yield


app = FastAPI(title="rag", version="0.1.0", lifespan=lifespan)
app.include_router(health.router, prefix="/api")
app.include_router(documents.router, prefix="/api")
app.include_router(tables.router, prefix="/api")
app.include_router(search.router, prefix="/api")
app.include_router(chat.router, prefix="/api")
app.include_router(graph.router, prefix="/api")
