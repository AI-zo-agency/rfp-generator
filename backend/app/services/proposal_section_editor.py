"""Per-section improve: refined KB re-query + targeted re-draft from user chat feedback."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Any

from app.models.proposal import EvidenceItem, ProposalDraft, ProposalResearchCache, ProposalSection, RfpSectionMap
from app.models.rfp import RfpRecord
from app.services import llm, proposal_knowledge_base_tools, supermemory
from app.services.go_no_go_service import RfpContentInfo, _assess_rfp_content, _build_rfp_context
from app.services.llm import LlmError
from app.services.proposal_common import ProposalError, load_rfp_for_proposal
from app.services.proposal_presubmit_autofix import STATIC_SECTION_IDS
from app.services.proposal_langchain import _provider_name
from app.services.proposal_section_quality import (
    prior_content_for_redraft,
    redraft_is_inadequate,
    word_count,
)
from app.services.proposal_brand_voice import (
    classify_section_register,
    format_brand_voice_block,
    resolve_voice_context,
)
from app.services.proposal_loss_lessons import format_avoidance_block
from app.services.proposal_voice_enforcement import enforce_narrative_voice
from app.services.proposal_repository import get_proposal_draft, get_research_cache, save_proposal_draft, save_research_cache
from app.services.proposal_evidence_corpus import merge_hits_into_corpus
from app.services.proposal_retrieval_graph import (
    EXCERPT_MAX_CHARS,
    SEARCH_LIMIT,
    _hit_excerpt,
    _hit_key,
    _hit_label,
)

logger = logging.getLogger(__name__)

REFINE_QUERIES_PROMPT = """Plan 3-4 NEW Supermemory search queries to improve ONE proposal section.
Prior queries failed or returned insufficient evidence. User feedback describes what is wrong or missing.

Rules:
- Queries must be MORE SPECIFIC and DIFFERENT from all prior queries (never repeat or lightly rephrase).
- Use document-type hints where relevant: 02 master template, 03_CS case studies, 04 bio, certifications, org chart.
- Target the exact gaps: firm history, employee count, philosophy, org structure, case studies, fees, etc.
- Include client name, sector, and section requirements in each query.

Return ONLY JSON: {"queries": ["detailed query 1", "detailed query 2", "detailed query 3"]}"""

SECTION_REDRAFT_PROMPT = """Rewrite ONE zö agency proposal section based on user feedback and evidence.

Rules:
1. Directly address the user's edit request.
2. Use ONLY facts from the evidence corpus. Cite inline as [E1], [E2], etc.
3. Improve substantially on the previous draft — never return the same placeholder or [VERIFY] block if evidence now supports the content.
4. Use [VERIFY: ...] only for requirements still missing from evidence.
5. Follow the REGISTER block: narrative sections use first person we/our — NEVER "The Vendor", "The Offeror", or third-person agency distance.
6. PRESERVE the full BRAND VOICE block — zö core voice + RFP adaptation. User edits must NOT flatten tone into generic consultant/corporate prose.
7. Keep rhythm, confidence, warmth, and client-centered framing from the previous draft unless the user explicitly requests a tone change.
8. Apply WRITING AVOIDANCES from lost bids when provided — do not repeat past loss patterns.
9. Write submission-ready prose in zö's voice.

Return ONLY JSON:
{
  "content": "full section prose",
  "kbRefs": ["E1", "E3"],
  "designerNote": null
}"""

STATIC_SECTION_REDRAFT_PROMPT = """Improve ONE static zö proposal section (company overview, team bios, or case studies).

Use ONLY the knowledge-base excerpts provided. For pull/select sections, include [DESIGNER NOTE: ...] where layout applies.
Address the user's feedback. Do not invent clients or metrics.

NARRATIVE REGISTER: first person we/our — never "The Vendor" or third-person procurement language.
PRESERVE the BRAND VOICE block — zö core voice and RFP adaptation are mandatory.

