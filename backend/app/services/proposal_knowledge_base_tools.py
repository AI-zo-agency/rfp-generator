"""Retrieval helpers: Supermemory for knowledge base, local disk for active RFPs."""

import asyncio
import logging
import re
from typing import Any

from app.services import llm, supermemory
from app.services.rfp_content import combine_rfp_text, load_local_rfp_text
from app.services.rfp_repository import get_rfp

logger = logging.getLogger(__name__)

SEARCH_CHARACTER_LIMIT = 6_000
PROPOSAL_KB_SEARCH_LIMIT = 8
PROPOSAL_BUCKET_CHAR_LIMITS = {
    "zo_voice": 8_000,
    "company": 14_000,
    "bios": 14_000,
    "case_studies": 16_000,
}

PROPOSAL_KB_BUCKETS = ("zo_voice", "company", "bios", "case_studies")

PROPOSAL_QUERY_PLANNER_PROMPT = """You plan targeted Supermemory knowledge-base searches for zö agency proposal Sections 1–3.
Given the RFP excerpt, return 10–14 specific queries to retrieve:
- zö brand voice / proposal writing tone (zoVoiceQueries)
- company overview, certifications, insurance, org facts for Section 1 (companyQueries)
- team bios 04_Bio_ and roles the RFP requires (bioQueries)
- case studies 03_CS_ and won proposals 06_ matching sector/client/scope (caseStudyQueries)

Use client name, location, sector, and specific deliverables from the RFP in queries.
Do NOT include HTML or portal boilerplate in queries.

Return ONLY JSON:
{
  "zoVoiceQueries": ["query 1"],
  "companyQueries": ["query 1"],
  "bioQueries": ["query 1"],
  "caseStudyQueries": ["query 1"]
}"""


async def search_knowledge_base(
    query: str,
    *,
    limit: int = 6,
    category: str | None = None,
    max_chars: int | None = None,
) -> tuple[str, list[str]]:
    if not supermemory.is_configured():
        return "(Supermemory not configured.)", []

    filters: dict[str, Any] = dict(supermemory.KNOWLEDGE_BASE_SEARCH_FILTERS)
    if category:
        filters = {
            "AND": [
                *supermemory.KNOWLEDGE_BASE_SEARCH_FILTERS["AND"],
                {"key": "category", "value": category},
            ]
        }

    try:
        hits = await supermemory.search_documents(
            query=query,
            limit=limit,
            include_full_docs=True,
            filters=filters,
        )
        hits = [hit for hit in hits if supermemory.is_knowledge_base_hit(hit)]
    except supermemory.SupermemoryError:
        return "(Supermemory search failed.)", []

    sources: list[str] = []
    for hit in hits:
        metadata = hit.get("metadata") if isinstance(hit.get("metadata"), dict) else {}
        label = (
            hit.get("customId")
            or metadata.get("fileName")
            or metadata.get("title")
            or hit.get("id")
            or "document"
        )
        sources.append(str(label))

    text = supermemory.format_search_hits(
        hits, max_chars=max_chars or SEARCH_CHARACTER_LIMIT
    )
    return text or "(No matching knowledge-base content.)", sources


async def search_rfp_document(
    rfp_id: str,
    title: str,
    client: str,
) -> tuple[str, list[str]]:
    del title, client  # RFP text is loaded locally by id; title/client kept for tool API.

    rfp = get_rfp(rfp_id)
    if not rfp:
        return "(RFP not found.)", []

    description, pdf_text, _, _ = load_local_rfp_text(rfp, max_chars=12_000)
    text = combine_rfp_text(description, pdf_text, max_chars=12_000)
    if not text:
        return "(No local RFP PDF or description found.)", []

    return text, [f"local:rfp:{rfp_id}"]


