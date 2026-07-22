"""Load 01_ClientList_Approved.md from Supermemory (cached per process)."""

from __future__ import annotations

import logging
import time
from typing import Any

from app.services.evidence_trust.client_list import (
    ClientListRegistry,
    parse_client_list_markdown,
)

logger = logging.getLogger(__name__)

_CLIENT_LIST_FILENAMES = (
    "01_ClientList_Approved.md",
    "01_ClientList_Approved",
)

_cache: ClientListRegistry | None = None
_cache_at: float = 0.0
_CACHE_TTL_SEC = 600.0


def clear_client_list_cache() -> None:
    global _cache, _cache_at
    _cache = None
    _cache_at = 0.0


async def load_client_list_registry(
    *,
    force_refresh: bool = False,
    fallback_markdown: str | None = None,
) -> ClientListRegistry:
    """Fetch and parse ClientList; optional fixture markdown for tests/offline."""
    global _cache, _cache_at
    now = time.monotonic()
    if (
        not force_refresh
        and _cache is not None
        and (now - _cache_at) < _CACHE_TTL_SEC
    ):
        return _cache

    text = ""
    try:
        from app.services import supermemory

        if supermemory.is_configured():
            for name in _CLIENT_LIST_FILENAMES:
                doc = await supermemory.find_document_by_file_name(name)
                if not doc and name.endswith(".md"):
                    continue
                if not doc:
                    # try with .md
                    doc = await supermemory.find_document_by_file_name(
                        name if name.endswith(".md") else f"{name}.md"
                    )
                if not doc:
                    continue
                text = await supermemory.get_document_content(
                    document_id=str(doc.get("id") or "")
                )
                if not text and doc.get("customId"):
                    text = await supermemory.get_document_content(
                        custom_id=str(doc.get("customId"))
                    )
                if text:
                    break
            if not text:
                # search fallback — stitch chunks
                hits = await supermemory.search_documents(
                    query="01_ClientList_Approved Public Work Type",
                    limit=20,
                    include_full_docs=True,
                    filters=supermemory.KNOWLEDGE_BASE_SEARCH_FILTERS,
                )
                parts: list[str] = []
                for hit in hits:
                    meta = hit.get("metadata") if isinstance(hit.get("metadata"), dict) else {}
                    fname = str(meta.get("fileName") or hit.get("title") or "")
                    if "clientlist" not in fname.casefold() and "client list" not in fname.casefold():
                        # still take if content looks like the table
                        body = supermemory.hit_text(hit)
                        if "| Client |" not in body and "Public |" not in body:
                            continue
                    parts.append(supermemory.hit_text(hit))
                text = "\n\n".join(parts)
    except Exception as exc:
        logger.warning("ClientList load from Supermemory failed: %s", exc)

    if not text and fallback_markdown:
        text = fallback_markdown

    registry = parse_client_list_markdown(text) if text else ClientListRegistry()
    if not registry.entries:
        logger.warning("ClientList registry empty — gates will be permissive on unknown only")
    else:
        logger.info("ClientList loaded: %d clients", len(registry.entries))
    _cache = registry
    _cache_at = now
    return registry


def registry_from_markdown(text: str) -> ClientListRegistry:
    """Sync helper for tests."""
    return parse_client_list_markdown(text)


def hit_as_dict(
    *,
    source: str,
    excerpt: str,
    title: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    hit: dict[str, Any] = {
        "source": source,
        "title": title or source,
        "excerpt": excerpt,
        "content": excerpt,
        "metadata": {"fileName": source},
    }
    if extra:
        hit.update(extra)
    return hit
