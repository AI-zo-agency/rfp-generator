"""Agency-wide standing corrections.

A correction is a human statement that supersedes what the knowledge base
says — "Ron Comer has retired", "Ella Lindau is now Director of Operations".
Corrections live in Supermemory as ordinary documents (no local store) but are
read deterministically by listing the container, never by RAG search, so an
agent always sees all of them.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from app.services import supermemory

logger = logging.getLogger(__name__)

CORRECTION_TYPE = "kb_correction"
CORRECTION_ID_PREFIX = "kbnote:"

_UNAVAILABLE_BLOCK = (
    "## STANDING CORRECTIONS\n(corrections unavailable — Supermemory unreachable)"
)


def _metadata(memory: dict[str, Any]) -> dict[str, Any]:
    raw = memory.get("metadata")
    return raw if isinstance(raw, dict) else {}


def is_correction(memory: dict[str, Any]) -> bool:
    metadata = _metadata(memory)
    if metadata.get("type") == CORRECTION_TYPE:
        return metadata.get("active") is not False
    return False


def _to_row(memory: dict[str, Any]) -> dict[str, Any]:
    metadata = _metadata(memory)
    return {
        "id": str(memory.get("id") or ""),
        "customId": str(memory.get("customId") or ""),
        "title": str(metadata.get("title") or ""),
        "note": str(metadata.get("note") or ""),
        "createdAt": str(metadata.get("createdAt") or memory.get("createdAt") or ""),
        "updatedAt": str(metadata.get("updatedAt") or ""),
        "linkedDocumentId": str(metadata.get("linkedDocumentId") or "") or None,
    }


async def list_corrections() -> list[dict[str, Any]]:
    """All active corrections, newest first."""
    memories = await supermemory.list_container_memories(limit=500)
    rows = [_to_row(memory) for memory in memories if is_correction(memory)]
    rows = [row for row in rows if row["note"]]
    rows.sort(key=lambda row: row["createdAt"], reverse=True)
    return rows


def _short_date(value: str) -> str:
    return value[:10] if len(value) >= 10 else value


def corrections_block(corrections: list[dict[str, Any]]) -> str:
    if not corrections:
        return ""
    lines = [
        "## STANDING CORRECTIONS (authoritative)",
        "These override the knowledge base excerpts below. If a document conflicts",
        "with a correction, the correction wins and the document's version must not",
        "be printed or cited.",
    ]
    for row in corrections:
        date = _short_date(str(row.get("createdAt") or ""))
        note = str(row.get("note") or "").strip()
        lines.append(f"- ({date}) {note}" if date else f"- {note}")
    return "\n".join(lines)


async def corrections_prompt_block() -> str:
    """Render the block for prompt assembly; never raises."""
    try:
        return corrections_block(await list_corrections())
    except supermemory.SupermemoryError as exc:
        logger.warning("standing corrections unavailable: %s", exc)
        return _UNAVAILABLE_BLOCK


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def render_document_body(note: str, created_at: str) -> str:
    return (
        "STANDING CORRECTION (authoritative — supersedes older knowledge-base documents)\n"
        f"Added {_short_date(created_at)}\n"
        f"{note.strip()}"
    )


def _write_metadata(
    *,
    title: str,
    note: str,
    created_at: str,
    updated_at: str = "",
    linked_document_id: str | None = None,
    active: bool = True,
) -> dict[str, str | int | bool]:
    metadata: dict[str, str | int | bool] = {
        "type": CORRECTION_TYPE,
        "title": title.strip() or note.strip()[:60],
        "note": note.strip(),
        "createdAt": created_at,
        "updatedAt": updated_at,
        "active": active,
    }
    if linked_document_id:
        metadata["linkedDocumentId"] = linked_document_id
    return metadata


async def _find_correction(custom_id: str) -> dict[str, Any] | None:
    for row in await list_corrections():
        if row["customId"] == custom_id:
            return row
    return None


async def create_correction(
    *,
    title: str,
    note: str,
    linked_document_id: str | None = None,
) -> dict[str, Any]:
    clean_note = note.strip()
    if not clean_note:
        raise ValueError("Note text is required.")

    created_at = _now_iso()
    custom_id = f"{CORRECTION_ID_PREFIX}{uuid.uuid4()}"
    metadata = _write_metadata(
        title=title,
        note=clean_note,
        created_at=created_at,
        linked_document_id=linked_document_id,
    )
    result = await supermemory.add_text_document(
        content=render_document_body(clean_note, created_at),
        custom_id=custom_id,
        metadata=metadata,
    )
    supermemory.invalidate_document_cache()
    return {
        "id": str(result.get("id") or ""),
        "customId": custom_id,
        "title": str(metadata["title"]),
        "note": clean_note,
        "createdAt": created_at,
        "updatedAt": "",
        "linkedDocumentId": linked_document_id,
    }


async def update_correction(*, custom_id: str, title: str, note: str) -> dict[str, Any]:
    clean_note = note.strip()
    if not clean_note:
        raise ValueError("Note text is required.")

    existing = await _find_correction(custom_id)
    if existing is None:
        raise LookupError("Correction not found.")

    created_at = existing["createdAt"] or _now_iso()
    updated_at = _now_iso()
    metadata = _write_metadata(
        title=title,
        note=clean_note,
        created_at=created_at,
        updated_at=updated_at,
        linked_document_id=existing.get("linkedDocumentId"),
    )
    result = await supermemory.add_text_document(
        content=render_document_body(clean_note, created_at),
        custom_id=custom_id,
        metadata=metadata,
    )
    supermemory.invalidate_document_cache()
    return {
        "id": str(result.get("id") or existing["id"]),
        "customId": custom_id,
        "title": str(metadata["title"]),
        "note": clean_note,
        "createdAt": created_at,
        "updatedAt": updated_at,
        "linkedDocumentId": existing.get("linkedDocumentId"),
    }


async def delete_correction(*, custom_id: str, document_id: str) -> None:
    if document_id and await supermemory.delete_document(document_id):
        return

    existing = await _find_correction(custom_id)
    if existing is None:
        return
    await supermemory.add_text_document(
        content=render_document_body(existing["note"], existing["createdAt"]),
        custom_id=custom_id,
        metadata=_write_metadata(
            title=existing["title"],
            note=existing["note"],
            created_at=existing["createdAt"],
            updated_at=_now_iso(),
            linked_document_id=existing.get("linkedDocumentId"),
            active=False,
        ),
    )
    supermemory.invalidate_document_cache()
