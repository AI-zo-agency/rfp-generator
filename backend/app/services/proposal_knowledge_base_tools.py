"""Retrieval helpers: Supermemory for knowledge base, local disk for active RFPs."""

import asyncio
import logging
import re
from typing import Any

from app.services import llm, supermemory
from app.services.rfp_content import combine_rfp_text, load_local_rfp_text
from app.services.rfp_repository import get_rfp

logger = logging.getLogger(__name__)

SEARCH_CHARACTER_LIMIT = 500_000
PROPOSAL_KB_SEARCH_LIMIT = 50
PROPOSAL_BUCKET_CHAR_LIMITS = {
    "zo_voice": 500_000,
    "company": 500_000,
    "bios": 500_000,
    "case_studies": 500_000,
}


PROPOSAL_KB_BUCKETS = ("zo_voice", "company", "bios", "case_studies")

# Referenced in prompts — retrieval itself is search-driven, not hardcoded to this file.
MASTER_TEAM_ROSTER_DOC = "02_MasterTemplate_OrgStructure_AllTeamBios.pdf"
MASTER_TEAM_ROSTER_CHAR_LIMIT = 500_000

PROPOSAL_QUERY_PLANNER_PROMPT = """You plan targeted Supermemory knowledge-base searches for zö agency proposal Sections 1–3.
Given the RFP excerpt, return 10–14 specific queries to retrieve:
- zö brand voice / proposal writing tone (zoVoiceQueries)
- company overview, certifications, insurance, org facts for Section 1 (companyQueries)
- team bios 04_Bio_ and roles the RFP requires (bioQueries)
- master team roster 02_MasterTemplate_OrgStructure_AllTeamBios.pdf for org structure (bioQueries)
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
    """Search Supermemory and return full indexed documents (not single chunks)."""
    filters: dict[str, Any] | None = None
    if category:
        filters = {
            "AND": [
                *supermemory.KNOWLEDGE_BASE_SEARCH_FILTERS["AND"],
                {"key": "category", "value": category},
            ]
        }
    return await search_and_fetch_full(
        query,
        limit=limit,
        max_chars=max_chars or SEARCH_CHARACTER_LIMIT,
        filters=filters,
    )


async def search_and_fetch_full(
    query: str,
    *,
    limit: int = PROPOSAL_KB_SEARCH_LIMIT,
    max_chars: int = SEARCH_CHARACTER_LIMIT,
    filters: dict[str, Any] | None = None,
) -> tuple[str, list[str]]:
    """Run hybrid search, then load each matching document's full indexed text."""
    if not supermemory.is_configured():
        return "(Supermemory not configured.)", []

    hits = await _search_hits_all_modes(query, limit=limit, filters=filters)
    return await fetch_full_documents_for_hits(hits, max_chars=max_chars)


async def fetch_full_documents_for_hits(
    hits: list[dict[str, Any]],
    *,
    max_chars: int,
) -> tuple[str, list[str]]:
    """For each unique search hit, load the complete document via v3 GET."""
    seen_docs: set[str] = set()
    parts: list[str] = []
    sources: list[str] = []
    total = 0

    for hit in hits:
        doc_key = supermemory.document_dedupe_key(hit)
        if not doc_key or doc_key in seen_docs:
            continue
        seen_docs.add(doc_key)

        content = await supermemory.resolve_hit_document_content(hit)
        if not content:
            continue

        label = supermemory.hit_file_name(hit) or doc_key
        remaining = max_chars - total
        if remaining <= 0:
            break
        block = f"### {label}\n{content}"
        if len(block) > remaining:
            block = block[:remaining]
        parts.append(block)
        sources.append(label)
        total += len(block)

    text = "\n\n".join(parts).strip()
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

    description, pdf_text, _, _, _, _ = load_local_rfp_text(rfp, max_chars=12_000)
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
    
    # Broad fallbacks to ensure full retrieval of all KB documents
    extras["case_studies"].extend([
        "zö agency case studies 03_CS_",
        "zö agency past completed projects case study",
    ])
    extras["bios"].extend([
        f"zö agency team bios {MASTER_TEAM_ROSTER_DOC} 04_Bio_",
        "zö agency team member professional resume 04_Bio_",
    ])

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


