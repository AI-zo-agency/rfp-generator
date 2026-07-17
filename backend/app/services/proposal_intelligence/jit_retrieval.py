"""Phase 3 just-in-time retrieval from the Phase 2 retrievalPlan."""

from __future__ import annotations

import logging
from typing import Any

from app.models.proposal import EvidenceItem
from app.services import supermemory
from app.services.proposal_intelligence.schemas import RetrievalEntry

logger = logging.getLogger(__name__)


def _hit_label(hit: dict[str, Any]) -> str:
    metadata = hit.get("metadata") if isinstance(hit.get("metadata"), dict) else {}
    return str(
        hit.get("customId")
        or metadata.get("fileName")
        or metadata.get("title")
        or hit.get("title")
        or hit.get("id")
        or "document"
    )


def _hit_excerpt(hit: dict[str, Any], *, max_chars: int = 2000) -> str:
    content = (
        hit.get("content")
        or hit.get("memory")
        or hit.get("chunk")
        or hit.get("text")
        or hit.get("summary")
        or ""
    )
    text = str(content).strip()
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}…"


async def retrieve_for_section(
    entry: RetrievalEntry,
    *,
    rfp_client: str = "",
    start_index: int = 1,
) -> list[EvidenceItem]:
    """Retrieve writing assets for one section using the planned queries."""
    if not supermemory.is_configured():
        return []

    items: list[EvidenceItem] = []
    seen: set[str] = set()
    counter = start_index

    queries = list(entry.queries) or [
        f"zö agency {rfp_client} {' '.join(entry.required_assets)}".strip()
    ]
    for query in queries[:5]:
        try:
            hits = await supermemory.search_documents(
                query=query[:220],
                limit=6,
                include_full_docs=True,
                filters=supermemory.KNOWLEDGE_BASE_SEARCH_FILTERS,
            )
        except supermemory.SupermemoryError as exc:
            logger.warning("JIT retrieval failed for %s: %s", entry.section_id, exc)
            continue

        for hit in hits:
            if not supermemory.is_knowledge_base_hit(hit):
                continue
            label = _hit_label(hit)
            key = str(hit.get("id") or hit.get("customId") or label)
            if key in seen:
                continue
            seen.add(key)
            excerpt = _hit_excerpt(hit)
            if not excerpt:
                continue
            items.append(
                EvidenceItem(
                    id=f"E{counter}",
                    source=label,
                    excerpt=excerpt,
                    sectionIds=[entry.section_id],
                    chunkKey=key,
                )
            )
            counter += 1
            if len(items) >= 12:
                return items
    return items
