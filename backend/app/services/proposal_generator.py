import logging
from datetime import datetime, timezone
import re

from app.models.proposal import (
    ProposalBrandVoice,
    ProposalDraft,
    ProposalResearchCache,
    ProposalSection,
    ResearchQuestion,
    RfpSectionMap,
)
from app.models.rfp import RfpRecord
from app.services import llm, proposal_knowledge_base_tools
from app.services.go_no_go_service import (
    RfpContentInfo,
    _assess_rfp_content,
    _build_rfp_context,
)
from app.services.proposal_brand_voice import format_register_block
from app.services.proposal_langchain import run_tool_research_agent
from app.services.proposal_voice_enforcement import enforce_narrative_voice
from app.services.proposal_repository import (
    get_proposal_draft,
    get_research_cache,
    save_proposal_draft,
    save_research_cache,
)
from app.services.proposal_drafting_graph import run_drafting_graph
from app.services.proposal_loss_lessons import build_loss_lessons_for_rfp
from app.services.proposal_retrieval_graph import run_retrieval_graph
from app.services.proposal_sections_graph import run_sections_1_3_graph
from app.services.rfp_repository import get_rfp

logger = logging.getLogger(__name__)

ZO_SECTIONS: list[dict[str, object]] = [
    {
        "id": "section-1-company-overview",
        "title": "Section 1 — Company Overview",
        "mode": "pull",
        "source": "template",
        "word_target": 900,
        "knowledge_base_query": "zö agency 02 master template company overview certifications WBENC WOSB insurance organizational structure",
        "designer_note": "PULL FROM MASTER TEMPLATE — Section 1. No edits needed.",
    },
    {
        "id": "section-2-team-overview",
        "title": "Section 2 — Team Overview",
        "mode": "select",
        "source": "template",
        "word_target": 1200,
        "knowledge_base_query": "04 bio team zö agency project manager creative director brand strategist approved bios",
        "designer_note": "Select bio layout based on page budget (full page, 3–5 per page, or team overview). Do not rewrite bios.",
    },
    {
        "id": "section-3-our-work",
        "title": "Section 3 — Our Work (Case Studies)",
        "mode": "select",
        "source": "template",
        "word_target": 1500,
        "knowledge_base_query": "03 case study CS zö agency verified outcomes municipal government higher education",
        "designer_note": "Select 2–4 verified 03_CS_ case studies by sector and scope match. Do not write new case studies.",
    },
    {
        "id": "section-4-project-approach",
        "title": "Section 4 — Project Approach",
        "mode": "write",
        "source": "generated",
        "word_target": 1800,
    },
    {
        "id": "section-5-scope-of-work",
        "title": "Section 5 — Scope of Work",
        "mode": "write",
        "source": "generated",
        "word_target": 1500,
    },
]


STATIC_SECTION_IDS = (
    "section-1-company-overview",
    "section-2-team-overview",
    "section-3-our-work",
)


class ProposalError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def _can_start_proposal(rfp: RfpRecord) -> bool:
    return rfp.go_no_go in {"go", "review"}


def _default_sections(page_limit: int | None) -> list[ProposalSection]:
    budget = page_limit or 30
    return [
        ProposalSection(
            id=str(s["id"]),
            title=str(s["title"]),
            pageLimit=max(1, int(budget * ratio)) if (ratio := _page_ratio(i)) else None,
            wordTarget=int(s["word_target"]),
            required=True,
            custom=False,
            source=s["source"],  # type: ignore[arg-type]
            mode=s["mode"],  # type: ignore[arg-type]
            designerNote=str(s["designer_note"]) if s.get("designer_note") else None,
            status="outline",
        )
        for i, s in enumerate(ZO_SECTIONS)
    ]


def _page_ratio(index: int) -> float:
    ratios = [0.12, 0.15, 0.18, 0.32, 0.23]
    return ratios[index] if index < len(ratios) else 0.1


