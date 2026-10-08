"""Sledovaný priečinok — knižnica sa synchronizuje so súbormi v data/inbox.

Nový súbor → ingest, zmenený (iný sha256) → reindex ako nová verzia, zmazaný → dokument
sa odstráni. Polling (bez natívnych watcherov) — funguje rovnako na macOS, Linuxe aj
v Docker volume. Súbory, do ktorých sa práve zapisuje, sa preskočia (čerstvý mtime).
"""

import asyncio
import contextlib
import hashlib
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from app import db
from app.config import get_settings
from app.ingest.formats import detect_format
from app.ingest.store import delete_document, store_document

logger = logging.getLogger(__name__)

SETTLE_SECONDS = 2.0  # súbor musí byť 2 s nemenný — kopírovanie veľkého PDF ešte beží


@dataclass
class SyncResult:
    added: list[str] = field(default_factory=list)  # doc_id na ingest
    updated: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)


def inbox_dir() -> Path:
    d = get_settings().data_dir / "inbox"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _source_key(path: Path) -> str:
    """Kľúč = cesta relatívne k inboxu — rovnaká na hostiteľovi aj v Docker volume."""
    return path.relative_to(inbox_dir()).as_posix()


def sync_inbox(now: float | None = None) -> SyncResult:
    now = now if now is not None else time.time()
    result = SyncResult()
    tracked = {d["source_path"]: d for d in db.list_documents() if d.get("source_path")}
    present: set[str] = set()

    for path in sorted(inbox_dir().iterdir()):
        # symlinky by viedli mimo inboxu; skryté súbory sú dočasné súbory editorov
        if path.is_symlink() or not path.is_file() or path.name.startswith("."):
            continue
        key = _source_key(path)
        present.add(key)
        try:
            if now - path.stat().st_mtime < SETTLE_SECONDS:
                continue
            data = path.read_bytes()
        except OSError as exc:
            logger.warning("inbox: %s sa nedá čítať (%s)", path.name, exc)
            continue
        fmt = detect_format(path.name, data)
        if fmt is None:
            continue
        doc = tracked.get(key)
        if doc is not None and doc["sha256"] == hashlib.sha256(data).hexdigest():
            continue
        access_json = None
        if doc is not None:
            if doc["status"] not in ("done", "error"):
                continue  # predošlá verzia sa ešte spracúva
            access_json = doc.get("access_json")  # nová verzia dedí prístupové práva
            delete_document(doc["id"])
        row, created = store_document(path.name, data, fmt, source_path=key)
        if created and access_json:
            db.set_document_access(row["id"], access_json)
        if created:
            (result.updated if doc is not None else result.added).append(row["id"])

    for key, doc in tracked.items():
        if key not in present and doc["status"] in ("done", "error"):
            delete_document(doc["id"])
            result.removed.append(doc["id"])
    return result


async def watch_inbox(stop: asyncio.Event) -> None:
    from app.ingest.service import schedule_ingest

    interval = get_settings().inbox_interval_s
    logger.info("inbox sync: %s (každých %.0f s)", inbox_dir(), interval)
    while not stop.is_set():
        try:
            result = await asyncio.to_thread(sync_inbox)
            for doc_id in [*result.added, *result.updated]:
                schedule_ingest(doc_id)
            if result.added or result.updated or result.removed:
                logger.info("inbox: +%d ~%d -%d", len(result.added), len(result.updated),
                            len(result.removed))
        except Exception:
            logger.exception("inbox sync zlyhal")
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval)
