"""LangGraph Phase 2: RFP section analysis → per-section Supermemory retrieval → coverage loop → evidence corpus."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph

from app.models.proposal import EvidenceItem, RfpSectionMap
from app.services import llm, supermemory
from app.services.llm import LlmError
from app.services.proposal_langchain import _provider_name

logger = logging.getLogger(__name__)

COVERAGE_THRESHOLD = 85
MAX_RETRIEVAL_ROUNDS = 3
QUERIES_PER_SECTION = 5
SEARCH_LIMIT = 8
MAX_CHUNKS_PER_SECTION = 30
EXCERPT_MAX_CHARS = 2_000
MAX_CONCURRENT_SUPERMEMORY_SEARCHES = 6

_LLM_SEMAPHORE = asyncio.Semaphore(2)

RFP_ANALYSIS_PROMPT = """You analyze an RFP to plan a full proposal response for zö agency.

Extract EVERY section the proposer must submit (proposal format, table of contents, attachment list,
evaluation/scored sections, page-by-page demands). For each section return:
- title (as stated in the RFP — keep RFP wording)
- pageLimit if specified
- requirements (bullet list of what evaluators require — quote RFP language)
- retrievalFocus (what to search in zö knowledge base: case studies, bios, certs, pricing, etc.)
- zoMode: pull (company facts/template), select (pick bios/case studies), or write (custom narrative)
- evaluationWeight (points if stated, else null)
- sectionType (firm|team|experience|approach|methodology|timeline|budget|references|forms|other)
- duplicateOfStaticSection (section-1|section-2|section-3 when this RFP tab overlaps zö static sections, else null)

CRITICAL — RFP-PRIMARY (not a static template for every bid):
- Methodology, Timeline, Budget narrative, Approach, References appear ONLY when THIS RFP demands them.
- Do NOT invent generic tabs that are not in the RFP.
- Do NOT skip a scored/required tab because zö already has Sections 1–3 — map it and set duplicateOfStaticSection if it overlaps.
- Phase 3 drafts zö Sections 1–3 first, then ONLY RFP-mapped tabs from this list (dynamic per solicitation).
- For References: quote required count, institution type (e.g. two-year public), and contact fields from the RFP.
- For Pricing/Quotation forms: when the RFP includes a form or forbids alterations, map it as its own tab — do not substitute a custom Section A/B/C/D outline.

MANDATORY SUBMISSION SCAN (every RFP):
- Read "Documents to be Submitted" / Section IV (or equivalent) and list EVERY item: narrative tabs,
  signed forms, addenda acknowledgement, Excel/pricing attachments, references, certifications.
- Vendor qualification blocks: if RFP asks for financial stability, awards/recognitions, higher-ed
  commitment as scored narrative, map each as its own section with requirement bullets quoting RFP text.
- Acknowledgement of Addenda: always map when the RFP says it must be returned with the proposal.

Include compliance/admin sections when they need narrative OR a checklist section in the proposal.

Return ONLY JSON:
{
  "sections": [
    {
      "id": "rfp-sec-1",
      "title": "...",
      "pageLimit": null,
      "requirements": ["..."],
      "retrievalFocus": ["case studies", "government"],
      "zoMode": "select",
      "evaluationWeight": 25,
      "sectionType": "experience",
      "duplicateOfStaticSection": "section-3"
    }
  ]
}"""

BATCH_QUERY_PLANNER_PROMPT = """Plan Supermemory search queries for ALL listed proposal sections in one pass.
Return exactly 3 queries per section (client, sector, location, requirements, retrievalFocus).
Target specific KB buckets when relevant: 02 master template, 03_CS case studies, 04 bios, 
06_WON, 07_FIN rates, 00_Guide_Pricing, certifications, references, portfolio/writing samples.

Return ONLY JSON:
{
  "sections": [
    {"sectionId": "rfp-sec-1", "queries": ["query 1", "query 2"]}
  ]
}"""

BATCH_COVERAGE_EVAL_PROMPT = """Score KB coverage for EACH proposal section listed.
For each section, compare requirements to excerpts. coveragePercent = round(100 * covered / total).