async def _map_rfp_sections(rfp_context: str) -> list[RfpSectionMap]:
    raw, _ = await llm.chat_json(
        [
            {
                "role": "system",
                "content": (
                    "Map the RFP into response sections. Return JSON: "
                    '{"sections":[{"id":"rfp-1","title":"...","pageLimit":null,"requirements":["..."]}]}'
                ),
            },
            {"role": "user", "content": rfp_context[:12000]},
        ]
    )
    sections = raw.get("sections", [])
    if not isinstance(sections, list):
        return []
    result: list[RfpSectionMap] = []
    for item in sections:
        if isinstance(item, dict):
            try:
                result.append(RfpSectionMap.model_validate(item))
            except Exception:
                continue
    return result


async def _build_research_questions(
    rfp: RfpRecord,
    rfp_sections: list[RfpSectionMap],
) -> list[ResearchQuestion]:
    raw, _ = await llm.chat_json(
        [
            {
                "role": "system",
                "content": (
                    "Generate token-efficient research questions for proposal writing. "
                    'Return JSON: {"questions":[{"id":"q1","topic":"compliance","question":"..."}]}'
                    " Cover: scope, evaluation criteria, page limits, required roles, certifications, "
                    "deliverables, timeline, sector context. Max 10 questions."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"RFP: {rfp.title} / {rfp.client} / {rfp.sector}\n"
                    f"Mapped sections: {[s.model_dump(by_alias=True) for s in rfp_sections]}"
                ),
            },
        ]
    )
    questions = raw.get("questions", [])
    result: list[ResearchQuestion] = []
    if isinstance(questions, list):
        for item in questions:
            if isinstance(item, dict) and item.get("question"):
                result.append(
                    ResearchQuestion(
                        id=str(item.get("id", f"q-{len(result)+1}")),
                        topic=str(item.get("topic", "general")),
                        question=str(item["question"]),
                    )
                )
    return result


async def _fill_static_section(
    section_def: dict[str, object],
    section: ProposalSection,
    rfp: RfpRecord,
    research_summary: str,
) -> ProposalSection:
    query = str(section_def.get("knowledge_base_query", "zö agency"))
    text, sources = await proposal_knowledge_base_tools.search_knowledge_base(
        f"{query} {rfp.sector} {rfp.client}",
        limit=6,
    )

    mode = section.mode
    designer = section.designer_note or ""

    if mode == "pull":
        body = (
            f"{designer}\n\n"
            f"--- Reference excerpt (designer pulls full designed pages from master template) ---\n"
            f"{text[:3500]}"
        )
    elif mode == "select" and "case" in section.id.lower():
        selection, _ = await llm.chat_json(
            [
                {
                    "role": "system",
                    "content": (
                        "Select 2-4 case studies from the excerpts. Return JSON: "
                        '{"selected":["filename"],"rationale":"...","designerNote":"..."} '
                        "Use only documents explicitly listed. Never invent clients."
                    ),
                },
                {
                    "role": "user",
                    "content": f"RFP: {rfp.title} / {rfp.sector}\n\nCase studies:\n{text[:8000]}",
                },
            ]
        )
        selected = selection.get("selected", [])
        rationale = selection.get("rationale", "")
        body = (
            f"{designer}\n\n"
            f"Selected case studies: {', '.join(selected) if isinstance(selected, list) else selected}\n"
            f"Rationale: {rationale}\n\n"
            f"--- KB excerpts ---\n{text[:3000]}"
        )
        section.kb_refs = [str(s) for s in selected] if isinstance(selected, list) else []
    else:
        selection, _ = await llm.chat_json(
            [
                {
                    "role": "system",
                    "content": (
                        "Pick team bio layout and named bios from KB. Return JSON: "
                        '{"layout":"full-page|multi|overview","bios":[],"designerNote":"..."}'
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Page budget ~{section.page_limit or 'per RFP'} pages.\n"
                        f"Team bios KB:\n{text[:8000]}\n\nRFP research:\n{research_summary[:3000]}"
                    ),
                },
            ]
        )
        layout = selection.get("layout", "multi")
        bios = selection.get("bios", [])
        body = (
            f"{designer}\n\n"
            f"Recommended layout: {layout}\n"
            f"Bios to include: {', '.join(bios) if isinstance(bios, list) else bios}\n\n"
            f"--- KB excerpts (use exact bio text at layout stage) ---\n{text[:3000]}"
        )
        section.kb_refs = [str(b) for b in bios] if isinstance(bios, list) else []

    section.content = body.strip()
    section.status = "generated"
    section.kb_refs = list(dict.fromkeys([*section.kb_refs, *sources[:5]]))
    return section