async def _search_hits_all_modes(
    query: str,
    *,
    limit: int,
    filters: dict[str, Any] | None = None,
    threshold: float = 0.45,
) -> list[dict[str, Any]]:
    """v4 hybrid (memories) + documents (chunks). Memories first; chunks fill gaps."""
    import asyncio

    active_filters = filters or supermemory.KNOWLEDGE_BASE_SEARCH_FILTERS

    async def _hybrid() -> list[dict[str, Any]]:
        try:
            return await supermemory.search_documents(
                query=query,
                limit=limit,
                include_full_docs=True,
                search_mode="hybrid",
                filters=active_filters,
                threshold=threshold,
            )
        except supermemory.SupermemoryError:
            return []

    async def _chunks() -> list[dict[str, Any]]:
        try:
            return await supermemory.search_document_chunks(
                query=query,
                limit=limit,
                filters=active_filters,
                threshold=threshold,
            )
        except supermemory.SupermemoryError:
            return []

    memory_hits, chunk_hits = await asyncio.gather(_hybrid(), _chunks())
    memory_hits = [h for h in memory_hits if supermemory.is_knowledge_base_hit(h)]
    chunk_hits = [h for h in chunk_hits if supermemory.is_knowledge_base_hit(h)]
    return supermemory.merge_memory_and_chunk_hits(memory_hits, chunk_hits)


async def _search_hits(query: str) -> list[dict[str, Any]]:
    return await _search_hits_all_modes(query, limit=PROPOSAL_KB_SEARCH_LIMIT)


def _merge_hits(hits_by_query: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    merged: list[dict[str, Any]] = []
    for hits in hits_by_query:
        for hit in hits:
            key = supermemory.document_dedupe_key(hit) or str(hit.get("id") or id(hit))
            if key in seen:
                continue
            seen.add(key)
            merged.append(hit)
    return merged


async def fetch_case_study_candidates_jit(
    *,
    rfp_client: str,
    rfp_sector: str,
    rfp_context: str = "",
    max_chars: int = 400_000,
) -> tuple[str, list[str]]:
    """JIT case-study index for Evidence Selection — no bulk upfront retrieval."""
    # Sector/service cues only — NEVER the current RFP client (that is not past work).
    sector = (rfp_sector or "government").strip()
    queries = [
        f"03_CS_ {sector} case study project outcomes",
        f"06_WON_ {sector} proposal past performance",
        "03_CS_ government municipal digital campaign results",
        "03_CS_ state agency media outdoor recreation campaign",
    ]
    del rfp_client, rfp_context, max_chars

    seen_sources: set[str] = set()
    parts: list[str] = []
    sources: list[str] = []
    total = 0
    char_cap = 80_000

    for i, query in enumerate(queries, 1):
        logger.info(
            "  └─ [Evidence Selection] JIT query %d/%d: %s",
            i,
            len(queries),
            query[:100],
        )
        text, srcs = await search_knowledge_base(query, limit=4, max_chars=40_000)
        if not text.strip():
            continue
        for src in srcs:
            if src not in seen_sources:
                seen_sources.add(src)
                sources.append(src)
        remaining = char_cap - total
        if remaining <= 0:
            break
        block = text[:remaining]
        parts.append(block)
        total += len(block)

    return "\n\n".join(parts), sources


async def fetch_master_team_roster(
    *,
    rfp_client: str = "",
    rfp_sector: str = "",
    rfp_context: str = "",
) -> tuple[str, list[str]]:
    """Fetch the exact Master Team Roster document used for team strategy."""
    del rfp_client, rfp_sector, rfp_context

    try:
        document = await supermemory.find_document_by_file_name(MASTER_TEAM_ROSTER_DOC)
        if document:
            custom_id = supermemory.document_fetch_key(document)
            if custom_id:
                content = await supermemory.get_document_content(custom_id=custom_id)
                if content.strip():
                    logger.info(
                        "  └─ [Team Selection] exact roster: %s (%d chars)",
                        MASTER_TEAM_ROSTER_DOC,
                        len(content),
                    )
                    return content[:MASTER_TEAM_ROSTER_CHAR_LIMIT], [MASTER_TEAM_ROSTER_DOC]
    except supermemory.SupermemoryError as exc:
        logger.warning("Exact Master Team Roster fetch failed: %s", exc)

    # Fallback only when the exact document cannot be fetched.
    query = f"{MASTER_TEAM_ROSTER_DOC} organizational structure team roster"
    logger.info("  └─ [Team Selection] fallback roster query: %s", query)
    return await search_and_fetch_full(query, limit=4, max_chars=MASTER_TEAM_ROSTER_CHAR_LIMIT)


async def _gather_bucket(
    bucket: str,
    queries: list[str],
) -> tuple[str, list[str]]:
    """Fetch Supermemory results for all queries in a bucket sequentially (one-by-one),
    then merge unique hits. The 4 buckets themselves run in parallel via asyncio.gather."""
    if not queries:
        return "(No queries for this bucket.)", []
    all_hits: list[list[dict[str, Any]]] = []
    for i, query in enumerate(queries, 1):
        from app.services.proposal_generation_cancel import check_cancelled_for_active

        await check_cancelled_for_active()
        logger.info(
            "  └─ [Knowledge Base Retriever] [%s] query %d/%d: %s", bucket, i, len(queries), query[:80]
        )
        hits = await _search_hits(query)  # One at a time — no flooding
        all_hits.append(hits)
    hits = _merge_hits(all_hits)
    logger.info("  [%s] merged %d unique hits", bucket, len(hits))
    return await fetch_full_documents_for_hits(
        hits,
        max_chars=PROPOSAL_BUCKET_CHAR_LIMITS[bucket],
    )


async def gather_proposal_kb_for_sections(
    *,
    rfp_title: str,
    rfp_client: str,
    rfp_sector: str,
    rfp_location: str | None,
    rfp_context: str,
    skip_company: bool = False,
) -> dict[str, tuple[str, list[str]]]:
    """Run targeted Supermemory queries grouped for Sections 1–3.

    When skip_company=True (Company Qualification S1 path), the company bucket is
    omitted — Section 1 uses JIT Company Truth retrieval instead.
    """
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

    active_buckets = (
        ("zo_voice", "bios", "case_studies")
        if skip_company
        else PROPOSAL_KB_BUCKETS
    )

    bucket_queries: dict[str, list[str]] = {}
    total_queries = 0
    for bucket in active_buckets:
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
        "Proposal KB search for %s / %s: %d queries across %d buckets%s",
        rfp_client,
        rfp_sector,
        total_queries,
        len(active_buckets),
        " (company skipped — CQ S1 JIT)" if skip_company else "",
    )

    # Buckets run in parallel; within each bucket queries run sequentially.
    logger.info(
        "Gathering %d KB buckets in parallel (per-bucket queries are sequential)...",
        len(active_buckets),
    )
    if skip_company:
        zo_voice, bios, case_studies = await asyncio.gather(
            _gather_bucket("zo_voice", bucket_queries["zo_voice"]),
            _gather_bucket("bios", bucket_queries["bios"]),
            _gather_bucket("case_studies", bucket_queries["case_studies"]),
        )
        company = ("", [])
    else:
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


