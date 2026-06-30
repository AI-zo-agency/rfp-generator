"""Targeted KB gap-fill before Phase 3 — mirrors section-improve retrieval strength."""

from __future__ import annotations

import asyncio
import logging
import re

from app.models.proposal import EvidenceItem, RfpSectionMap
from app.services import supermemory
from app.services.proposal_evidence_corpus import merge_hits_into_corpus
from app.services.proposal_retrieval_graph import (
    EXCERPT_MAX_CHARS,
    MAX_CONCURRENT_SUPERMEMORY_SEARCHES,
    SEARCH_LIMIT,
    _hit_excerpt,
    _hit_key,
    _hit_label,
)

logger = logging.getLogger(__name__)

_GAP_TOPICS: list[tuple[re.Pattern[str], list[str]]] = [
    (
        re.compile(r"\b(writing\s+sample|portfolio|design\s+sample|work\s+sample)\b", re.I),
        [
            "zö agency 03_CS portfolio writing samples case studies graphic design",
            "zö agency content samples blog newsletter design examples",
        ],
    ),
    (
        re.compile(r"\b(reference|contact\s+person|telephone|email\s+address)\b", re.I),
        [
            "zö agency client references government contact name title email phone",
            "zö agency 06_WON 07_FIN reference letters client contacts",
        ],
    ),
    (
        re.compile(r"\b(certif|WBENC|WOSB|BRC|insurance|bond)\b", re.I),
        [
            "zö agency certifications WBENC WOSB insurance bonding BRC",
            "zö agency 02 master template certifications compliance",
        ],
    ),
    (
        re.compile(r"\b(organi[sz]ational\s+chart|employee|staff|personnel|team)\b", re.I),
        [
            "zö agency organizational chart employee count team structure",
            "zö agency 04 bio team roster personnel",
        ],
    ),
    (
        re.compile(r"\b(office\s+location|headquarters|regional|on[\s-]*site)\b", re.I),
        [
            "zö agency office location headquarters Bend Oregon regional presence",
            "zö agency facilities operating office team locations",
        ],
    ),
    (
        re.compile(r"\b(hourly\s+rate|lump\s*sum|budget|fee|pricing)\b", re.I),
        [
            "00_Guide_Pricing tier Low Average High personnel loading lump sum",
            "zö agency 07_FIN burdened hourly rates fee schedule",
        ],
    ),
]


async def _search_hits(query: str) -> list[dict]:
    if not supermemory.is_configured():
        return []
    try:
        hits = await supermemory.search_documents(
            query=query[:240],
            limit=SEARCH_LIMIT,
            include_full_docs=True,
            filters=supermemory.KNOWLEDGE_BASE_SEARCH_FILTERS,
        )
        return [h for h in hits if supermemory.is_knowledge_base_hit(h)]
    except supermemory.SupermemoryError:
        return []


_search_sem = asyncio.Semaphore(MAX_CONCURRENT_SUPERMEMORY_SEARCHES)


async def _search_throttled(query: str) -> list[dict]:
    async with _search_sem:
        return await _search_hits(query)


def _queries_for_section(
    section: RfpSectionMap,
    *,
    client: str,
    sector: str,
) -> list[str]:
    queries: list[str] = []
    req_blob = " ".join(section.requirements or [])
    uncovered = " ".join(section.uncovered_requirements or [])
    combined = f"{section.title} {req_blob} {uncovered}"

    for pattern, templates in _GAP_TOPICS:
        if pattern.search(combined):
            for template in templates:
                queries.append(f"{template} {client} {sector}"[:240])

    for req in (section.uncovered_requirements or section.requirements or [])[:3]:
        snippet = str(req).strip()[:120]
        if snippet:
            queries.append(f"zö agency {snippet} {client} {sector}"[:240])

    focus = section.retrieval_focus or []
    if focus:
        queries.append(
            f"zö agency {client} {sector} {section.title} {' '.join(str(f) for f in focus[:3])}"[:240]
        )

    seen: set[str] = set()
    unique: list[str] = []
    for query in queries:
        key = query.lower()
        if key not in seen:
            seen.add(key)
            unique.append(query)
    return unique[:5]


def _merge_hits(
    corpus: list[EvidenceItem],
    hits: list[dict],
    section_id: str,
) -> list[EvidenceItem]:
    return merge_hits_into_corpus(
        corpus,
        hits,
        section_id,
        hit_key=_hit_key,
        hit_label=_hit_label,
        hit_excerpt=_hit_excerpt,
        excerpt_max_chars=EXCERPT_MAX_CHARS,
    )


async def gap_fill_evidence_for_sections(
    *,
    rfp_sections: list[RfpSectionMap],
    evidence_corpus: list[EvidenceItem],
    section_queries: dict[str, list[str]],
    rfp_client: str,
    rfp_sector: str,
    coverage_threshold: int = 85,
) -> tuple[list[EvidenceItem], dict[str, list[str]], list[RfpSectionMap]]:
    """Extra retrieval pass for low-coverage sections before Phase 3 drafting."""
    if not supermemory.is_configured():
        return evidence_corpus, section_queries, rfp_sections

    targets = [
        s
        for s in rfp_sections
        if (s.coverage_percent or 0) < coverage_threshold
        or (s.uncovered_requirements or [])
    ]
    if not targets:
        return evidence_corpus, section_queries, rfp_sections

    corpus = list(evidence_corpus)
    queries_map = dict(section_queries)
    added_queries = 0
    added_hits = 0

    for section in targets:
        planned = _queries_for_section(section, client=rfp_client, sector=rfp_sector)
        prior = queries_map.get(section.id, [])
        new_queries = [q for q in planned if q.lower() not in {p.lower() for p in prior}]
        if not new_queries:
            continue

        tasks = [_search_throttled(q) for q in new_queries]
        results = await asyncio.gather(*tasks)
        for query, hits in zip(new_queries, results):
            if hits:
                corpus = _merge_hits(corpus, hits, section.id)
                added_hits += len(hits)
            added_queries += 1

        queries_map[section.id] = [*prior, *new_queries]

    logger.info(
        "Gap-fill retrieval: %d sections, %d new queries, %d hit merges, corpus=%d",
        len(targets),
        added_queries,
        added_hits,
        len(corpus),
    )
    return corpus, queries_map, rfp_sections