async def _write_custom_section(
    section: ProposalSection,
    rfp: RfpRecord,
    research_summary: str,
    rfp_context: str,
) -> ProposalSection:
    raw, _ = await llm.chat_json(
        [
            {
                "role": "system",
                "content": (
                    "You write zö agency proposal content for Sections 4–5 ONLY.\n"
                    f"{format_register_block('narrative')}\n"
                    "Use ONLY facts from the research brief and RFP excerpt. "
                    "Flag unverified items as [VERIFY: ...]. "
                    "Include [DESIGNER NOTE: ...] where layout is needed. "
                    'Return JSON: {"content":"full section prose","designerNote":"..."}'
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Section: {section.title}\n"
                    f"Word target: {section.word_target}\n"
                    f"Client: {rfp.client}\n"
                    f"RFP: {rfp.title}\n\n"
                    f"Research brief:\n{research_summary[:14000]}\n\n"
                    f"RFP excerpt:\n{rfp_context[:8000]}"
                ),
            },
        ]
    )
    section.content = str(raw.get("content", "")).strip()
    section.designer_note = raw.get("designerNote")
    section.status = "generated" if section.content else "outline"
    return section


def _research_summary(cache: ProposalResearchCache) -> str:
    lines = []
    for q in cache.questions:
        if q.answer:
            lines.append(f"Q ({q.topic}): {q.question}\nA: {q.answer}")
    return "\n\n".join(lines)[:16000]


async def generate_proposal(rfp_id: str) -> tuple[ProposalDraft, ProposalResearchCache]:
    if not llm.is_configured():
        raise ProposalError("LLM not configured.", status_code=503)

    rfp = get_rfp(rfp_id)
    if not rfp:
        raise ProposalError("RFP not found", status_code=404)
    if rfp.go_no_go != "go":
        raise ProposalError("RFP must be marked Go before generating a full proposal.", status_code=400)

    logger.info("Proposal generation starting for %s", rfp_id)

    content: RfpContentInfo = _assess_rfp_content(rfp)
    rfp_context = _build_rfp_context(rfp, content)

    if content.substantive_chars < 200:
        raise ProposalError(
            "Insufficient RFP content. Upload a PDF or add a description.",
            status_code=400,
        )

    rfp_sections = await _map_rfp_sections(rfp_context)
    questions = await _build_research_questions(rfp, rfp_sections)

    question_payload = [
        {"id": q.id, "topic": q.topic, "question": q.question} for q in questions
    ]
    answers, provider = await run_tool_research_agent(
        rfp_id=rfp.id,
        title=rfp.title,
        client=rfp.client,
        rfp_excerpt=rfp_context,
        questions=question_payload,
    )

    answer_by_id = {
        str(a.get("id")): a for a in answers if isinstance(a, dict) and a.get("id")
    }
    for q in questions:
        ans = answer_by_id.get(q.id, {})
        q.answer = str(ans.get("answer", "")) if ans else None
        sources = ans.get("sources", [])
        q.sources = [str(s) for s in sources] if isinstance(sources, list) else []

    now = datetime.now(timezone.utc).isoformat()
    research = ProposalResearchCache(
        rfpId=rfp.id,
        rfpSections=rfp_sections,
        questions=questions,
        updatedAt=now,
        provider=provider,
    )
    save_research_cache(research)

    summary = _research_summary(research)
    sections = _default_sections(rfp.page_limit)

    built: list[ProposalSection] = []
    for section_def, section in zip(ZO_SECTIONS, sections, strict=True):
        mode = section_def.get("mode")
        if mode in {"pull", "select"}:
            built.append(await _fill_static_section(section_def, section, rfp, summary))
        else:
            built.append(await _write_custom_section(section, rfp, summary, rfp_context))

    draft = ProposalDraft(
        rfpId=rfp.id,
        sections=built,
        updatedAt=now,
        generatedAt=now,
        provider=provider,
    )
    save_proposal_draft(draft)

    logger.info("Proposal generation complete for %s (%d sections)", rfp_id, len(built))
    return draft, research


