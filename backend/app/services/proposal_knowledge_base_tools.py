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
The KB contains ONLY zö agency materials (company facts, bios, case studies, pricing guide) —
it does NOT contain the RFP buyer / prospect. Never search as if the buyer lives in the KB.

Given the RFP excerpt, return 10–14 specific queries to retrieve:
- zö brand voice / proposal writing tone (zoVoiceQueries)
- company overview, certifications, insurance, org facts for Section 1 (companyQueries)
- team bios 04_Bio_ and roles the RFP requires (bioQueries)
- master team roster 02_MasterTemplate_OrgStructure_AllTeamBios.pdf for org structure (bioQueries)
- case studies 03_CS_ and won proposals 06_ matching sector/scope themes (caseStudyQueries)

Frame every query around zö capabilities + RFP theme (sector, deliverables, audience type).
Do NOT use the RFP client/buyer name as the search subject. Do NOT include HTML or portal boilerplate.

Return ONLY JSON:
{
  "zoVoiceQueries": ["query 1"],
  "companyQueries": ["query 1"],
  "bioQueries": ["query 1"],
  "caseStudyQueries": ["query 1"]
}"""


_CLIENT_ORG_STOPWORDS = frozenset(
    {
        "the",
        "and",
        "of",
        "for",
        "inc",
        "llc",
        "ltd",
        "corp",
        "co",
        "college",
        "university",
        "community",
        "valley",
        "state",
        "city",
        "county",
        "district",
        "board",
        "agency",
        "department",
        "office",
        "school",
        "schools",
        "public",
        "maine",
        "oregon",
        "florida",
        "california",
        "texas",
        "new",
        "york",
    }
)


def _significant_tokens(text: str) -> list[str]:
    return [
        t
        for t in re.split(r"\W+", (text or "").casefold())
        if len(t) > 2 and t not in _CLIENT_ORG_STOPWORDS
    ]


def normalize_zo_kb_query(
    query: str,
    *,
    rfp_client: str = "",
    rfp_sector: str = "",
    rfp_title: str = "",
) -> str:
    """Rewrite KB search queries so they target zö materials, not the RFP buyer.

    Supermemory holds zö companyfacts / bios / case studies / pricing guide.
    Queries like "KVCC … community college marketing" waste tokens — strip buyer
    subject and reframe as zö capability + sector/theme.
    """
    raw = (query or "").strip()
    if not raw:
        theme = (rfp_sector or "marketing communications").strip()
        return f"zö agency {theme} company facts case studies capabilities"[:220]

    lower = raw.casefold()
    # Pricing guide: keep ONLY guide + pricing vocabulary — never buyer/RFP title.
    if "00_guide_pricing" in lower or "00_guide" in lower:
        return sanitize_pricing_guide_query(
            raw, rfp_client=rfp_client, rfp_title=rfp_title
        )

    if re.search(r"\b(01[_ ]?companyfacts|02[_ ]?master|03[_ ]?cs|04[_ ]?bio)\b", lower):
        stripped = _strip_buyer_phrases(
            raw, rfp_client=rfp_client, rfp_title=rfp_title
        )
        if "zö" not in stripped.casefold() and "zo agency" not in stripped.casefold():
            stripped = f"zö agency {stripped}"
        return stripped[:240]

    client = (rfp_client or "").strip()
    client_tokens = set(_significant_tokens(client))
    title_tokens = set(_significant_tokens(rfp_title))
    buyer_tokens = client_tokens | title_tokens
    q_tokens = _significant_tokens(raw)

    topical = _strip_buyer_phrases(raw, rfp_client=rfp_client, rfp_title=rfp_title)

    # Buyer-as-subject: first tokens heavily overlap the RFP client/title.
    buyer_as_subject = False
    if buyer_tokens and q_tokens:
        head = q_tokens[: min(5, len(q_tokens))]
        overlap = sum(1 for t in head if t in buyer_tokens)
        if overlap >= 1 and (
            q_tokens[0] in buyer_tokens or overlap >= 2 or len(topical) < 18
        ):
            buyer_as_subject = True

    if buyer_as_subject or len(topical) < 12:
        leftover = " ".join(t for t in q_tokens if t not in buyer_tokens)
        theme_bits: list[str] = []
        seen: set[str] = set()
        for bit in (
            (rfp_sector or "").strip(),
            leftover[:80],
            "marketing communications capabilities case studies references",
        ):
            for word in bit.split():
                key = word.casefold()
                if key in seen:
                    continue
                seen.add(key)
                theme_bits.append(word)
        topical = " ".join(theme_bits).strip()

    if "zö" not in topical.casefold() and "zo agency" not in topical.casefold():
        topical = f"zö agency {topical}"

    # Drop leftover bare client acronyms still sitting as the first word.
    if buyer_tokens:
        first = _significant_tokens(topical)
        if first and first[0] in buyer_tokens:
            topical = re.sub(
                rf"^\s*(?:zö agency\s+)?{re.escape(first[0])}\b",
                "zö agency",
                topical,
                count=1,
                flags=re.IGNORECASE,
            )
            topical = re.sub(r"\s+", " ", topical).strip()
            if not topical.casefold().startswith("zö") and not topical.casefold().startswith(
                "zo "
            ):
                topical = f"zö agency {topical}"

    normalized = topical[:220].strip()
    if normalized.casefold() != raw.casefold():
        logger.info(
            "KB query normalized (buyer→zö): %r → %r",
            raw[:120],
            normalized[:120],
        )
    return normalized or f"zö agency {(rfp_sector or 'capabilities').strip()}"[:220]


_DEFAULT_PRICING_QUERY = (
    "00_Guide_Pricing tier ranges Low Average High discovery strategy "
    "content digital media project management fees"
)


def _strip_buyer_phrases(
    text: str,
    *,
    rfp_client: str = "",
    rfp_title: str = "",
) -> str:
    out = text or ""
    for phrase in (rfp_client, rfp_title):
        phrase = (phrase or "").strip()
        if len(phrase) >= 3:
            out = re.sub(re.escape(phrase), " ", out, flags=re.IGNORECASE)
    buyer_tokens = set(_significant_tokens(rfp_client)) | set(
        _significant_tokens(rfp_title)
    )
    for tok in sorted(buyer_tokens, key=len, reverse=True):
        out = re.sub(rf"\b{re.escape(tok)}\b", " ", out, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", out).strip(" -–,")


def sanitize_pricing_guide_query(
    topic: str,
    *,
    rfp_client: str = "",
    rfp_title: str = "",
) -> str:
    """Keep the agent's pricing query; only strip RFP buyer/title contamination.

    Do not whitelist-tokenize the query — that drops guide line numbers and
    phrases (e.g. 'short projects', '5-8 percent') and hurts retrieval.
    """
    raw = (topic or "").strip()
    cleaned = _strip_buyer_phrases(
        raw, rfp_client=rfp_client, rfp_title=rfp_title
    )
    cleaned = re.sub(
        r"\b(marketing\s+plan|request\s+for\s+proposal|rfp|solicitation)\b",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -–,")
    if not cleaned:
        out = _DEFAULT_PRICING_QUERY
    elif re.search(r"\b00[_ ]?guide[_ ]?pricing\b", cleaned, re.I):
        out = cleaned
    else:
        out = f"00_Guide_Pricing {cleaned}"
    # Normalize accidental glued guide line ids (9.19.2 → 9.1 9.2)
    out = re.sub(r"\b(\d+\.\d+)(\d+\.\d+)\b", r"\1 \2", out)
    if out.casefold() != raw.casefold():
        logger.info(
            "Pricing guide query sanitized: %r → %r",
            raw[:120],
            out[:120],
        )
    return out[:220]


async def search_knowledge_base(
    query: str,
    *,
    limit: int = 6,
    category: str | None = None,
    max_chars: int | None = None,
    rfp_client: str = "",
    rfp_sector: str = "",
    rfp_title: str = "",
) -> tuple[str, list[str]]:
    """Search Supermemory and return full indexed documents (not single chunks)."""
    normalized = normalize_zo_kb_query(
        query,
        rfp_client=rfp_client,
        rfp_sector=rfp_sector,
        rfp_title=rfp_title,
    )
    filters: dict[str, Any] | None = None
    if category:
        filters = {
            "AND": [
                *supermemory.KNOWLEDGE_BASE_SEARCH_FILTERS["AND"],
                {"key": "category", "value": category},
            ]
        }
    return await search_and_fetch_full(
        normalized,
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
    # Requirement themes — NEVER the current RFP client (that is not past work).
    del rfp_client, max_chars
    queries = build_case_study_candidate_queries(
        rfp_sector=rfp_sector,
        rfp_context=rfp_context,
        max_queries=6,
    )

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
    buckets: tuple[str, ...] | None = None,
) -> dict[str, tuple[str, list[str]]]:
    """Run targeted Supermemory queries grouped for Sections 1–3.

    When skip_company=True (Company Qualification S1 path), the company bucket is
    omitted — Section 1 uses JIT Company Truth retrieval instead.

    Pass buckets=(...) to gather only specific buckets (e.g. voice-only for chat).
    """
    if not supermemory.is_configured():
        empty = "(Supermemory not configured.)", []
        return {key: empty for key in PROPOSAL_KB_BUCKETS}

    if buckets is not None:
        active_buckets = tuple(b for b in buckets if b in PROPOSAL_KB_BUCKETS)
    elif skip_company:
        active_buckets = ("zo_voice", "bios", "case_studies")
    else:
        active_buckets = PROPOSAL_KB_BUCKETS
    if not active_buckets:
        return {key: ("", []) for key in PROPOSAL_KB_BUCKETS}

    voice_only = active_buckets == ("zo_voice",)
    if voice_only:
        # Chat/voice refresh must not fan out into bios/company/case-study planning.
        planned = {key: [] for key in PROPOSAL_KB_BUCKETS}
        planned["zo_voice"] = [
            f"zö agency brand voice tone proposal writing style {rfp_client}",
            f"zö agency writing style public sector proposals {rfp_sector}",
            "zö agency voice and tone guidelines RFP response",
        ]
    else:
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
    for bucket in active_buckets:
        merged = _unique_queries(planned.get(bucket, []), topic.get(bucket, []))
        if not merged:
            merged = [
                (
                    f"zö agency {rfp_client} {rfp_sector} "
                    f"{bucket.replace('_', ' ')} {rfp_title[:80]}"
                ).strip()
            ]
        if voice_only:
            merged = merged[:3]
        bucket_queries[bucket] = merged
        total_queries += len(bucket_queries[bucket])

    logger.info(
        "Proposal KB search for %s / %s: %d queries across %d buckets%s",
        rfp_client,
        rfp_sector,
        total_queries,
        len(active_buckets),
        " (company skipped — CQ S1 JIT)" if skip_company and buckets is None else "",
    )

    # Buckets run in parallel; within each bucket queries run sequentially.
    logger.info(
        "Gathering %d KB buckets in parallel (per-bucket queries are sequential)...",
        len(active_buckets),
    )

    gathered: dict[str, tuple[str, list[str]]] = {
        key: ("", []) for key in PROPOSAL_KB_BUCKETS
    }
    results = await asyncio.gather(
        *[_gather_bucket(bucket, bucket_queries[bucket]) for bucket in active_buckets]
    )
    for bucket, result in zip(active_buckets, results):
        gathered[bucket] = result

    logger.info(
        "Proposal KB gathered for %s: %s",
        rfp_client,
        ", ".join(f"{b}={len(gathered[b][0])} chars" for b in active_buckets),
    )
    return gathered


def _is_case_study_source(file_name: str) -> bool:
    lowered = file_name.strip().casefold()
    return lowered.startswith("03_cs") or "03_cs_" in lowered or "case study" in lowered


_CASE_THEME_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(
            r"\b(public\s+awareness|behavior\s+change|community\s+outreach|"
            r"social\s+marketing|health\s+communication)\b",
            re.I,
        ),
        "public awareness marketing campaign outcomes",
    ),
    (
        re.compile(
            r"\b(digital\s+campaign|media\s+buy|media\s+planning|paid\s+media|"
            r"geofenc|programmatic|ppc|sem)\b",
            re.I,
        ),
        "digital media campaign paid media results",
    ),
    (
        re.compile(
            r"\b(social\s+media|content\s+strategy|instagram|facebook|tiktok)\b",
            re.I,
        ),
        "social media strategy content campaign results",
    ),
    (
        re.compile(
            r"\b(brand(?:ing)?|visual\s+identity|creative\s+direction|graphic\s+design)\b",
            re.I,
        ),
        "brand identity creative campaign case study",
    ),
    (
        re.compile(
            r"\b(tourism|destination|visitor|leisure|hospitality|event\s+marketing)\b",
            re.I,
        ),
        "tourism destination visitor event marketing campaign",
    ),
    (
        re.compile(
            r"\b(municipal|county|city\s+of|government|public\s+sector)\b",
            re.I,
        ),
        "municipal government public sector campaign results",
    ),
    (
        re.compile(
            r"\b(research|analytics|formative|audience\s+testing|kpi)\b",
            re.I,
        ),
        "research analytics audience campaign measurement outcomes",
    ),
]


def extract_case_study_search_themes(
    *,
    rfp_sector: str = "",
    rfp_context: str = "",
    services_requested: list[str] | None = None,
    max_themes: int = 6,
) -> list[str]:
    """Derive KB search themes from RFP services/requirements (not the buyer name)."""
    services = [s.strip() for s in (services_requested or []) if str(s).strip()]
    blob = " ".join(
        [
            " ".join(services),
            (rfp_context or "")[:12_000],
            (rfp_sector or ""),
        ]
    )
    themes: list[str] = []
    seen: set[str] = set()

    def _add(theme: str) -> None:
        key = theme.casefold()
        if key in seen or not theme.strip():
            return
        seen.add(key)
        themes.append(theme.strip())

    for service in services[:8]:
        _add(service)
    for pattern, theme in _CASE_THEME_PATTERNS:
        if pattern.search(blob):
            _add(theme)
        if len(themes) >= max_themes:
            break

    sector = (rfp_sector or "government").strip()
    _add(f"{sector} case study project outcomes")
    _add("government municipal digital campaign results")
    return themes[:max_themes]


def build_case_study_candidate_queries(
    *,
    rfp_sector: str = "",
    rfp_context: str = "",
    services_requested: list[str] | None = None,
    max_queries: int = 8,
) -> list[str]:
    """Requirement-aware 03_CS_ queries — mirrors kb_qa_loop topical breadth."""
    themes = extract_case_study_search_themes(
        rfp_sector=rfp_sector,
        rfp_context=rfp_context,
        services_requested=services_requested,
        max_themes=max_queries,
    )
    queries = [f"03_CS_ {theme} successful case study" for theme in themes]
    # Always include the master case-study digest so Bend Water / OED / etc. surface.
    queries.append("03_CS_AllCaseStudies public awareness marketing campaign outcomes")
    # Deduplicate while preserving order.
    out: list[str] = []
    seen: set[str] = set()
    for q in queries:
        key = q.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(q)
    return out[:max_queries]


async def search_evidence_candidate_index(
    *,
    rfp_client: str,
    rfp_sector: str,
    rfp_context: str = "",
    services_requested: list[str] | None = None,
    limit_per_query: int = 6,
) -> list[dict[str, str]]:
    """Lightweight evidence index — search hit titles/snippets only, no full doc fetch.

    Queries are built from RFP services/requirements (like kb_qa_loop topical search),
    not only a fixed sector string. Never searches the current buyer name as a past client.
    """
    from app.services.company_qualification.schemas import EvidenceCandidate

    if not supermemory.is_configured():
        return []

    del rfp_client  # never search current prospect as past performance
    queries = build_case_study_candidate_queries(
        rfp_sector=rfp_sector,
        rfp_context=rfp_context,
        services_requested=services_requested,
    )

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

    logger.info(
        "Evidence candidate index: %d unique case studies from %d RFP-theme queries",
        len(candidates),
        len(queries),
    )
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