def _unique_queries(*groups: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for group in groups:
        for query in group:
            key = query.strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            ordered.append(query.strip())
    return ordered


def _rfp_topic_queries(rfp_client: str, rfp_sector: str, rfp_context: str) -> dict[str, list[str]]:
    sample = rfp_context[:20_000]
    extras: dict[str, list[str]] = {
        "zo_voice": [],
        "company": [],
        "bios": [],
        "case_studies": [],
    }
    if rfp_client.strip():
        extras["case_studies"].append(
            f"zö agency {rfp_client} case study proposal reference"
        )
    extras["case_studies"].append(
        f"zö agency {rfp_sector} sector case studies similar clients outcomes"
    )
    if re.search(r"higher education|university|college|TBR|community college", sample, re.I):
        extras["case_studies"].append(
            "zö agency higher education university college Benedictine case study"
        )
    if re.search(r"media buying|programmatic|geo-?fencing|PPC", sample, re.I):
        extras["case_studies"].append(
            "zö agency media buying digital advertising Maricopa Oregon Employment case study"
        )
    if re.search(r"public relations|PR |crisis comm", sample, re.I):
        extras["case_studies"].append(
            "zö agency public relations crisis communications government case study"
        )
    if re.search(r"housing authority|HUD", sample, re.I):
        extras["case_studies"].append("zö agency housing authority HUD public housing")
    extras["bios"].append(
        f"zö agency team bios {rfp_sector} public sector account creative"
    )
    return extras


async def _plan_proposal_kb_queries(
    *,
    rfp_title: str,
    rfp_client: str,
    rfp_sector: str,
    rfp_location: str | None,
    rfp_excerpt: str,
) -> dict[str, list[str]]:
    messages = [
        {"role": "system", "content": PROPOSAL_QUERY_PLANNER_PROMPT},
        {
            "role": "user",
            "content": (
                f"Title: {rfp_title}\n"
                f"Client: {rfp_client}\n"
                f"Sector: {rfp_sector}\n"
                f"Location: {rfp_location or '(not provided)'}\n\n"
                f"RFP excerpt:\n{rfp_excerpt[:8000]}"
            ),
        },
    ]
    empty = {key: [] for key in PROPOSAL_KB_BUCKETS}
    try:
        raw, provider = await llm.chat_json(messages, max_tokens=1024, temperature=0.2)
        logger.info("Planned proposal KB queries via %s for %s", provider, rfp_client)
        planned: dict[str, list[str]] = {}
        for bucket, key in (
            ("zo_voice", "zoVoiceQueries"),
            ("company", "companyQueries"),
            ("bios", "bioQueries"),
            ("case_studies", "caseStudyQueries"),
        ):
            values = raw.get(key, [])
            planned[bucket] = (
                [str(query).strip() for query in values if str(query).strip()]
                if isinstance(values, list)
                else []
            )
        return planned
    except llm.LlmError as exc:
        logger.warning("Proposal KB query planning failed: %s", exc)
        return empty


async def _search_hits(query: str) -> list[dict[str, Any]]:
    try:
        hits = await supermemory.search_documents(
            query=query,
            limit=PROPOSAL_KB_SEARCH_LIMIT,
            include_full_docs=True,
            filters=supermemory.KNOWLEDGE_BASE_SEARCH_FILTERS,
        )
        return [hit for hit in hits if supermemory.is_knowledge_base_hit(hit)]
    except supermemory.SupermemoryError:
        return []


def _merge_hits(hits_by_query: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    merged: list[dict[str, Any]] = []
    for hits in hits_by_query:
        for hit in hits:
            key = str(hit.get("id") or hit.get("customId") or id(hit))
            if key in seen:
                continue
            seen.add(key)
            merged.append(hit)
    return merged


def _hits_to_bundle(
    hits: list[dict[str, Any]],
    *,
    max_chars: int,
) -> tuple[str, list[str]]:
    sources: list[str] = []
    for hit in hits:
        metadata = hit.get("metadata") if isinstance(hit.get("metadata"), dict) else {}
        label = (
            hit.get("customId")
            or metadata.get("fileName")
            or metadata.get("title")
            or hit.get("id")
            or "document"
        )
        sources.append(str(label))
    text = supermemory.format_search_hits(hits, max_chars=max_chars)
    return text or "(No matching knowledge-base content.)", sources


async def _gather_bucket(
    bucket: str,
    queries: list[str],
) -> tuple[str, list[str]]:
    if not queries:
        return "(No queries for this bucket.)", []
    results = await asyncio.gather(*(_search_hits(query) for query in queries))
    hits = _merge_hits(results)
    return _hits_to_bundle(hits, max_chars=PROPOSAL_BUCKET_CHAR_LIMITS[bucket])


async def gather_proposal_kb_for_sections(
    *,
    rfp_title: str,
    rfp_client: str,
    rfp_sector: str,
    rfp_location: str | None,
    rfp_context: str,
) -> dict[str, tuple[str, list[str]]]:
    """Run 18–25 targeted Supermemory queries grouped for Sections 1–3."""
    if not supermemory.is_configured():
        empty = "(Supermemory not configured.)", []
        return {key: empty for key in PROPOSAL_KB_BUCKETS}

    planned = await _plan_proposal_kb_queries(
        rfp_title=rfp_title,
        rfp_client=rfp_client,
        rfp_sector=rfp_sector,
        rfp_location=rfp_location,
        rfp_excerpt=rfp_context,
    )
    topic = _rfp_topic_queries(rfp_client, rfp_sector, rfp_context)

    bucket_queries: dict[str, list[str]] = {}
    total_queries = 0
    for bucket in PROPOSAL_KB_BUCKETS:
        merged = _unique_queries(planned.get(bucket, []), topic.get(bucket, []))
        if not merged:
            merged = [
                (
                    f"zö agency {rfp_client} {rfp_sector} "
                    f"{bucket.replace('_', ' ')} {rfp_title[:80]}"
                ).strip()
            ]
        bucket_queries[bucket] = merged
        total_queries += len(bucket_queries[bucket])

    logger.info(
        "Proposal KB search for %s / %s: %d queries across 4 buckets",
        rfp_client,
        rfp_sector,
        total_queries,
    )

    zo_voice, company, bios, case_studies = await asyncio.gather(
        _gather_bucket("zo_voice", bucket_queries["zo_voice"]),
        _gather_bucket("company", bucket_queries["company"]),
        _gather_bucket("bios", bucket_queries["bios"]),
        _gather_bucket("case_studies", bucket_queries["case_studies"]),
    )

    logger.info(
        "Proposal KB gathered for %s: voice=%d co=%d bio=%d cs=%d chars",
        rfp_client,
        len(zo_voice[0]),
        len(company[0]),
        len(bios[0]),
        len(case_studies[0]),
    )

    return {
        "zo_voice": zo_voice,
        "company": company,
        "bios": bios,
        "case_studies": case_studies,
    }
