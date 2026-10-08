"""Kontroly prístupu pre endpointy — 404 namiesto 403, aby API neprezrádzalo, čo existuje."""

from typing import Any

from fastapi import HTTPException

from app import db
from app.auth import can_access_document, current_scope


def require_admin() -> None:
    if not current_scope().unrestricted:
        raise HTTPException(status_code=403, detail="len pre administrátora")


def ensure_document(doc_id: str) -> dict[str, Any]:
    doc = db.get_document(doc_id)
    if doc is None or not can_access_document(doc_id):
        raise HTTPException(status_code=404, detail="dokument neexistuje")
    return doc


def chat_visible(chat: dict[str, Any]) -> bool:
    scope = current_scope()
    # bez prihlasovania (user_id None) vidno všetko; inak len vlastné chaty
    return scope.user_id is None or chat.get("user_id") == scope.user_id


def ensure_chat(chat_id: str) -> dict[str, Any]:
    chat = db.get_chat(chat_id)
    if chat is None or not chat_visible(chat):
        raise HTTPException(status_code=404, detail="chat neexistuje")
    return chat


def ensure_message(message_id: str) -> dict[str, Any]:
    message = db.get_message(message_id)
    if message is None:
        raise HTTPException(status_code=404, detail="správa neexistuje")
    ensure_chat(message["chat_id"])
    return message
