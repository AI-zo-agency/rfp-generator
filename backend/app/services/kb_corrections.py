"""Agency-wide standing corrections.

A correction is a human statement that supersedes what the knowledge base
says — "Ron Comer has retired", "Ella Lindau is now Director of Operations".
Corrections live in Supermemory as ordinary documents (no local store) but are
read deterministically by listing the container, never by RAG search, so an
agent always sees all of them.
"""

from __future__ import annotations

import logging
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
