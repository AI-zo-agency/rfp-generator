import logging
import re
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

from app.models.proposal import (
    PreSubmitReview,
    ProposalBrandVoice,
    ProposalBudget,
    ProposalDraft,
    ProposalResearchCache,
    ProposalSection,
    PreSubmitAutoFixReport,
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
from app.services.proposal_voice_enforcement import (
    enforce_narrative_voice,
    is_duplicate_static_rfp_section,
)
from app.services.proposal_repository import (
    aget_proposal_draft,
    aget_research_cache,
    asave_proposal_draft,
    asave_research_cache,
    get_proposal_draft,
    get_research_cache,
    save_proposal_draft,
    save_research_cache,
)
from app.services.proposal_drafting_graph import run_drafting_graph
from app.services.proposal_budget_content import incorporate_budget_into_draft
from app.services.proposal_budget_editor import run_budget_editor_pass
from app.services.proposal_budget_sync import align_fee_narrative_with_budget
from app.services.proposal_consistency import self_edit_exhausted_issues
from app.services.proposal_fee_justification import generate_fee_justification_memo
from app.services.proposal_loss_lessons import build_loss_lessons_for_rfp
from app.services.proposal_pipeline_status import assert_manuscript_ready
from app.services.proposal_pricing_service import generate_proposal_budget
from app.services.proposal_presubmit_review import run_presubmit_review
from app.services.proposal_presubmit_autofix import run_presubmit_autofix_loop
from app.services.proposal_intelligence.graph import run_intelligence_graph
from app.services.proposal_intelligence.plan_ops import IntelligenceError
from app.services.proposal_intelligence.schemas import ProposalExecutionPlan
from app.services.proposal_self_edit_loop import run_self_edit_loop
from app.services.proposal_sections_graph import run_sections_1_3_graph
from app.services.rfp_repository import get_rfp

logger = logging.getLogger(__name__)

ZO_SECTIONS: list[dict[str, object]] = [
    # Section 1 — Company Overview subsections
    {
        "id": "section-1-who-we-are",
        "title": "1.1 — Who We Are",
        "mode": "pull",
        "source": "template",
        "word_target": 600,
        "designer_note": "Section 1 subsection: 1.1 — Who We Are.",
    },
    {
        "id": "section-1-org-structure",
        "title": "1.2 — Organizational Structure",
        "mode": "pull",
        "source": "template",
        "word_target": 800,
        "designer_note": "Full org chart from Master Team Roster — every person by department.",
    },
    {
        "id": "section-1-business-info",
        "title": "1.3 — Business Information",
        "mode": "pull",
        "source": "template",
        "word_target": 400,
        "designer_note": "Section 1 subsection: 1.3 — Business Information.",
    },
    {
        "id": "section-1-certifications",
        "title": "1.4 — Certifications",
        "mode": "pull",
        "source": "template",
        "word_target": 400,
        "designer_note": "Section 1 subsection: 1.4 — Certifications.",
    },
    {
        "id": "section-1-insurance",
        "title": "1.5 — Insurance Information",
        "mode": "pull",
        "source": "template",
        "word_target": 400,
        "designer_note": "Section 1 subsection: 1.5 — Insurance Information.",
    },
    # Section 2 — Team Bios (placeholder; subsections generated dynamically)
    {
        "id": "section-2-bio-placeholder",
        "title": "2.x — Team Bios (generated per member)",
        "mode": "select",
        "source": "template",
        "word_target": 500,
        "designer_note": "Team bios template. Generated dynamically per member.",
    },
    # Section 3 — Our Work (placeholder; subsections generated dynamically)
    {
        "id": "section-3-work-placeholder",
        "title": "3.x — Our Work (generated per example)",
        "mode": "select",
        "source": "template",
        "word_target": 600,
        "designer_note": "Our Work examples. Generated dynamically.",
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


def static_sections_1_3_have_content(draft: ProposalDraft | None) -> bool:
    """True when all three zö template sections have body text (checks new subsection prefixes)."""
    if not draft:
        return False
    has_section1 = any(
        (s.id.startswith("section-1-") or s.id == "section-1-company-overview")
        and (s.content or "").strip()
        for s in draft.sections
    )
    has_section2 = any(
        (s.id.startswith("section-2-") or s.id == "section-2-team-overview")
        and (s.content or "").strip()
        for s in draft.sections
    )
    has_section3 = any(
        (s.id.startswith("section-3-") or s.id == "section-3-our-work")
        and (s.content or "").strip()
        for s in draft.sections
    )
    return has_section1 and has_section2 and has_section3



from app.services.proposal_common import ProposalError, can_start_proposal, load_rfp_for_proposal


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
        # KB references removed - not included in proposals
        section.kb_refs = []
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
        # KB references removed - not included in proposals
        section.kb_refs = []

    section.content = body.strip()
    section.status = "generated"
    # KB references removed - not included in proposals
    section.kb_refs = []
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
    """Always keep zö static Sections 1–3 (company subsections, team bios, our work examples) at the front."""
    defaults = _default_sections(page_limit)
    if not draft:
        return [s for s in defaults if s.id.startswith(("section-1-", "section-2-", "section-3-"))]

    static: list[ProposalSection] = []
    for s in draft.sections:
        is_static_1_3 = (
            s.id.startswith(("section-1-", "section-2-bio-", "section-3-work-"))
            or s.id in {"section-1-company-overview", "section-2-team-overview", "section-3-our-work"}
        )
        if is_static_1_3:
            static.append(s)

    if not static:
        return [s for s in defaults if s.id.startswith(("section-1-", "section-2-", "section-3-"))]

    return static


def _merge_static_with_rfp_sections(
    static_sections: list[ProposalSection],
    rfp_sections: list[ProposalSection],
) -> list[ProposalSection]:
    """Static zö blocks first, then RFP-mapped sections (varies per solicitation)."""
    static_ids = {section.id for section in static_sections}
    rfp_only = [section for section in rfp_sections if section.id not in static_ids]
    return [*static_sections, *rfp_only]


def _is_static_1_3_section_id(section_id: str) -> bool:
    return section_id.startswith(("section-1-", "section-2-", "section-3-")) or section_id in {
        "section-1-company-overview",
        "section-2-team-overview",
        "section-3-our-work",
    }


def _prefer_richer_section(current: ProposalSection, incoming: ProposalSection) -> ProposalSection:
    """Never let an empty parallel-track emit wipe content already saved by another track."""
    current_has = bool(current.content and current.content.strip())
    incoming_has = bool(incoming.content and incoming.content.strip())
    if incoming_has:
        return incoming
    if current_has:
        return current
    return incoming


async def _persist_sections_1_3_partial(
    rfp_id: str,
    sections_1_3: list[ProposalSection],
    provider: str,
    *,
    brand_voice: ProposalBrandVoice | None = None,
) -> None:
    """Save sections 1–3 as each completes so the UI can show progress immediately.

    Parallel S1 / S2 / S3 tracks emit independently — merge with the existing draft so
    one track never blanks another track's already-generated subsections.
    """
    rfp = get_rfp(rfp_id)
    page_limit = rfp.page_limit if rfp else 30
    existing = await aget_proposal_draft(rfp_id)

    template_1_3 = [
        s
        for s in _default_sections(page_limit)
        if s.id.startswith(("section-1-", "section-2-", "section-3-"))
    ]

    by_id: dict[str, ProposalSection] = {s.id: s for s in template_1_3}
    # Keep any previously persisted 1–3 content (other parallel track).
    if existing:
        for section in existing.sections:
            if _is_static_1_3_section_id(section.id):
                by_id[section.id] = section
    # Apply this emit — only overwrite when incoming has real content (or new ids).
    for section in sections_1_3:
        prior = by_id.get(section.id)
        by_id[section.id] = (
            _prefer_richer_section(prior, section) if prior is not None else section
        )

    ordered: list[ProposalSection] = []
    seen: set[str] = set()
    for section in template_1_3:
        ordered.append(by_id[section.id])
        seen.add(section.id)
    # Preserve dynamic bios / case studies already in draft, then new ones from this emit.
    dynamic_order: list[str] = []
    if existing:
        for section in existing.sections:
            if section.id not in seen and _is_static_1_3_section_id(section.id):
                dynamic_order.append(section.id)
    for section in sections_1_3:
        if section.id not in seen and section.id not in dynamic_order:
            dynamic_order.append(section.id)
    for sid in dynamic_order:
        if sid in by_id:
            ordered.append(by_id[sid])
            seen.add(sid)

    sections_1_3 = ordered

    # Non–Sections-1–3 content stays as-is.
    base_sections: list[ProposalSection] = []
    if existing:
        for s in existing.sections:
            if not _is_static_1_3_section_id(s.id):
                base_sections.append(s)
    else:
        for s in _default_sections(page_limit):
            if not _is_static_1_3_section_id(s.id):
                base_sections.append(s)

    merged = [*sections_1_3, *base_sections]

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

    now = datetime.now(timezone.utc).isoformat()
    draft = ProposalDraft(
        rfpId=rfp_id,
        sections=merged,
        updatedAt=now,
        generatedAt=now,
        provider=provider,
    )
    await asave_proposal_draft(draft)

    if brand_voice is not None:
        prior_research = await aget_research_cache(rfp_id)
        research = ProposalResearchCache(
            rfpId=rfp_id,
            rfpSections=prior_research.rfp_sections if prior_research else [],
            questions=prior_research.questions if prior_research else [],
            brandVoice=brand_voice,
            evidenceCorpus=prior_research.evidence_corpus if prior_research else [],
            retrievalRounds=prior_research.retrieval_rounds if prior_research else 0,
            coverageThreshold=prior_research.coverage_threshold if prior_research else 85,
            pipelineCheckpoint=prior_research.pipeline_checkpoint if prior_research else None,
            updatedAt=now,
            provider=provider,
        )
        await asave_research_cache(research)


async def _persist_phase3_partial(
    rfp_id: str,
    *,
    static_sections: list[ProposalSection],
    drafted_rfp_sections: list[ProposalSection],
    rfp_sections: list[RfpSectionMap],
    provider: str,
) -> None:
    """Save each drafted RFP section immediately; remaining slots stay as outline stubs."""
    drafted_ids = {section.id for section in drafted_rfp_sections}
    stubs: list[ProposalSection] = []
    for mapped in rfp_sections:
        if mapped.id in drafted_ids:
            continue
        if is_duplicate_static_rfp_section(mapped.title):
            continue
        stubs.append(
            ProposalSection(
                id=mapped.id,
                title=mapped.title,
                pageLimit=mapped.page_limit,
                wordTarget=800,
                required=True,
                custom=False,
                source="rfp",
                mode=mapped.zo_mode or "write",
                content="",
                status="outline",
            )
        )

    merged_sections = _merge_static_with_rfp_sections(
        static_sections,
        [*drafted_rfp_sections, *stubs],
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
        rfpId=rfp_id,
        sections=merged_sections,
        updatedAt=now,
        generatedAt=now,
        provider=provider,
    )
    await asave_proposal_draft(draft)


def _load_rfp_for_proposal(rfp_id: str) -> tuple[RfpRecord, RfpContentInfo, str]:
    return load_rfp_for_proposal(rfp_id)


async def run_phase2_retrieval(rfp_id: str) -> ProposalResearchCache:
    """Phase 2: Proposal Intelligence Layer → ProposalExecutionPlan (no writing evidence)."""
    if not llm.is_configured():
        raise ProposalError("LLM not configured.", status_code=503)

    rfp, _content, rfp_context = _load_rfp_for_proposal(rfp_id)
    prior_research = get_research_cache(rfp_id)

    logger.info("Phase 2 intelligence starting for %s", rfp_id)
    try:
        plan, legacy = await run_intelligence_graph(
            rfp_id=rfp.id,
            rfp_title=rfp.title,
            rfp_client=rfp.client,
            rfp_sector=rfp.sector,
            rfp_location=rfp.location or None,
            rfp_context=rfp_context,
        )
    except IntelligenceError as exc:
        raise ProposalError(str(exc), status_code=422) from exc

    if plan.validation.readiness_status == "blocked":
        raise ProposalError(
            "Phase 2 intelligence blocked: " + "; ".join(plan.validation.blockers),
            status_code=422,
        )

    rfp_sections = legacy.get("rfpSections") or []
    section_queries = legacy.get("sectionQueries") or {}
    proof_points = legacy.get("proofPoints") or []

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
        evidenceCorpus=[],  # HARD RULE: writing evidence only in Phase 3
        sectionQueries=section_queries,
        retrievalRounds=0,
        coverageThreshold=85,
        lossLessons=loss_lessons,
        writingAvoidances=writing_avoidances,
        proofPoints=proof_points,
        proposalExecutionPlan=plan,
        budget=prior_research.budget if prior_research else None,
        presubmitReview=prior_research.presubmit_review if prior_research else None,
        pipelineCheckpoint=prior_research.pipeline_checkpoint if prior_research else None,
        updatedAt=now,
        provider=plan.metadata.provider,
    )
    save_research_cache(research)

    logger.info(
        "Phase 2 complete for %s: plan=%s sections=%d decisions=%d evidence=0",
        rfp_id,
        plan.validation.readiness_status,
        len(rfp_sections),
        len(plan.decision_log),
    )
    return research


def _phase2_plan_ready(research: ProposalResearchCache | None) -> bool:
    if not research:
        return False
    plan = research.proposal_execution_plan
    if plan is None:
        # Legacy caches created before intelligence layer
        return bool(research.evidence_corpus and research.rfp_sections)
    if isinstance(plan, dict):
        status = (plan.get("validation") or {}).get("readinessStatus")
        return status == "ready" and bool(research.rfp_sections)
    if isinstance(plan, ProposalExecutionPlan):
        return plan.validation.readiness_status == "ready" and bool(research.rfp_sections)
    return bool(research.rfp_sections)


async def generate_sections_1_3(
    rfp_id: str,
    *,
    force_regenerate: bool = False,
) -> tuple[ProposalDraft, ProposalBrandVoice, ProposalResearchCache]:
    if not llm.is_configured():
        raise ProposalError("LLM not configured.", status_code=503)

    rfp, _content, rfp_context = _load_rfp_for_proposal(rfp_id)

    existing_draft = get_proposal_draft(rfp_id)
    existing_sections_1_3: list[ProposalSection] = []
    has_section1 = has_section2 = has_section3 = False
    existing_section1: list[ProposalSection] = []
    existing_section2: list[ProposalSection] = []
    existing_section3: list[ProposalSection] = []

    if force_regenerate:
        logger.info(
            "Force-regenerating sections 1–3 for %s (explicit draft request)",
            rfp_id,
        )
    elif existing_draft:
        # Check if we already have COMPLETE sections 1-3 with content
        existing_sections_1_3 = [
            s for s in existing_draft.sections
            if s.id.startswith(("section-1-", "section-2-", "section-3-"))
        ]

        # Check if all three section groups are present and have content
        has_section1 = any(
            s.id.startswith("section-1-") and s.content.strip()
            for s in existing_sections_1_3
        )
        has_section2 = any(
            s.id.startswith("section-2-") and s.content.strip()
            for s in existing_sections_1_3
        )
        has_section3 = any(
            s.id.startswith("section-3-") and s.content.strip()
            for s in existing_sections_1_3
        )

        if has_section1 and has_section2 and has_section3:
            logger.info(
                "Sections 1–3 already complete for %s — using cached version. "
                "Use RESET to regenerate.",
                rfp_id,
            )
            research = get_research_cache(rfp_id)
            brand_voice = (
                research.brand_voice
                if research and research.brand_voice
                else ProposalBrandVoice(
                    tone="professional", style="narrative", voice="first_person"
                )
            )
            return existing_draft, brand_voice, research or ProposalResearchCache(
                rfp_id=rfp_id
            )

        missing = []
        if not has_section1:
            missing.append("Section 1 (Company)")
        if not has_section2:
            missing.append("Section 2 (Team)")
        if not has_section3:
            missing.append("Section 3 (Our Work)")
        logger.info(
            "Sections 1–3 incomplete for %s (missing: %s) — will preserve existing and regenerate missing",
            rfp_id,
            ", ".join(missing),
        )
        existing_section1 = (
            [s for s in existing_sections_1_3 if s.id.startswith("section-1-")]
            if has_section1
            else []
        )
        existing_section2 = (
            [s for s in existing_sections_1_3 if s.id.startswith("section-2-")]
            if has_section2
            else []
        )
        existing_section3 = (
            [s for s in existing_sections_1_3 if s.id.startswith("section-3-")]
            if has_section3
            else []
        )

    preserve_existing = bool(existing_draft and not force_regenerate)
    logger.info("Sections 1–3 generation (LangGraph) starting for %s", rfp_id)

    async def _on_sections_partial(
        partial: list[ProposalSection],
        provider: str,
        brand_voice: ProposalBrandVoice | None,
    ) -> None:
        await _persist_sections_1_3_partial(
            rfp_id,
            partial,
            provider,
            brand_voice=brand_voice,
        )

    # Seed Section 1 stubs immediately so the UI can show 1.1–1.5 while agents run.
    if not (preserve_existing and has_section1):
        stub_sections = [
            s
            for s in _default_sections(rfp.page_limit)
            if s.id.startswith("section-1-")
        ]
        await _persist_sections_1_3_partial(
            rfp_id,
            stub_sections,
            "pending",
            brand_voice=None,
        )

    existing_sections_for_graph = existing_sections_1_3 if preserve_existing else []
    skip_section_1 = preserve_existing and has_section1
    skip_section_2 = preserve_existing and has_section2
    skip_section_3 = preserve_existing and has_section3
    sections_1_3, brand_voice, provider, section1_editorial = await run_sections_1_3_graph(
        rfp_id=rfp.id,
        rfp_title=rfp.title,
        rfp_client=rfp.client,
        rfp_sector=rfp.sector,
        rfp_location=rfp.location or None,
        rfp_context=rfp_context,
        page_limit=rfp.page_limit,
        on_sections_partial=_on_sections_partial,
        existing_sections=existing_sections_for_graph,
        skip_section_1=skip_section_1,
        skip_section_2=skip_section_2,
        skip_section_3=skip_section_3,
    )

    # Merge with existing sections if any were already complete
    if preserve_existing and (existing_section1 or existing_section2 or existing_section3):
        # Replace newly generated sections with existing ones that were already complete
        merged_sections = []
        for section in sections_1_3:
            # If this is a section 1 and we already had good section 1, use the existing one
            if section.id.startswith("section-1-") and existing_section1:
                # Check if we already added sections from existing_section1
                if not any(s.id == section.id for s in merged_sections):
                    # Find matching existing section or use new one
                    existing = next((s for s in existing_section1 if s.id == section.id), None)
                    merged_sections.append(existing if existing else section)
            elif section.id.startswith("section-2-") and existing_section2:
                existing = next((s for s in existing_section2 if s.id == section.id), None)
                merged_sections.append(existing if existing else section)
            elif section.id.startswith("section-3-") and existing_section3:
                existing = next((s for s in existing_section3 if s.id == section.id), None)
                merged_sections.append(existing if existing else section)
            else:
                merged_sections.append(section)
        
        # Add any existing sections that weren't in the newly generated list
        for section in existing_section1 + existing_section2 + existing_section3:
            if not any(s.id == section.id for s in merged_sections):
                merged_sections.append(section)
        
        if merged_sections:
            logger.info(
                "Merged %d existing sections with %d newly generated sections",
                len(existing_section1 + existing_section2 + existing_section3),
                len(sections_1_3)
            )
            sections_1_3 = merged_sections

    # Parallel tracks persist via partial callbacks; the graph return can still
    # omit a track if stream accumulation fails. Fold richer draft content back in.
    draft_after_graph = get_proposal_draft(rfp_id)
    if draft_after_graph:
        by_id = {s.id: s for s in sections_1_3}
        for section in draft_after_graph.sections:
            if not _is_static_1_3_section_id(section.id):
                continue
            prior = by_id.get(section.id)
            by_id[section.id] = (
                _prefer_richer_section(prior, section) if prior is not None else section
            )
        ordered_ids: list[str] = []
        for section in sections_1_3:
            if section.id not in ordered_ids:
                ordered_ids.append(section.id)
        for section in draft_after_graph.sections:
            if _is_static_1_3_section_id(section.id) and section.id not in ordered_ids:
                ordered_ids.append(section.id)
        sections_1_3 = [by_id[sid] for sid in ordered_ids if sid in by_id]

    def _group_has_content(prefix: str) -> bool:
        return any(
            s.id.startswith(prefix) and (s.content or "").strip() for s in sections_1_3
        )

    missing_groups = [
        label
        for label, prefix, has in (
            ("Section 1 (Company)", "section-1-", _group_has_content("section-1-")),
            ("Section 2 (Team)", "section-2-", _group_has_content("section-2-")),
            ("Section 3 (Our Work)", "section-3-", _group_has_content("section-3-")),
        )
        if not has
    ]

    empty_ids = [s.id for s in sections_1_3 if not (s.content or "").strip()]
    if empty_ids or missing_groups:
        logger.warning(
            "Sections 1–3 first pass incomplete for %s (empty=%s missing_groups=%s) — retrying graph once",
            rfp_id,
            empty_ids,
            missing_groups,
        )
        sections_1_3, brand_voice, provider, section1_editorial = await run_sections_1_3_graph(
            rfp_id=rfp.id,
            rfp_title=rfp.title,
            rfp_client=rfp.client,
            rfp_sector=rfp.sector,
            rfp_location=rfp.location or None,
            rfp_context=rfp_context,
            page_limit=rfp.page_limit,
            on_sections_partial=_on_sections_partial,
            existing_sections=existing_sections_for_graph,
            skip_section_1=skip_section_1 or _group_has_content("section-1-"),
            skip_section_2=skip_section_2 or _group_has_content("section-2-"),
            skip_section_3=skip_section_3 or _group_has_content("section-3-"),
        )
        # Re-fold draft after retry
        draft_after_retry = get_proposal_draft(rfp_id)
        if draft_after_retry:
            by_id = {s.id: s for s in sections_1_3}
            for section in draft_after_retry.sections:
                if not _is_static_1_3_section_id(section.id):
                    continue
                prior = by_id.get(section.id)
                by_id[section.id] = (
                    _prefer_richer_section(prior, section) if prior is not None else section
                )
            ordered_ids = []
            for section in sections_1_3:
                if section.id not in ordered_ids:
                    ordered_ids.append(section.id)
            for section in draft_after_retry.sections:
                if _is_static_1_3_section_id(section.id) and section.id not in ordered_ids:
                    ordered_ids.append(section.id)
            sections_1_3 = [by_id[sid] for sid in ordered_ids if sid in by_id]

        empty_ids = [s.id for s in sections_1_3 if not (s.content or "").strip()]
        still_missing = [
            label
            for label, prefix in (
                ("Section 1 (Company)", "section-1-"),
                ("Section 2 (Team)", "section-2-"),
                ("Section 3 (Our Work)", "section-3-"),
            )
            if not any(
                s.id.startswith(prefix) and (s.content or "").strip() for s in sections_1_3
            )
        ]
        if empty_ids:
            from app.services.proposal_section_editor import improve_proposal_section

            logger.warning(
                "Sections 1–3 graph still empty %s for %s — targeted improve pass",
                empty_ids,
                rfp_id,
            )
            for sid in empty_ids:
                section = next((s for s in sections_1_3 if s.id == sid), None)
                if not section:
                    continue
                try:
                    improved, _, _, _, _ = await improve_proposal_section(
                        rfp_id,
                        sid,
                        "Generate the full section from the knowledge base. "
                        "Use [E#] citations. Meet the word target. No placeholders.",
                        persist=True,
                    )
                    if (improved.content or "").strip():
                        sections_1_3 = [
                            improved if s.id == sid else s for s in sections_1_3
                        ]
                except Exception as exc:
                    logger.warning(
                        "Targeted improve failed for %s (%s): %s", rfp_id, sid, exc
                    )
            empty_ids = [s.id for s in sections_1_3 if not (s.content or "").strip()]
            still_missing = [
                label
                for label, prefix in (
                    ("Section 1 (Company)", "section-1-"),
                    ("Section 2 (Team)", "section-2-"),
                    ("Section 3 (Our Work)", "section-3-"),
                )
                if not any(
                    s.id.startswith(prefix) and (s.content or "").strip()
                    for s in sections_1_3
                )
            ]
            if empty_ids:
                titles = [s.title for s in sections_1_3 if s.id in empty_ids]
                raise ProposalError(
                    "Sections 1–3 generation produced empty content for: "
                    f"{', '.join(titles)}. Check KB (02_ company overview, 04 bios, 03_CS) and retry.",
                    status_code=502,
                )
        if still_missing:
            raise ProposalError(
                "Sections 1–3 incomplete after generation — missing: "
                f"{', '.join(still_missing)}. Click Reset, then Draft Sections 1–3 again.",
                status_code=502,
            )

    now = datetime.now(timezone.utc).isoformat()
    existing = get_proposal_draft(rfp_id)
    base_sections = []
    if existing:
        for s in existing.sections:
            is_static_1_3 = (
                s.id.startswith(("section-1-", "section-2-", "section-3-"))
                or s.id in {"section-1-company-overview", "section-2-team-overview", "section-3-our-work"}
            )
            if not is_static_1_3:
                base_sections.append(s)
    else:
        for s in _default_sections(rfp.page_limit):
            is_static_1_3 = (
                s.id.startswith(("section-1-", "section-2-", "section-3-"))
                or s.id in {"section-1-company-overview", "section-2-team-overview", "section-3-our-work"}
            )
            if not is_static_1_3:
                base_sections.append(s)

    merged = [*sections_1_3, *base_sections]


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
        section1EditorialReview=section1_editorial or (
            prior_research.section1_editorial_review if prior_research else None
        ),
        pipelineCheckpoint=prior_research.pipeline_checkpoint if prior_research else None,
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
    if not _phase2_plan_ready(research):
        raise ProposalError(
            "Phase 2 Proposal Execution Plan required. Run Phase 2 intelligence first.",
            status_code=400,
        )
    assert research is not None
    if not research.rfp_sections:
        raise ProposalError(
            "No RFP sections mapped. Re-run Phase 2.",
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

    async def _on_phase3_batch(
        drafted_sections: list[ProposalSection],
        batch_provider: str,
    ) -> None:
        await _persist_phase3_partial(
            rfp_id,
            static_sections=static_sections,
            drafted_rfp_sections=drafted_sections,
            rfp_sections=research.rfp_sections,
            provider=batch_provider,
        )

    await _persist_phase3_partial(
        rfp_id,
        static_sections=static_sections,
        drafted_rfp_sections=[],
        rfp_sections=research.rfp_sections,
        provider="phase-3",
    )

    drafted_rfp_sections, provider, jit_corpus = await run_drafting_graph(
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
        proof_points=research.proof_points,
        execution_plan=(
            research.proposal_execution_plan.model_dump(by_alias=True)
            if hasattr(research.proposal_execution_plan, "model_dump")
            else research.proposal_execution_plan
        ),
        on_sections_drafted=_on_phase3_batch,
    )

    if jit_corpus:
        research = research.model_copy(update={"evidence_corpus": jit_corpus})
        save_research_cache(research)

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


async def run_phase3_6_self_edit(rfp_id: str):
    """Phase 3.6: senior-editor self-edit loop (section-wise KB repair)."""
    return await run_self_edit_loop(rfp_id)


async def run_phase3_5_budget_reconcile(
    rfp_id: str,
) -> tuple[ProposalDraft, ProposalResearchCache, ProposalBudget]:
    """Reconcile cached budget math, re-render budget section, sync fee narrative (no LLM regen)."""
    from app.services.proposal_pricing_service import reconcile_cached_budget

    budget, research = reconcile_cached_budget(rfp_id)
    draft = incorporate_budget_into_draft(rfp_id, budget)
    if not draft:
        raise ProposalError("No proposal draft to incorporate budget.", status_code=400)

    draft = await align_fee_narrative_with_budget(
        rfp_id=rfp_id,
        draft=draft,
        budget=budget,
    )
    save_proposal_draft(draft)

    budget = run_budget_editor_pass(
        budget,
        rfp_sections=research.rfp_sections if research else [],
        rfp_context=load_rfp_for_proposal(rfp_id)[2][:28_000],
    )
    research = research.model_copy(update={"budget": budget})
    save_research_cache(research)
    final_draft = incorporate_budget_into_draft(rfp_id, budget)
    if final_draft:
        draft = final_draft
        save_proposal_draft(draft)

    logger.info(
        "Budget reconcile complete for %s: revenue=%s, passthrough=%s, invoicing=%s",
        rfp_id,
        budget.agency_revenue_estimate,
        budget.client_media_passthrough,
        budget.total_client_invoicing,
    )
    return draft, research, budget


def _assert_proposal_not_reset(rfp_id: str) -> None:
    """Refuse to persist if the user reset the proposal while a phase was running."""
    draft = get_proposal_draft(rfp_id)
    if draft is None:
        raise ProposalError(
            "Proposal was reset while this step was running. Progress was discarded.",
            status_code=409,
        )


async def run_phase3_5_budget(
    rfp_id: str,
) -> tuple[ProposalDraft, ProposalResearchCache, ProposalBudget]:
    """Phase 3.5: Stage 3 budget from 00_Guide_Pricing, incorporate into manuscript, sync fee narrative."""
    if not llm.is_configured():
        raise ProposalError("LLM not configured.", status_code=503)

    draft_existing = get_proposal_draft(rfp_id)
    if not draft_existing or not any(s.content.strip() for s in draft_existing.sections):
        raise ProposalError(
            "Phase 3 manuscript required before budget. Run full proposal or Phase 3 drafting first.",
            status_code=400,
        )

    logger.info("Phase 3.5 budget starting for %s", rfp_id)
    budget, research = await generate_proposal_budget(rfp_id)

    # User may have clicked Reset while budget was computing — do not rewrite wiped data.
    _assert_proposal_not_reset(rfp_id)

    draft = incorporate_budget_into_draft(rfp_id, budget)
    if not draft:
        raise ProposalError("No proposal draft to incorporate budget.", status_code=400)

    draft = await align_fee_narrative_with_budget(
        rfp_id=rfp_id,
        draft=draft,
        budget=budget,
    )
    _assert_proposal_not_reset(rfp_id)
    save_proposal_draft(draft)

    # Re-render budget section after fee sync so narrative totals stay aligned
    try:
        budget = run_budget_editor_pass(
            budget,
            rfp_sections=research.rfp_sections if research else [],
            rfp_context=load_rfp_for_proposal(rfp_id)[2][:28_000],
        )
    except Exception as exc:
        logger.exception("Budget editor pass after fee sync failed for %s: %s", rfp_id, exc)
        raise ProposalError(
            f"Budget editor pass failed after fee sync: {exc}",
            status_code=502,
        ) from exc

    _assert_proposal_not_reset(rfp_id)
    if research:
        research = research.model_copy(update={"budget": budget})
        save_research_cache(research)
    final_draft = incorporate_budget_into_draft(rfp_id, budget)
    if final_draft:
        draft = final_draft
        _assert_proposal_not_reset(rfp_id)
        save_proposal_draft(draft)

    logger.info(
        "Phase 3.5 budget complete for %s: tier=%s, %d line items, revenue=%s",
        rfp_id,
        budget.pricing_tier,
        len(budget.line_items),
        budget.agency_revenue_estimate,
    )
    return draft, research, budget


async def run_phase4_presubmit_review(rfp_id: str) -> tuple[PreSubmitReview, ProposalResearchCache]:
    """Stage 4: pre-submit copy-paste scan + compliance checklist."""
    rfp = get_rfp(rfp_id)
    if not rfp:
        raise ProposalError("RFP not found", status_code=404)

    draft = get_proposal_draft(rfp_id)
    if not draft or not any(s.content.strip() for s in draft.sections):
        raise ProposalError(
            "No proposal content to review. Generate proposal sections first.",
            status_code=400,
        )

    research = get_research_cache(rfp_id)
    from app.services.proposal_presubmit_review import run_presubmit_review_with_manual_flags

    review = run_presubmit_review_with_manual_flags(
        rfp=rfp, draft=draft, research=research, finalized=False
    )

    now = datetime.now(timezone.utc).isoformat()
    updated_research = (research or ProposalResearchCache(rfpId=rfp_id, updatedAt=now)).model_copy(
        update={"presubmit_review": review, "updated_at": now}
    )
    save_research_cache(updated_research)

    logger.info(
        "Phase 4 pre-submit review for %s: %d issues, ready=%s",
        rfp_id,
        len(review.issues),
        review.ready_to_submit,
    )
    return review, updated_research


async def run_phase4_presubmit_autofix(
    rfp_id: str,
    *,
    use_llm: bool = True,
    should_cancel: Callable[[], Awaitable[bool]] | None = None,
) -> tuple[PreSubmitReview, ProposalResearchCache, ProposalDraft, PreSubmitAutoFixReport]:
    """Run bounded auto-fix passes on review findings, then re-scan."""
    rfp = get_rfp(rfp_id)
    if not rfp:
        raise ProposalError("RFP not found", status_code=404)

    draft = get_proposal_draft(rfp_id)
    if not draft or not any(s.content.strip() for s in draft.sections):
        raise ProposalError(
            "No proposal content to fix. Generate proposal sections first.",
            status_code=400,
        )

    research = get_research_cache(rfp_id)
    issues_before = len(
        run_presubmit_review(rfp=rfp, draft=draft, research=research).issues
    )

    updated_draft, review, section_logs, stopped_reason, iterations_run, updated_research_cache, sections_targeted = (
        await run_presubmit_autofix_loop(
            rfp=rfp,
            draft=draft,
            research=research,
            use_llm=use_llm,
            should_cancel=should_cancel,
        )
    )

    save_proposal_draft(updated_draft)

    now = datetime.now(timezone.utc).isoformat()
    base_research = updated_research_cache or research
    updated_research = (base_research or ProposalResearchCache(rfpId=rfp_id, updatedAt=now)).model_copy(
        update={"presubmit_review": review, "updated_at": now}
    )
    save_research_cache(updated_research)

    report = PreSubmitAutoFixReport(
        iterations_run=iterations_run,
        issues_before=issues_before,
        issues_after=len(review.issues),
        sections_patched=len(section_logs),
        sections_targeted=sections_targeted,
        stopped_reason=stopped_reason,
        section_logs=section_logs,
    )

    logger.info(
        "Phase 4 auto-fix for %s: %d → %d issues, %d sections patched, stopped=%s",
        rfp_id,
        report.issues_before,
        report.issues_after,
        report.sections_patched,
        stopped_reason,
    )
    return review, updated_research, updated_draft, report


async def run_phase4_finalize_gaps(
    rfp_id: str,
) -> tuple[PreSubmitReview, ProposalResearchCache, ProposalDraft]:
    """Final editor: Supermemory gap-fill + owner-assigned MANUAL FILL flags."""
    rfp = get_rfp(rfp_id)
    if not rfp:
        raise ProposalError("RFP not found", status_code=404)

    draft = get_proposal_draft(rfp_id)
    if not draft or not any(s.content.strip() for s in draft.sections):
        raise ProposalError(
            "No proposal content to finalize. Generate proposal sections first.",
            status_code=400,
        )

    research = get_research_cache(rfp_id)
    from app.services.proposal_submission_gap_finalizer import (
        attach_manual_fill_flags_to_review,
        run_submission_gap_finalize_pass,
    )
    from app.services.proposal_presubmit_review import run_presubmit_review

    updated_draft, logs, updated_research = await run_submission_gap_finalize_pass(
        rfp_id,
        rfp=rfp,
        draft=draft,
        research=research,
    )
    if logs:
        logger.info("Phase 4 finalize gaps for %s: %s", rfp_id, "; ".join(logs[:5]))

    review = run_presubmit_review(rfp=rfp, draft=updated_draft, research=updated_research)
    review = attach_manual_fill_flags_to_review(
        review,
        draft=updated_draft,
        research=updated_research,
        rfp=rfp,
        kb_searched=True,
        finalized=True,
    )

    now = datetime.now(timezone.utc).isoformat()
    saved_research = (
        updated_research or ProposalResearchCache(rfpId=rfp_id, updatedAt=now)
    ).model_copy(update={"presubmit_review": review, "updated_at": now})
    save_research_cache(saved_research)
    save_proposal_draft(updated_draft)

    logger.info(
        "Phase 4 finalize gaps for %s: %d manual fill flag(s), ready=%s",
        rfp_id,
        len(review.manual_fill_flags),
        review.ready_to_submit,
    )
    return review, saved_research, updated_draft


async def generate_full_proposal(
    rfp_id: str,
) -> tuple[ProposalDraft, ProposalBrandVoice, ProposalResearchCache]:
    """Full pipeline: Sections 1–3 → Phase 2 retrieval → Phase 3 drafting → Phase 3.5 budget."""
    if not llm.is_configured():
        raise ProposalError("LLM not configured.", status_code=503)

    logger.info("Full proposal pipeline starting for %s", rfp_id)

    _draft, brand_voice, _research = await generate_sections_1_3(rfp_id)
    await run_phase2_retrieval(rfp_id)
    draft, research = await run_phase3_drafting(rfp_id)
    draft, research, edit_report = await run_phase3_6_self_edit(rfp_id)
    draft, research, _budget = await run_phase3_5_budget(rfp_id)

    if brand_voice and not research.brand_voice:
        research = research.model_copy(update={"brand_voice": brand_voice})
        save_research_cache(research)

    rfp = get_rfp(rfp_id)
    if rfp:
        extra_issues = self_edit_exhausted_issues(edit_report.section_logs, draft)
        review = run_presubmit_review(
            rfp=rfp,
            draft=draft,
            research=research,
            extra_issues=extra_issues,
        )
        now = datetime.now(timezone.utc).isoformat()
        research = research.model_copy(
            update={"presubmit_review": review, "updated_at": now}
        )
        save_research_cache(research)
        logger.info(
            "Phase 4 pre-submit review (auto) for %s: %d issues, ready=%s",
            rfp_id,
            len(review.issues),
            review.ready_to_submit,
        )

    assert_manuscript_ready(
        draft=draft,
        research=research,
        rfp=rfp,
        require_budget=True,
    )

    logger.info(
        "Full proposal complete for %s: %d sections, budget tier=%s",
        rfp_id,
        len(draft.sections),
        research.budget.pricing_tier if research.budget else "n/a",
    )
    return draft, brand_voice, research