def _section_merge_key(section: ProposalSection) -> str:
    match = re.search(r"section-(\d+)", section.id)
    if match:
        return match.group(1)
    return section.title.strip().casefold()


def _merge_sections_into_draft(
    base_sections: list[ProposalSection],
    generated: list[ProposalSection],
) -> list[ProposalSection]:
    generated_by_id = {section.id: section for section in generated}
    generated_by_key = {_section_merge_key(section): section for section in generated}
    merged: list[ProposalSection] = []
    used_generated: set[str] = set()

    for section in base_sections:
        if section.id in generated_by_id:
            merged.append(generated_by_id[section.id])
            used_generated.add(section.id)
            continue
        key = _section_merge_key(section)
        match = generated_by_key.get(key)
        if match and match.id not in used_generated:
            merged.append(match)
            used_generated.add(match.id)
            continue
        merged.append(section)

    for section in generated:
        if section.id not in used_generated:
            merged.append(section)

    return merged


def _static_sections_from_draft(
    draft: ProposalDraft | None,
    page_limit: int | None,
) -> list[ProposalSection]:
    """Always keep zö Sections 1–3 (company, team, case studies) at the front."""
    defaults = _default_sections(page_limit)
    default_by_id = {section.id: section for section in defaults}
    if not draft:
        return defaults[:3]

    by_id = {section.id: section for section in draft.sections}
    static: list[ProposalSection] = []
    for sid in STATIC_SECTION_IDS:
        if sid in by_id:
            static.append(by_id[sid])
        elif sid in default_by_id:
            static.append(default_by_id[sid])

    if len(static) >= 3:
        return static[:3]

    for section in draft.sections:
        key = _section_merge_key(section)
        if key in {"1", "2", "3"} and section not in static:
            static.append(section)
        if len(static) >= 3:
            break

    while len(static) < 3:
        static.append(defaults[len(static)])
    return static[:3]


def _merge_static_with_rfp_sections(
    static_sections: list[ProposalSection],
    rfp_sections: list[ProposalSection],
) -> list[ProposalSection]:
    """Static zö blocks first, then RFP-mapped sections (varies per solicitation)."""
    static_ids = {section.id for section in static_sections}
    rfp_only = [section for section in rfp_sections if section.id not in static_ids]
    return [*static_sections, *rfp_only]


def _load_rfp_for_proposal(rfp_id: str) -> tuple[RfpRecord, RfpContentInfo, str]:
    rfp = get_rfp(rfp_id)
    if not rfp:
        raise ProposalError("RFP not found", status_code=404)
    if not _can_start_proposal(rfp):
        raise ProposalError(
            "RFP must be marked Go or Go With Conditions before generating sections.",
            status_code=400,
        )
    content = _assess_rfp_content(rfp)
    rfp_context = _build_rfp_context(rfp, content)
    if content.substantive_chars < 200:
        raise ProposalError(
            "Insufficient RFP content. Upload a PDF or add a description.",
            status_code=400,
        )
    return rfp, content, rfp_context