Return ONLY JSON:
{
  "content": "...",
  "kbRefs": ["source filenames"],
  "designerNote": "..."
}"""

_search_semaphore = asyncio.Semaphore(4)


async def _search_hits(query: str) -> list[dict[str, Any]]:
    if not supermemory.is_configured():
        return []
    async with _search_semaphore:
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


def _merge_hits_into_corpus(
    corpus: list[EvidenceItem],
    hits: list[dict[str, Any]],
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


def _evidence_for_section(section_id: str, corpus: list[EvidenceItem]) -> list[EvidenceItem]:
    tagged = [item for item in corpus if section_id in item.section_ids]
    if tagged:
        return tagged[:16]
    return corpus[:8]


def _format_evidence(items: list[EvidenceItem]) -> str:
    lines = []
    for item in items:
        lines.append(f"[{item.id}] {item.source}\n{item.excerpt[:1800]}")
    return "\n\n".join(lines) if lines else "(No evidence yet.)"


def _find_rfp_section(research: ProposalResearchCache, section_id: str) -> RfpSectionMap | None:
    for section in research.rfp_sections:
        if section.id == section_id:
            return section
    return None


def _find_draft_section(draft: ProposalDraft, section_id: str) -> ProposalSection | None:
    for section in draft.sections:
        if section.id == section_id:
            return section
    return None


async def _plan_refined_queries(
    *,
    section: ProposalSection,
    rfp_section: RfpSectionMap | None,
    rfp: RfpRecord,
    prior_queries: list[str],
    user_message: str,
    current_content: str,
) -> list[str]:
    from app.services.proposal_langchain_agents import AgentRole, plan_section_queries_agent

    requirements = rfp_section.requirements if rfp_section else []
    retrieval_focus = rfp_section.retrieval_focus if rfp_section else []

    planned = await plan_section_queries_agent(
        role=AgentRole.USER_REVISE,
        rfp_client=rfp.client,
        rfp_sector=rfp.sector,
        section_title=section.title,
        requirements=requirements,
        retrieval_focus=retrieval_focus,
        prior_queries=prior_queries,
        user_message=user_message,
        current_content=current_content,
    )
    if planned:
        return planned

    raw, _ = await llm.chat_json(
        [
            {"role": "system", "content": REFINE_QUERIES_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Client: {rfp.client}\n"
                    f"Sector: {rfp.sector}\n"
                    f"Section: {section.title}\n"
                    f"Requirements: {requirements}\n"
                    f"Retrieval focus: {retrieval_focus}\n"
                    f"Prior queries (DO NOT repeat):\n"
                    + "\n".join(f"- {q}" for q in prior_queries)
                    + f"\n\nUser feedback:\n{user_message}\n\n"
                    f"Current draft (insufficient):\n{current_content[:2000]}"
                ),
            },
        ],
        max_tokens=1024,
        temperature=0.35,
    )
    queries = raw.get("queries", [])
    if not isinstance(queries, list):
        return []
    used = {q.strip().lower() for q in prior_queries}
    cleaned: list[str] = []
    for query in queries:
        text = str(query).strip()
        if text and text.lower() not in used:
            cleaned.append(text[:240])
            used.add(text.lower())
    return cleaned[:4]


async def _redraft_rfp_section(
    *,
    section: ProposalSection,
    rfp_section: RfpSectionMap | None,
    rfp: RfpRecord,
    rfp_context: str,
    evidence: list[EvidenceItem],
    brand_voice: dict[str, Any] | None,
    kb_zo_voice: str,
    user_message: str,
    prior_content: str,
    zo_context: str,
    avoidance_block: str = "",
) -> tuple[ProposalSection, str]:
    requirements = rfp_section.requirements if rfp_section else []
    register = classify_section_register(
        section_id=section.id,
        title=section.title,
        zo_mode=section.mode,
    )
    voice_block = format_brand_voice_block(
        brand_voice,
        kb_zo_voice=kb_zo_voice,
        rfp_client=rfp.client,
        register=register,
    )

    original_content = (section.content or "").strip()
    prior_for_agent, full_rewrite = prior_content_for_redraft(section)
    rewrite_note = ""
    if full_rewrite:
        rewrite_note = (
            "\n\nIMPORTANT: Prior draft is below the word target or not marked generated. "
            "Write the COMPLETE section for every listed requirement from evidence and KB tools. "
            "Do not return stubs, error text, or unchanged placeholder content.\n"
        )

    user_block = (
        f"BRAND VOICE (mandatory — maintain throughout):\n{voice_block}\n\n"
        f"Client: {rfp.client}\n"
        f"Sector: {rfp.sector}\n"
        f"RFP: {rfp.title}\n"
        f"Section: {section.title}\n"
        f"Word target: {section.word_target}\n"
        f"Requirements:\n"
        + "\n".join(f"- {r}" for r in requirements)
        + rewrite_note
        + f"\n\nUser edit request:\n{user_message}\n\n"
        f"Previous draft:\n{prior_for_agent[:3000] if prior_for_agent else '(none — write from scratch)'}\n\n"
        f"RFP excerpt:\n{rfp_context[:4000]}\n\n"
        f"Evidence corpus:\n{_format_evidence(evidence)}\n\n"
        + (f"{avoidance_block}\n\n" if avoidance_block else "")
        + (f"zö Sections 1–3 reference:\n{zo_context[:3000]}\n" if zo_context else "")
    )

    max_tokens = 8192 if section.word_target >= 1500 else 6144

    try:
        from app.services.proposal_langchain_agents import AgentRole, redraft_section_agent

        raw, provider, _tools = await redraft_section_agent(
            role=AgentRole.USER_REVISE,
            rfp_id=rfp.id,
            rfp_title=rfp.title,
            rfp_client=rfp.client,
            user_content=user_block,
        )
    except Exception as exc:
        logger.warning("User Revise agent failed, falling back to chat_json: %s", exc)
        raw, provider = await llm.chat_json(
            [
                {"role": "system", "content": SECTION_REDRAFT_PROMPT},
                {"role": "user", "content": user_block},
            ],
            max_tokens=max_tokens,
            temperature=0.4,
        )

    content = enforce_narrative_voice(
        str(raw.get("content", "")).strip(),
        section_id=section.id,
        title=section.title,
        zo_mode=section.mode,
    )

    if redraft_is_inadequate(section, content, original_content=original_content):
        logger.warning(
            "User Revise output too short for %s (%d words) — retrying chat_json",
            section.id,
            word_count(content),
        )
        raw, provider = await llm.chat_json(
            [
                {"role": "system", "content": SECTION_REDRAFT_PROMPT},
                {"role": "user", "content": user_block},
            ],
            max_tokens=max_tokens,
            temperature=0.35,
        )
        content = enforce_narrative_voice(
            str(raw.get("content", "")).strip(),
            section_id=section.id,
            title=section.title,
            zo_mode=section.mode,
        )

    if redraft_is_inadequate(section, content, original_content=original_content):
        raise ProposalError(
            f"Section revise did not produce enough content ({word_count(content)} words). "
            "Try a more specific instruction or re-run Phase 3 for this section.",
            status_code=422,
        )
    kb_refs = raw.get("kbRefs") or raw.get("kb_refs") or []
    if not isinstance(kb_refs, list):
        kb_refs = []
    from_text = re.findall(r"\[E(\d+)\]", content)
    refs = {f"E{n}" for n in from_text}
    refs.update(str(r) for r in kb_refs if str(r).strip())

    updated = section.model_copy(
        update={
            "content": content,
            "designer_note": raw.get("designerNote") or raw.get("designer_note"),
            "status": "generated",
            "kb_refs": sorted(refs, key=lambda x: int(x[1:]) if x[1:].isdigit() else 0),
        }
    )
    return updated, provider


async def _improve_static_section(
    *,
    section: ProposalSection,
    rfp: RfpRecord,
    rfp_context: str,
    queries: list[str],
    user_message: str,
    brand_voice: dict[str, Any] | None,
    kb_zo_voice: str,
) -> tuple[ProposalSection, str]:
    kb_parts: list[str] = []
    sources: list[str] = []
    for query in queries:
        text, refs = await proposal_knowledge_base_tools.search_knowledge_base(
            query,
            limit=6,
        )
        if text.strip():
            kb_parts.append(text[:3500])
        sources.extend(refs)

    if not kb_parts:
        text, refs = await proposal_knowledge_base_tools.search_knowledge_base(
            f"zö agency {section.title} {rfp.client} {rfp.sector}",
            limit=8,
        )
        kb_parts.append(text[:4000])
        sources.extend(refs)

    voice_block = format_brand_voice_block(
        brand_voice,
        kb_zo_voice=kb_zo_voice,
        rfp_client=rfp.client,
        register="narrative",
    )

    raw, provider = await llm.chat_json(
        [
            {"role": "system", "content": STATIC_SECTION_REDRAFT_PROMPT},
            {
                "role": "user",
                "content": (
                    f"BRAND VOICE (mandatory — maintain throughout):\n{voice_block}\n\n"
                    f"Section: {section.title}\n"
                    f"Mode: {section.mode}\n"
                    f"Client: {rfp.client}\n"
                    f"User request:\n{user_message}\n\n"
                    f"Previous content (preserve zö voice while improving):\n{section.content[:2500]}\n\n"
                    f"KB excerpts:\n{'---'.join(kb_parts)[:10000]}\n\n"
                    f"RFP excerpt:\n{rfp_context[:3000]}"
                ),
            },
        ],
        max_tokens=4096,
        temperature=0.35,
    )
    content = enforce_narrative_voice(
        str(raw.get("content", "")).strip(),
        section_id=section.id,
        title=section.title,
        register="narrative",
    )
    kb_refs = raw.get("kbRefs") or sources[:8]
    updated = section.model_copy(
        update={
            "content": content or section.content,
            "designer_note": raw.get("designerNote") or section.designer_note,
            "status": "generated",
            "kb_refs": [str(r) for r in kb_refs] if isinstance(kb_refs, list) else sources[:8],
        }
    )
    return updated, provider


async def improve_proposal_section(
    rfp_id: str,
    section_id: str,
    user_message: str,
    *,
    persist: bool = True,
) -> tuple[ProposalSection, ProposalDraft, ProposalResearchCache, str, str]:
    """Re-query KB with new detailed queries, expand evidence, re-draft one section only."""
    if not llm.is_configured():
        raise ProposalError("LLM not configured.", status_code=503)
    if not user_message.strip():
        raise ProposalError("Edit message is required.", status_code=400)

    rfp, _content, rfp_context = load_rfp_for_proposal(rfp_id)
    draft = get_proposal_draft(rfp_id)
    if not draft:
        raise ProposalError("No proposal draft found. Generate a proposal first.", status_code=400)

    section = _find_draft_section(draft, section_id)
    if not section:
        raise ProposalError(f"Section {section_id} not found in draft.", status_code=404)

    research = get_research_cache(rfp_id)
    is_static = section_id in STATIC_SECTION_IDS or section.source == "template"

    brand_voice_dict, kb_zo_voice = await resolve_voice_context(
        rfp=rfp,
        rfp_context=rfp_context,
        brand_voice=(
            research.brand_voice.model_dump(by_alias=True)
            if research and research.brand_voice
            else None
        ),
    )

    logger.info(
        "Section improve for %s / %s: static=%s message=%r",
        rfp_id,
        section_id,
        is_static,
        user_message[:80],
    )

    provider = _provider_name()
    evidence_added = 0
    query_count = 0

    if is_static:
        prior_queries = []
        if research:
            prior_queries = (research.section_queries or {}).get(section_id, [])
        queries = await _plan_refined_queries(
            section=section,
            rfp_section=None,
            rfp=rfp,
            prior_queries=prior_queries,
            user_message=user_message,
            current_content=section.content,
        )
        if not queries:
            queries = [
                f"zö agency 02 master template {section.title} {rfp.client}"[:220],
                f"zö agency {rfp.sector} {section.title} organizational structure employees"[:220],
            ]
        query_count = len(queries)
        updated_section, provider = await _improve_static_section(
            section=section,
            rfp=rfp,
            rfp_context=rfp_context,
            queries=queries,
            user_message=user_message,
            brand_voice=brand_voice_dict,
            kb_zo_voice=kb_zo_voice,
        )
        new_queries = {
            **(research.section_queries if research else {}),
            section_id: [*prior_queries, *queries],
        }
        if research:
            research = research.model_copy(update={"section_queries": new_queries, "provider": provider})
        else:
            research = ProposalResearchCache(
                rfpId=rfp_id,
                sectionQueries=new_queries,
                updatedAt=datetime.now(timezone.utc).isoformat(),
                provider=provider,
            )
    else:
        if not research or not research.evidence_corpus:
            raise ProposalError(
                "Phase 2 research required for RFP sections. Run KB retrieval first.",
                status_code=400,
            )

        prior_queries = (research.section_queries or {}).get(section_id, [])
        rfp_section = _find_rfp_section(research, section_id)

        queries = await _plan_refined_queries(
            section=section,
            rfp_section=rfp_section,
            rfp=rfp,
            prior_queries=prior_queries,
            user_message=user_message,
            current_content=section.content,
        )
        if not queries:
            title = section.title
            queries = [
                f"zö agency firm history organizational chart employee count {rfp.client} {title}"[:240],
                f"zö agency company philosophy capabilities statement {rfp.sector} {title}"[:240],
                f"zö agency 02 master template certifications WBENC WOSB {title}"[:240],
            ]

        query_count = len(queries)

        all_hits: list[dict[str, Any]] = []
        for query in queries:
            hits = await _search_hits(query)
            all_hits.extend(hits)
            logger.info("Section refine search %s: %d hits for %r", section_id, len(hits), query[:60])

        prior_corpus_len = len(research.evidence_corpus)
        corpus = _merge_hits_into_corpus(research.evidence_corpus, all_hits, section_id)
        evidence_added = len(corpus) - prior_corpus_len
        section_evidence = _evidence_for_section(section_id, corpus)

        from app.services.proposal_generator import _static_sections_from_draft

        static = _static_sections_from_draft(draft, rfp.page_limit)
        zo_context = "\n\n".join(
            f"### {s.title}\n{s.content[:1500]}"
            for s in static[:3]
            if s.content.strip()
        )

        avoidance_block = format_avoidance_block(
            research.writing_avoidances,
            research.loss_lessons,
        )

        updated_section, provider = await _redraft_rfp_section(
            section=section,
            rfp_section=rfp_section,
            rfp=rfp,
            rfp_context=rfp_context,
            evidence=section_evidence,
            brand_voice=brand_voice_dict,
            kb_zo_voice=kb_zo_voice,
            user_message=user_message,
            prior_content=section.content,
            zo_context=zo_context,
            avoidance_block=avoidance_block,
        )

        new_queries = {**research.section_queries, section_id: [*prior_queries, *queries]}
        updated_rfp_sections: list[RfpSectionMap] = []
        for s in research.rfp_sections:
            if s.id == section_id:
                updated_rfp_sections.append(
                    s.model_copy(
                        update={
                            "coverage_percent": min(95, (s.coverage_percent or 0) + 15),
                        }
                    )
                )
            else:
                updated_rfp_sections.append(s)

        research = research.model_copy(
            update={
                "evidence_corpus": corpus,
                "section_queries": new_queries,
                "rfp_sections": updated_rfp_sections,
                "provider": provider,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        )

    merged_sections = [
        updated_section if s.id == section_id else s for s in draft.sections
    ]

    now = datetime.now(timezone.utc).isoformat()
    updated_draft = draft.model_copy(
        update={
            "sections": merged_sections,
            "updated_at": now,
            "provider": provider,
        }
    )
    if persist:
        save_proposal_draft(updated_draft)
        save_research_cache(research)

    word_count_result = word_count(updated_section.content)
    if is_static:
        assistant_message = (
            f"Re-searched the knowledge base with {query_count} new detailed queries "
            f"and rewrote **{section.title}** ({word_count_result} words). "
            f"Review citations and [DESIGNER NOTE] blocks."
        )
    else:
        assistant_message = (
            f"Ran {query_count} new Supermemory queries (different from prior searches), "
            f"added {evidence_added} evidence item(s) to the corpus, and rewrote "
            f"**{section.title}** ({word_count_result} words). Check [E#] citations."
        )

    logger.info(
        "Section improve complete for %s / %s (%d words)",
        rfp_id,
        section_id,
        word_count_result,
    )
    return updated_section, updated_draft, research, provider, assistant_message