Return ONLY JSON:
{
  "sections": [
    {
      "sectionId": "rfp-sec-1",
      "coveragePercent": 0,
      "uncoveredRequirements": ["..."]
    }
  ]
}"""


class RetrievalGraphState(TypedDict, total=False):
    rfp_id: str
    rfp_title: str
    rfp_client: str
    rfp_sector: str
    rfp_location: str | None
    rfp_context: str
    round: int
    rfp_sections: list[dict[str, Any]]
    section_hits: dict[str, list[dict[str, Any]]]
    section_queries: dict[str, list[str]]
    evidence_corpus: list[dict[str, Any]]
    provider: str
    error: str | None


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


def _hit_key(hit: dict[str, Any]) -> str:
    content = str(hit.get("content") or hit.get("memory") or hit.get("chunk") or "")[:120]
    return str(hit.get("id") or hit.get("customId") or f"{_hit_label(hit)}:{hash(content)}")


def _hit_excerpt(hit: dict[str, Any], *, max_chars: int = EXCERPT_MAX_CHARS) -> str:
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


async def _analyze_rfp(state: RetrievalGraphState) -> dict[str, Any]:
    try:
        raw, provider = await llm.chat_json(
            [
                {"role": "system", "content": RFP_ANALYSIS_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"Client: {state['rfp_client']}\n"
                        f"Sector: {state['rfp_sector']}\n"
                        f"Location: {state.get('rfp_location') or '(not provided)'}\n"
                        f"Title: {state['rfp_title']}\n\n"
                        f"RFP text:\n{state['rfp_context'][:50_000]}"
                    ),
                },
            ],
            max_tokens=4096,
            temperature=0.25,
        )
        sections_raw = raw.get("sections", [])
        sections: list[dict[str, Any]] = []
        if isinstance(sections_raw, list):
            for index, item in enumerate(sections_raw):
                if not isinstance(item, dict):
                    continue
                try:
                    section = RfpSectionMap.model_validate(
                        {
                            **item,
                            "id": item.get("id") or f"rfp-sec-{index + 1}",
                        }
                    )
                    sections.append(section.model_dump(by_alias=True))
                except Exception:
                    continue
        if not sections:
            sections = _fallback_sections(state)
        logger.info(
            "RFP analysis for %s: %d sections mapped",
            state.get("rfp_id"),
            len(sections),
        )
        return {
            "rfp_sections": sections,
            "section_hits": {},
            "section_queries": {},
            "round": 0,
            "provider": provider,
        }
    except LlmError as exc:
        logger.warning("RFP analysis failed for %s: %s", state.get("rfp_id"), exc)
        return {
            "rfp_sections": _fallback_sections(state),
            "section_hits": {},
            "section_queries": {},
            "round": 0,
            "provider": _provider_name(),
        }


def _fallback_sections(state: RetrievalGraphState) -> list[dict[str, Any]]:
    """Minimal section map when LLM analysis fails."""
    client = state["rfp_client"]
    sector = state["rfp_sector"]
    templates = [
        ("rfp-sec-1", "Company Overview", "pull", ["certifications", "company overview"]),
        ("rfp-sec-2", "Team & Personnel", "select", ["team bios", "key personnel"]),
        ("rfp-sec-3", "Relevant Experience", "select", ["case studies", sector]),
        ("rfp-sec-4", "Technical Approach", "write", ["methodology", "approach"]),
        ("rfp-sec-5", "Scope & Deliverables", "write", ["scope", "deliverables"]),
    ]
    result: list[dict[str, Any]] = []
    for sid, title, mode, focus in templates:
        section = RfpSectionMap(
            id=sid,
            title=title,
            requirements=[f"Address {title.lower()} per RFP"],
            retrievalFocus=list(focus),
            zoMode=mode,  # type: ignore[arg-type]
        )
        result.append(section.model_dump(by_alias=True))
    logger.info("Using fallback section map for %s (%s)", client, sector)
    return result


def _sections_needing_retrieval(state: RetrievalGraphState) -> list[dict[str, Any]]:
    sections = state.get("rfp_sections") or []
    current_round = state.get("round") or 0
    if current_round < 1:
        return sections
    return [
        section
        for section in sections
        if (section.get("coveragePercent") or 0) < COVERAGE_THRESHOLD
    ]


async def _plan_queries_for_sections(
    sections: list[dict[str, Any]],
    state: RetrievalGraphState,
    *,
    gap_fill: bool,
) -> dict[str, list[str]]:
    """One LLM call for all sections (avoids N parallel Fireworks requests)."""
    prior = state.get("section_queries") or {}
    used: set[str] = set()
    for queries in prior.values():
        for query in queries:
            used.add(query.strip().lower())

    result: dict[str, list[str]] = {}
    if gap_fill:
        for section in sections:
            sid = str(section.get("id") or "")
            uncovered = section.get("uncoveredRequirements") or []
            queries = [
                f"zö agency {state['rfp_client']} {req}"[:200]
                for req in uncovered[:2]
                if f"zö agency {state['rfp_client']} {req}".strip().lower() not in used
            ]
            if not queries:
                title = str(section.get("title") or "section")
                queries = [
                    f"zö agency {state['rfp_client']} {title} knowledge base"[:220]
                ]
            result[sid] = queries[:QUERIES_PER_SECTION]
        return result

    section_summaries = []
    for section in sections:
        section_summaries.append(
            {
                "sectionId": section.get("id"),
                "title": section.get("title"),
                "requirements": section.get("requirements"),
                "retrievalFocus": section.get("retrievalFocus"),
                "zoMode": section.get("zoMode"),
            }
        )

    try:
        async with _LLM_SEMAPHORE:
            raw, _ = await llm.chat_json(
                [
                    {"role": "system", "content": BATCH_QUERY_PLANNER_PROMPT},
                    {
                        "role": "user",
                        "content": (
                            f"Client: {state['rfp_client']}\n"
                            f"Sector: {state['rfp_sector']}\n"
                            f"Location: {state.get('rfp_location') or ''}\n"
                            f"Already used queries: {list(used)[:20]}\n\n"
                            f"Sections:\n{json.dumps(section_summaries, indent=2)}"
                        ),
                    },
                ],
                max_tokens=2048,
                temperature=0.25,
            )
        planned_sections = raw.get("sections", [])
        if isinstance(planned_sections, list):
            for item in planned_sections:
                if not isinstance(item, dict):
                    continue
                sid = str(item.get("sectionId") or item.get("id") or "").strip()
                queries = item.get("queries", [])
                if not sid or not isinstance(queries, list):
                    continue
                cleaned = [
                    str(query).strip()
                    for query in queries
                    if str(query).strip()
                    and str(query).strip().lower() not in used
                ]
                if cleaned:
                    result[sid] = cleaned[:QUERIES_PER_SECTION]
    except LlmError as exc:
        logger.warning("Batch query planning failed: %s", exc)

    for section in sections:
        sid = str(section.get("id") or "")
        if sid in result and result[sid]:
            continue
        title = str(section.get("title") or "section")
        focus = section.get("retrievalFocus") or []
        focus_text = " ".join(str(item) for item in focus[:3])
        result[sid] = [
            (
                f"zö agency {state['rfp_client']} {state['rfp_sector']} "
                f"{title} {focus_text}"
            ).strip()[:220]
        ]
    return result


async def _search_hits(query: str) -> list[dict[str, Any]]:
    if not supermemory.is_configured():
        return []
    try:
        hits = await supermemory.search_documents(
            query=query,
            limit=SEARCH_LIMIT,
            include_full_docs=True,
            filters=supermemory.KNOWLEDGE_BASE_SEARCH_FILTERS,
        )
        return [hit for hit in hits if supermemory.is_knowledge_base_hit(hit)]
    except supermemory.SupermemoryError:
        return []


_search_semaphore = asyncio.Semaphore(MAX_CONCURRENT_SUPERMEMORY_SEARCHES)


async def _search_hits_throttled(query: str) -> list[dict[str, Any]]:
    async with _search_semaphore:
        return await _search_hits(query)


def _merge_section_hits(
    existing: list[dict[str, Any]],
    new_hits: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    seen = {_hit_key(hit) for hit in existing}
    merged = list(existing)
    for hit in new_hits:
        key = _hit_key(hit)
        if key in seen:
            continue
        seen.add(key)
        merged.append(hit)
        if len(merged) >= MAX_CHUNKS_PER_SECTION:
            break
    return merged


async def _retrieve_round(state: RetrievalGraphState) -> dict[str, Any]:
    if not supermemory.is_configured():
        return {"error": "Supermemory not configured for retrieval."}

    next_round = (state.get("round") or 0) + 1
    gap_fill = next_round > 1
    targets = _sections_needing_retrieval(state)
    if not targets:
        targets = state.get("rfp_sections") or []

    section_hits = dict(state.get("section_hits") or {})
    section_queries = dict(state.get("section_queries") or {})

    planned = await _plan_queries_for_sections(targets, state, gap_fill=gap_fill)

    search_tasks: list[tuple[str, str]] = []
    for section in targets:
        sid = str(section.get("id") or "")
        queries = planned.get(sid, [])
        for query in queries:
            search_tasks.append((sid, query))

    if search_tasks:
        search_results = await asyncio.gather(
            *(_search_hits_throttled(query) for _, query in search_tasks)
        )
        hits_by_section: dict[str, list[dict[str, Any]]] = {}
        for (sid, _), hits in zip(search_tasks, search_results):
            hits_by_section.setdefault(sid, []).extend(hits)
        for section in targets:
            sid = str(section.get("id") or "")
            new_hits = hits_by_section.get(sid, [])
            prior = section_hits.get(sid, [])
            section_hits[sid] = _merge_section_hits(prior, new_hits)
            prior_queries = section_queries.get(sid, [])
            section_queries[sid] = [*prior_queries, *planned.get(sid, [])]

    total_queries = sum(len(planned.get(str(s.get("id") or ""), [])) for s in targets)

    logger.info(
        "Retrieval round %d for %s: %d sections, %d queries (1 LLM plan call)",
        next_round,
        state.get("rfp_id"),
        len(targets),
        total_queries,
    )
    return {"round": next_round, "section_hits": section_hits, "section_queries": section_queries}


async def _evaluate_coverage_batch(
    sections: list[dict[str, Any]],
    section_hits: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """One LLM call to score all sections (avoids N parallel coverage evals)."""
    batch_input: list[dict[str, Any]] = []
    no_hits: list[dict[str, Any]] = []

    for section in sections:
        sid = str(section.get("id") or "")
        hits = section_hits.get(sid, [])
        requirements = section.get("requirements") or []
        if not hits:
            no_hits.append(
                {
                    **section,
                    "coveragePercent": 0,
                    "uncoveredRequirements": list(requirements),
                }
            )
            continue
        if not requirements:
            no_hits.append({**section, "coveragePercent": 50, "uncoveredRequirements": []})
            continue
        excerpts = []
        for index, hit in enumerate(hits[:8], start=1):
            excerpts.append(f"[{index}] {_hit_label(hit)}\n{_hit_excerpt(hit, max_chars=800)}")
        batch_input.append(
            {
                "sectionId": sid,
                "title": section.get("title"),
                "requirements": requirements,
                "excerpts": excerpts,
            }
        )

    scores_by_id: dict[str, dict[str, Any]] = {}
    if batch_input:
        try:
            async with _LLM_SEMAPHORE:
                raw, _ = await llm.chat_json(
                    [
                        {"role": "system", "content": BATCH_COVERAGE_EVAL_PROMPT},
                        {
                            "role": "user",
                            "content": json.dumps(batch_input, indent=2),
                        },
                    ],
                    max_tokens=2048,
                    temperature=0.2,
                )
            for item in raw.get("sections", []):
                if not isinstance(item, dict):
                    continue
                sid = str(item.get("sectionId") or item.get("id") or "").strip()
                if not sid:
                    continue
                percent = item.get("coveragePercent", 0)
                if isinstance(percent, float):
                    percent = int(round(percent))
                percent = max(0, min(100, int(percent)))
                uncovered = item.get("uncoveredRequirements", [])
                if not isinstance(uncovered, list):
                    uncovered = []
                scores_by_id[sid] = {
                    "coveragePercent": percent,
                    "uncoveredRequirements": [
                        str(u) for u in uncovered if str(u).strip()
                    ],
                }
        except LlmError as exc:
            logger.warning("Batch coverage eval failed: %s", exc)

    updated: list[dict[str, Any]] = []
    for section in sections:
        sid = str(section.get("id") or "")
        if sid in scores_by_id:
            updated.append({**section, **scores_by_id[sid]})
            continue
        precomputed = next((s for s in no_hits if s.get("id") == sid), None)
        if precomputed:
            updated.append(precomputed)
            continue
        requirements = section.get("requirements") or []
        updated.append(
            {
                **section,
                "coveragePercent": 40,
                "uncoveredRequirements": list(requirements),
            }
        )
    return updated


async def _evaluate_coverage(state: RetrievalGraphState) -> dict[str, Any]:
    section_hits = state.get("section_hits") or {}
    sections = state.get("rfp_sections") or []

    updated = await _evaluate_coverage_batch(sections, section_hits)
    scores = [section.get("coveragePercent") or 0 for section in updated]
    logger.info(
        "Coverage round %d for %s: scores=%s avg=%.0f (1 LLM eval call)",
        state.get("round"),
        state.get("rfp_id"),
        scores,
        sum(scores) / len(scores) if scores else 0,
    )
    return {"rfp_sections": list(updated)}


def _should_continue_retrieval(state: RetrievalGraphState) -> Literal["retrieve_round", "build_evidence"]:
    current_round = state.get("round") or 0
    if current_round >= MAX_RETRIEVAL_ROUNDS:
        return "build_evidence"
    sections = state.get("rfp_sections") or []
    if not sections:
        return "build_evidence"
    if all((section.get("coveragePercent") or 0) >= COVERAGE_THRESHOLD for section in sections):
        return "build_evidence"
    return "retrieve_round"


async def _build_evidence(state: RetrievalGraphState) -> dict[str, Any]:
    section_hits = state.get("section_hits") or {}
    sections = state.get("rfp_sections") or []
    section_ids_by_key: dict[str, list[str]] = {}

    for section in sections:
        sid = str(section.get("id") or "")
        for hit in section_hits.get(sid, []):
            key = _hit_key(hit)
            section_ids_by_key.setdefault(key, [])
            if sid not in section_ids_by_key[key]:
                section_ids_by_key[key].append(sid)

    seen: set[str] = set()
    corpus: list[dict[str, Any]] = []
    counter = 1

    for section in sections:
        sid = str(section.get("id") or "")
        for hit in section_hits.get(sid, []):
            key = _hit_key(hit)
            if key in seen:
                continue
            seen.add(key)
            evidence_id = f"E{counter}"
            counter += 1
            item = EvidenceItem(
                id=evidence_id,
                source=_hit_label(hit),
                excerpt=_hit_excerpt(hit),
                sectionIds=section_ids_by_key.get(key, [sid]),
                chunkKey=key,
            )
            corpus.append(item.model_dump(by_alias=True))

    logger.info(
        "Evidence corpus for %s: %d items from %d sections",
        state.get("rfp_id"),
        len(corpus),
        len(sections),
    )
    return {"evidence_corpus": corpus}


def _build_graph() -> Any:
    graph = StateGraph(RetrievalGraphState)
    graph.add_node("analyze_rfp", _analyze_rfp)
    graph.add_node("retrieve_round", _retrieve_round)
    graph.add_node("evaluate_coverage", _evaluate_coverage)
    graph.add_node("build_evidence", _build_evidence)

    graph.add_edge(START, "analyze_rfp")
    graph.add_edge("analyze_rfp", "retrieve_round")
    graph.add_edge("retrieve_round", "evaluate_coverage")
    graph.add_conditional_edges(
        "evaluate_coverage",
        _should_continue_retrieval,
        {
            "retrieve_round": "retrieve_round",
            "build_evidence": "build_evidence",
        },
    )
    graph.add_edge("build_evidence", END)
    return graph.compile()


_RETRIEVAL_GRAPH = _build_graph()


async def run_retrieval_graph(
    *,
    rfp_id: str,
    rfp_title: str,
    rfp_client: str,
    rfp_sector: str,
    rfp_location: str | None,
    rfp_context: str,
) -> tuple[list[RfpSectionMap], list[EvidenceItem], int, str, dict[str, list[str]]]:
    if not llm.is_configured():
        raise LlmError(
            "LLM not configured. Set OPENROUTER_API_KEY or FIREWORKS_API_KEY.",
            status_code=503,
        )

    initial: RetrievalGraphState = {
        "rfp_id": rfp_id,
        "rfp_title": rfp_title,
        "rfp_client": rfp_client,
        "rfp_sector": rfp_sector,
        "rfp_location": rfp_location,
        "rfp_context": rfp_context,
        "round": 0,
        "rfp_sections": [],
        "section_hits": {},
        "section_queries": {},
        "evidence_corpus": [],
    }

    logger.info("Proposal retrieval graph starting for rfp_id=%s", rfp_id)
    final = await _RETRIEVAL_GRAPH.ainvoke(initial)

    if final.get("error"):
        raise LlmError(str(final["error"]), status_code=503)

    sections = [
        RfpSectionMap.model_validate(item) for item in (final.get("rfp_sections") or [])
    ]
    evidence = [
        EvidenceItem.model_validate(item) for item in (final.get("evidence_corpus") or [])
    ]
    rounds = int(final.get("round") or 0)
    provider = str(final.get("provider") or _provider_name())
    section_queries = final.get("section_queries") or {}
    if not isinstance(section_queries, dict):
        section_queries = {}

    logger.info(
        "Proposal retrieval complete for %s: %d sections, %d evidence items, %d rounds",
        rfp_id,
        len(sections),
        len(evidence),
        rounds,
    )
    return sections, evidence, rounds, provider, section_queries