async def run_phase2_retrieval(rfp_id: str) -> ProposalResearchCache:
    """Phase 2 only: RFP section map + per-section Supermemory + coverage + evidence corpus."""
    if not llm.is_configured():
        raise ProposalError("LLM not configured.", status_code=503)

    rfp, _content, rfp_context = _load_rfp_for_proposal(rfp_id)
    prior_research = get_research_cache(rfp_id)

    logger.info("Phase 2 retrieval (standalone) starting for %s", rfp_id)
    rfp_sections, evidence_corpus, retrieval_rounds, provider, section_queries = await run_retrieval_graph(
        rfp_id=rfp.id,
        rfp_title=rfp.title,
        rfp_client=rfp.client,
        rfp_sector=rfp.sector,
        rfp_location=rfp.location or None,
        rfp_context=rfp_context,
    )

    loss_lessons, writing_avoidances, _loss_sources = await build_loss_lessons_for_rfp(
        rfp=rfp,
        rfp_context=rfp_context,
    )

    now = datetime.now(timezone.utc).isoformat()
    research = ProposalResearchCache(
        rfpId=rfp.id,
        rfpSections=rfp_sections,
        questions=prior_research.questions if prior_research else [],
        brandVoice=prior_research.brand_voice if prior_research else None,
        evidenceCorpus=evidence_corpus,
        sectionQueries=section_queries,
        retrievalRounds=retrieval_rounds,
        coverageThreshold=85,
        lossLessons=loss_lessons,
        writingAvoidances=writing_avoidances,
        budget=prior_research.budget if prior_research else None,
        updatedAt=now,
        provider=provider,
    )
    save_research_cache(research)

    logger.info(
        "Phase 2 complete for %s: %d RFP sections, %d evidence items, %d loss lessons",
        rfp_id,
        len(rfp_sections),
        len(evidence_corpus),
        len(loss_lessons),
    )
    return research


async def generate_sections_1_3(
    rfp_id: str,
) -> tuple[ProposalDraft, ProposalBrandVoice, ProposalResearchCache]:
    if not llm.is_configured():
        raise ProposalError("LLM not configured.", status_code=503)

    rfp, _content, rfp_context = _load_rfp_for_proposal(rfp_id)

    logger.info("Sections 1–3 generation (LangGraph) starting for %s", rfp_id)

    sections_1_3, brand_voice, provider = await run_sections_1_3_graph(
        rfp_id=rfp.id,
        rfp_title=rfp.title,
        rfp_client=rfp.client,
        rfp_sector=rfp.sector,
        rfp_location=rfp.location or None,
        rfp_context=rfp_context,
        page_limit=rfp.page_limit,
    )

    now = datetime.now(timezone.utc).isoformat()
    existing = get_proposal_draft(rfp_id)
    if existing and len(existing.sections) >= 3:
        merged = _merge_sections_into_draft(existing.sections, sections_1_3)
    else:
        merged = _merge_sections_into_draft(_default_sections(rfp.page_limit), sections_1_3)

    merged = [
        section.model_copy(
            update={
                "content": enforce_narrative_voice(
                    section.content,
                    section_id=section.id,
                    title=section.title,
                    zo_mode=section.mode,
                    register="narrative",
                )
            }
        )
        if section.content.strip()
        else section
        for section in merged
    ]

    draft = ProposalDraft(
        rfpId=rfp.id,
        sections=merged,
        updatedAt=now,
        generatedAt=now,
        provider=provider,
    )
    save_proposal_draft(draft)

    prior_research = get_research_cache(rfp_id)
    research = ProposalResearchCache(
        rfpId=rfp.id,
        rfpSections=prior_research.rfp_sections if prior_research else [],
        questions=prior_research.questions if prior_research else [],
        brandVoice=brand_voice,
        evidenceCorpus=prior_research.evidence_corpus if prior_research else [],
        retrievalRounds=prior_research.retrieval_rounds if prior_research else 0,
        coverageThreshold=prior_research.coverage_threshold if prior_research else 85,
        updatedAt=now,
        provider=provider,
    )
    save_research_cache(research)

    logger.info("Sections 1–3 complete for %s (run Phase 2 separately for KB retrieval)", rfp_id)
    return draft, brand_voice, research


