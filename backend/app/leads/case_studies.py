"""Wave 3 phase 6 — 1–3 KB case studies for conversation prep, never auto-sent."""

from __future__ import annotations

import logging
from typing import Any

from app.services import supermemory
from app.services.proposal_blocker_prevention import clean_case_study_label
from app.services.proposal_knowledge_base_tools import _is_case_study_source

logger = logging.getLogger(__name__)

MAX_MATCHES = 3

# ponytail: process-local cache. A PoC re-opens the same industry constantly.
_CACHE: dict[str, list[str]] = {}


def titles_from_hits(hits: list[dict[str, Any]], *, limit: int = MAX_MATCHES) -> list[str]:
    """Keep unique eligible 03_CS_ documents; drop master dumps and non-studies."""
    titles: list[str] = []
    seen: set[str] = set()
    for hit in hits:
        source = supermemory.hit_file_name(hit).strip()
        if not source or not _is_case_study_source(source):
            continue
        name = clean_case_study_label(source, index=None)
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        titles.append(name)
        if len(titles) >= limit:
            break
    return titles


async def find_case_studies(industry: str | None) -> list[str] | None:
    """Search Supermemory for case studies matching the contact's industry.

    Returns None when Supermemory is unavailable so the caller can keep the
    fixture. Returns [] when the search ran and found nothing.
    """
    if not supermemory.is_configured():
        return None
    sector = (industry or "").strip()
    if not sector:
        return []
    if sector in _CACHE:
        return _CACHE[sector]

    # Prefixing "zö agency" makes hybrid rank companyfacts / MasterTemplate
    # memories over 03_CS_ PDFs — those docs mention the agency on every page.
    query = f"03_CS_ {sector} case study"
    try:
        hits = await supermemory.search_hybrid(
            query=query,
            limit=8,
            filters=supermemory.KNOWLEDGE_BASE_SEARCH_FILTERS,
        )
    except supermemory.SupermemoryError as exc:
        logger.warning("Lead case-study search failed industry=%s: %s", sector, exc)
        return None

    kb_hits = [hit for hit in hits if supermemory.is_knowledge_base_hit(hit)]
    sources = [supermemory.hit_file_name(hit) for hit in kb_hits]
    titles = titles_from_hits(kb_hits)
    logger.info(
        "Lead case-study search industry=%s query=%r hits=%d sources=%s matched=%s",
        sector,
        query,
        len(kb_hits),
        sources,
        titles,
    )
    _CACHE[sector] = titles
    return titles