def _is_case_study_source(file_name: str) -> bool:
    lowered = file_name.strip().casefold()
    return lowered.startswith("03_cs") or "03_cs_" in lowered or "case study" in lowered


async def search_evidence_candidate_index(
    *,
    rfp_client: str,
    rfp_sector: str,
    rfp_context: str = "",
    limit_per_query: int = 5,
) -> list[dict[str, str]]:
    """Lightweight evidence index — search hit titles/snippets only, no full doc fetch."""
    from app.services.company_qualification.schemas import EvidenceCandidate

    if not supermemory.is_configured():
        return []

    # Sector cues only — never current client name or raw RFP title (not past performance).
    sector = (rfp_sector or "government").strip()
    queries = [
        f"03_CS_ {sector} case study project outcomes",
        "03_CS_ government municipal digital campaign results",
        "03_CS_ state agency media outdoor recreation campaign",
        f"03_CS_ {sector} past performance outcomes",
    ]
    del rfp_client, rfp_context

    seen_titles: set[str] = set()
    candidates: list[dict[str, str]] = []

    for i, query in enumerate(queries, 1):
        logger.info(
            "  └─ [Evidence Selection] index query %d/%d: %s",
            i,
            len(queries),
            query[:100],
        )
        try:
            hits = await supermemory.search_documents(
                query=query,
                limit=limit_per_query,
                include_full_docs=False,
                search_mode="hybrid",
                filters=supermemory.KNOWLEDGE_BASE_SEARCH_FILTERS,
            )
        except supermemory.SupermemoryError:
            continue

        for hit in hits:
            if not supermemory.is_knowledge_base_hit(hit):
                continue
            title = supermemory.hit_file_name(hit).strip()
            if not title or not _is_case_study_source(title):
                continue
            key = title.casefold()
            if key in seen_titles:
                continue
            seen_titles.add(key)
            snippet = supermemory.hit_text(hit)[:500]
            candidates.append(
                EvidenceCandidate(title=title, snippet=snippet, source=title).model_dump(
                    by_alias=True
                )
            )

    logger.info("Evidence candidate index: %d unique case studies", len(candidates))
    return candidates


async def fetch_single_case_study(
    study_title: str,
    *,
    max_chars: int = 120_000,
) -> tuple[str, list[str]]:
    """JIT full retrieval for one selected case study."""
    query = f"03_CS_ {study_title}"
    logger.info("  └─ [Case Study Builder] fetching: %s", study_title[:80])
    return await search_and_fetch_full(query, max_chars=max_chars)