async def run_phase3_drafting(rfp_id: str) -> tuple[ProposalDraft, ProposalResearchCache]:
    """Phase 3: draft all RFP-mapped sections from evidence corpus with [E#] citations."""
    if not llm.is_configured():
        raise ProposalError("LLM not configured.", status_code=503)

    rfp, _content, rfp_context = _load_rfp_for_proposal(rfp_id)
    research = get_research_cache(rfp_id)
    if not research or not research.evidence_corpus:
        raise ProposalError(
            "Phase 2 research required. Run Phase 2 — KB retrieval first.",
            status_code=400,
        )
    if not research.rfp_sections:
        raise ProposalError(
            "No RFP sections mapped. Re-run Phase 2 retrieval.",
            status_code=400,
        )

    existing = get_proposal_draft(rfp_id)
    static_sections = _static_sections_from_draft(existing, rfp.page_limit)
    if not any(section.content.strip() for section in static_sections):
        logger.info(
            "Phase 3 for %s: static Sections 1–3 empty — run Generate Sections 1–3 or Full Proposal first",
            rfp_id,
        )

    logger.info(
        "Phase 3 drafting starting for %s (%d RFP sections, %d evidence items)",
        rfp_id,
        len(research.rfp_sections),
        len(research.evidence_corpus),
    )

    drafted_rfp_sections, provider = await run_drafting_graph(
        rfp_id=rfp.id,
        rfp_title=rfp.title,
        rfp_client=rfp.client,
        rfp_sector=rfp.sector,
        rfp_location=rfp.location or None,
        rfp_context=rfp_context,
        rfp_sections=research.rfp_sections,
        evidence_corpus=research.evidence_corpus,
        brand_voice=research.brand_voice,
        zo_template_sections=static_sections,
        writing_avoidances=research.writing_avoidances,
        loss_lessons=research.loss_lessons,
    )

    merged_sections = _merge_static_with_rfp_sections(
        static_sections,
        drafted_rfp_sections,
    )
    merged_sections = [
        section.model_copy(
            update={
                "content": enforce_narrative_voice(
                    section.content,
                    section_id=section.id,
                    title=section.title,
                    zo_mode=section.mode,
                )
            }
        )
        if section.content.strip()
        else section
        for section in merged_sections
    ]

    now = datetime.now(timezone.utc).isoformat()
    draft = ProposalDraft(
        rfpId=rfp.id,
        sections=merged_sections,
        updatedAt=now,
        generatedAt=now,
        provider=provider,
    )
    save_proposal_draft(draft)

    updated_research = research.model_copy(
        update={"updated_at": now, "provider": provider}
    )
    save_research_cache(updated_research)

    logger.info(
        "Phase 3 complete for %s: %d static + %d RFP sections (%d total)",
        rfp_id,
        len(static_sections),
        len(drafted_rfp_sections),
        len(merged_sections),
    )
    return draft, updated_research


async def generate_full_proposal(
    rfp_id: str,
) -> tuple[ProposalDraft, ProposalBrandVoice, ProposalResearchCache]:
    """Full pipeline: static Sections 1–3 → Phase 2 retrieval → Phase 3 RFP drafting."""
    if not llm.is_configured():
        raise ProposalError("LLM not configured.", status_code=503)

    logger.info("Full proposal pipeline starting for %s", rfp_id)

    _draft, brand_voice, _research = await generate_sections_1_3(rfp_id)
    await run_phase2_retrieval(rfp_id)
    draft, research = await run_phase3_drafting(rfp_id)

    if brand_voice and not research.brand_voice:
        research = research.model_copy(update={"brand_voice": brand_voice})
        save_research_cache(research)

    logger.info(
        "Full proposal complete for %s: %d sections",
        rfp_id,
        len(draft.sections),
    )
    return draft, brand_voice, research
