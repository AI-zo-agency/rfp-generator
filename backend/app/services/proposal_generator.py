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
    should_skip_rfp_section_as_static_duplicate,
)
from app.services.proposal_repository import (
    aget_proposal_draft,
    aget_research_cache,
    asave_proposal_draft,
    asave_research_cache,
)
from app.services.proposal_common import aload_rfp_for_proposal
from app.services.proposal_drafting_graph import (
    MIN_SECTION_WORDS,
    WORDS_PER_PAGE,
    run_drafting_graph,
)
from app.services.proposal_budget_content import incorporate_budget_into_draft
from app.services.proposal_budget_editor import run_budget_editor_pass
from app.services.proposal_budget_sync import (
    align_fee_narrative_with_budget,
    run_budget_grounding_check,
)
from app.services.proposal_consistency import self_edit_exhausted_issues
from app.services.proposal_fee_justification import generate_fee_justification_memo
from app.services.proposal_loss_lessons import build_loss_lessons_for_rfp
from app.services.proposal_pipeline_status import assert_manuscript_ready
from app.services.proposal_pricing_service import generate_proposal_budget
from app.services.proposal_presubmit_review import (
    run_presubmit_review,
    run_presubmit_review_with_manual_flags,
)
from app.services.proposal_presubmit_autofix import run_presubmit_autofix_loop
from app.services.proposal_manuscript_auditor import (
    persist_manuscript_audit,
    run_manuscript_auditor,
)
from app.services.proposal_adversarial_repair import (
    adversarial_repair_blocking_issues,
    run_adversarial_repair_loop,
)
from app.services.proposal_intelligence.graph import run_intelligence_graph
from app.services.proposal_intelligence.plan_ops import IntelligenceError
from app.services.proposal_intelligence.schemas import ProposalExecutionPlan
from app.services.proposal_self_edit_loop import run_self_edit_loop
from app.services.proposal_sections_graph import run_sections_1_3_graph
from app.services.rfp_repository import get_rfp
from app.core.step_debug_logger import (
    pipeline_phase,
    pipeline_run,
    pipeline_step,
    step_trace,
    summarize_budget,
    summarize_sections,
)

logger = logging.getLogger(__name__)


async def _attach_phase4_manuscript_audit(
    *,
    rfp: RfpRecord,
    draft: ProposalDraft,
    research: ProposalResearchCache | None,
    use_llm: bool = True,
) -> ProposalResearchCache:
    """Persist whole-manuscript audit findings without mutating draft content."""
    audit = await run_manuscript_auditor(
        draft=draft,
        research=research,
        rfp=rfp,
        use_llm=use_llm,
    )
    updated = persist_manuscript_audit(research, audit)
    critical = sum(1 for finding in audit.findings if finding.severity == "critical")
    logger.info(
        "Phase 4 adversarial audit for %s: findings=%d critical=%d provider=%s",
        rfp.id,
        len(audit.findings),
        critical,
        audit.provider,
    )
    by_sev: dict[str, int] = {}
    by_family: dict[str, int] = {}
    for finding in audit.findings:
        sev = str(getattr(finding, "severity", "") or "unknown")
        by_sev[sev] = by_sev.get(sev, 0) + 1
        fam = str(getattr(finding, "family", "") or getattr(finding, "category", "") or "other")
        by_family[fam] = by_family.get(fam, 0) + 1
    step_trace(
        "manuscript_audit_complete",
        rfp_id=rfp.id,
        phase="phase-4",
        findings=len(audit.findings),
        critical=critical,
        by_severity=by_sev,
        by_family=dict(list(by_family.items())[:20]),
        provider=str(audit.provider or ""),
        draft_summary=summarize_sections(draft.sections if draft else []),
    )
    return updated

ZO_SECTIONS: list[dict[str, object]] = [
    # Section 1 — Company Overview subsections
    {
        "id": "section-1-who-we-are",
        "title": "1.1 — Who We Are",
        "mode": "pull",
        "source": "template",
        "word_target": 250,
        "designer_note": "Section 1 subsection: 1.1 — Who We Are (max 250 words).",
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

# Pre-subsection monoliths — never keep these once 1.1–1.5 / bios / work cards exist.
LEGACY_MONOLITH_SECTION_IDS = frozenset(STATIC_SECTION_IDS)


def _is_legacy_monolith_section_id(section_id: str) -> bool:
    return section_id in LEGACY_MONOLITH_SECTION_IDS


def _strip_legacy_monolith_sections(
    sections: list[ProposalSection],
) -> list[ProposalSection]:
    """Drop old single-block Sections 1–3 so stale client pitches cannot reappear."""
    return [s for s in sections if not _is_legacy_monolith_section_id(s.id)]


# Section 1 is not "complete" if 1.2–1.5 filled but 1.1 Who We Are is still empty.
_SECTION_1_REQUIRED_IDS: tuple[str, ...] = (
    "section-1-who-we-are",
    "section-1-org-structure",
    "section-1-business-info",
    "section-1-certifications",
    "section-1-insurance",
)


def section_1_subsections_complete(sections: list[ProposalSection]) -> bool:
    """True only when every required Section 1 card (incl. Who We Are) has body text."""
    by_id = {s.id: s for s in sections}
    return all(
        bool((by_id.get(sid) and (by_id[sid].content or "").strip()))
        for sid in _SECTION_1_REQUIRED_IDS
    )


def static_sections_1_3_have_content(draft: ProposalDraft | None) -> bool:
    """True when all three zö template sections have body text (modern subsections only)."""
    if not draft:
        return False
    has_section1 = section_1_subsections_complete(draft.sections)
    has_section2 = any(
        (
            (s.id.startswith("section-2-bio-") and s.id != "section-2-bio-placeholder")
            or s.id == "section-2-team-overview"
        )
        and (s.content or "").strip()
        for s in draft.sections
    )
    has_section3 = any(
        (
            (s.id.startswith("section-3-work-") and s.id != "section-3-work-placeholder")
            or s.id == "section-3-our-work"
        )
        and (s.content or "").strip()
        for s in draft.sections
    )
    return has_section1 and has_section2 and has_section3



from app.services.proposal_common import ProposalError, can_start_proposal, load_rfp_for_proposal


# Share of the page limit held back for content written outside the drafting
# graph — budget/cost tables, closing forms, signature blocks. Without a reserve
# the drafting graph spends the whole allowance and those sections push the
# manuscript back over the limit.
_NON_DRAFT_RESERVE = 0.15


def _remaining_word_budget(
    *,
    rfp: RfpRecord,
    already_written: list[ProposalSection],
    drafting_count: int,
) -> int | None:
    """Words the drafting graph may spend, given the RFP's page limit.

    Returns None when the RFP states no page limit — sections then keep their
    natural targets, as before.
    """
    if not rfp.page_limit or rfp.page_limit <= 0 or drafting_count <= 0:
        return None

    total = rfp.page_limit * WORDS_PER_PAGE
    spent = sum(
        len((section.content or "").split())
        for section in already_written
        if (section.content or "").strip()
    )
    reserve = int(total * _NON_DRAFT_RESERVE)
    remaining = total - spent - reserve

    floor = MIN_SECTION_WORDS * drafting_count
    if remaining < floor:
        logger.warning(
            "page budget exhausted for %s: limit=%dpg total=%dw spent=%dw "
            "reserve=%dw remaining=%dw < floor=%dw — drafting at the floor; "
            "outline is too large for the page limit",
            rfp.id,
            rfp.page_limit,
            total,
            spent,
            reserve,
            remaining,
            floor,
        )
        return floor

    logger.info(
        "page budget for %s: limit=%dpg total=%dw spent=%dw reserve=%dw "
        "available=%dw across %d sections",
        rfp.id,
        rfp.page_limit,
        total,
        spent,
        reserve,
        remaining,
        drafting_count,
    )
    return remaining


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
        f"{query} {rfp.sector}",
        limit=6,
        rfp_client=rfp.client,
        rfp_sector=rfp.sector,
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
        sector=rfp.sector,
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
    await asave_research_cache(research)

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
    await asave_proposal_draft(draft)

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
        if _is_legacy_monolith_section_id(s.id):
            continue
        is_static_1_3 = s.id.startswith(("section-1-", "section-2-bio-", "section-3-work-"))
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


# Optional shells — empty by design until dynamic cards replace them.
# Never treat these (or any *-placeholder) as "incomplete content" that
# warrants a full graph retry / token burn.
_SECTION_PLACEHOLDER_IDS = frozenset(
    {
        "section-2-bio-placeholder",
        "section-3-work-placeholder",
    }
)


def _is_section_placeholder_id(section_id: str) -> bool:
    return (
        section_id in _SECTION_PLACEHOLDER_IDS
        or section_id.endswith("-placeholder")
    )


def _is_optional_empty_shell(section: ProposalSection) -> bool:
    """True for structural / stub shells that must not block the pipeline."""
    sid = section.id or ""
    title = (section.title or "").lower()
    body = (section.content or "").strip()
    if _is_section_placeholder_id(sid):
        return True
    if not body and (
        "generated per" in title
        or "(generated" in title
        or title.endswith("(placeholder)")
        or " — outline" in title
    ):
        return True
    # Chat/KB stubs: VERIFY-only outline content is handoff, not a graph failure.
    if body and body.count("[VERIFY:") >= 2 and len(body) < 400:
        return True
    return False


def _strip_satisfied_placeholders(
    sections: list[ProposalSection],
) -> list[ProposalSection]:
    """Drop bio/work placeholders once real dynamic cards exist."""
    has_bios = any(
        s.id.startswith("section-2-bio-")
        and not _is_section_placeholder_id(s.id)
        and (s.content or "").strip()
        and not _is_optional_empty_shell(s)
        for s in sections
    )
    has_work = any(
        s.id.startswith("section-3-work-")
        and not _is_section_placeholder_id(s.id)
        and (s.content or "").strip()
        and not _is_optional_empty_shell(s)
        for s in sections
    )
    out: list[ProposalSection] = []
    for s in sections:
        if s.id == "section-2-bio-placeholder" and has_bios:
            continue
        if s.id == "section-3-work-placeholder" and has_work:
            continue
        out.append(s)
    return out


def _empty_required_section_ids(sections: list[ProposalSection]) -> list[str]:
    """Only required Section 1 cards that are still empty.

    Dynamic bios / case studies / RFP tabs are tracked via missing_groups
    (or later phases), not by listing every empty shell as a retry trigger.
    """
    by_id = {s.id: s for s in sections}
    empty: list[str] = []
    for sid in _SECTION_1_REQUIRED_IDS:
        section = by_id.get(sid)
        if section is None or not (section.content or "").strip():
            empty.append(sid)
    return empty


# Back-compat alias used in older call sites / tests.
def _empty_content_section_ids(sections: list[ProposalSection]) -> list[str]:
    return _empty_required_section_ids(sections)


def _is_static_1_3_section_id(section_id: str) -> bool:
    if _is_legacy_monolith_section_id(section_id):
        return False
    return section_id.startswith(("section-1-", "section-2-", "section-3-"))


def _prefer_richer_section(current: ProposalSection, incoming: ProposalSection) -> ProposalSection:
    """Never let an empty parallel-track emit wipe content already saved by another track."""
    current_has = bool(current.content and current.content.strip())
    incoming_has = bool(incoming.content and incoming.content.strip())
    if incoming_has:
        return incoming
    if current_has:
        return current
    return incoming


async def _incremental_fact_check_after_sections(
    rfp_id: str,
    section_ids: list[str],
) -> None:
    """Run KB fact-check agent on sections just generated (before next section drafts)."""
    ids = list(dict.fromkeys(sid for sid in section_ids if sid))
    if not ids:
        return
    draft = await aget_proposal_draft(rfp_id)
    if not draft:
        return
    try:
        rfp, _, rfp_context = await aload_rfp_for_proposal(rfp_id)
        research = await aget_research_cache(rfp_id)
        from app.services.proposal_kb_fact_checker import run_kb_fact_check_section_ids
        from app.services.proposal_intelligence.log import log_intel_event

        draft, fc_report = await run_kb_fact_check_section_ids(
            draft,
            ids,
            rfp=rfp,
            rfp_context=rfp_context,
            research=research,
        )
        await asave_proposal_draft(draft)
        if fc_report.logs:
            logger.info(
                "KB fact-check after %s for %s: %s",
                ", ".join(ids[:3]),
                rfp_id,
                "; ".join(fc_report.logs[:3]),
            )
        log_intel_event(
            "SECTION_FACT_CHECK_DONE",
            rfp_id=rfp_id,
            section_ids=",".join(ids[:8]),
            repairs=fc_report.requirement_repairs,
            verify_fills=fc_report.verify_tags_filled,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Incremental KB fact-check failed for %s %s: %s", rfp_id, ids, exc)


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
    partial_emit = list(sections_1_3)
    rfp = get_rfp(rfp_id)
    page_limit = rfp.page_limit if rfp else 30
    existing = await aget_proposal_draft(rfp_id)
    prior_content_by_id = (
        {s.id: (s.content or "") for s in existing.sections} if existing else {}
    )

    template_1_3 = [
        s
        for s in _default_sections(page_limit)
        if s.id.startswith(("section-1-", "section-2-", "section-3-"))
    ]

    by_id: dict[str, ProposalSection] = {s.id: s for s in template_1_3}
    # Keep any previously persisted 1–3 content (other parallel track).
    # Never resurrect legacy monoliths (e.g. HCCC-era "Section 1 — Company Overview").
    if existing:
        for section in existing.sections:
            if _is_static_1_3_section_id(section.id):
                by_id[section.id] = section
    # Apply this emit — only overwrite when incoming has real content (or new ids).
    for section in sections_1_3:
        if _is_legacy_monolith_section_id(section.id):
            continue
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

    merged = _strip_legacy_monolith_sections([*sections_1_3, *base_sections])

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
        generatedAt=(existing.generated_at if existing and existing.generated_at else now),
        provider=provider,
        googleDocUrl=existing.google_doc_url if existing else None,
        googleDocId=existing.google_doc_id if existing else None,
        googleDocExportedAt=existing.google_doc_exported_at if existing else None,
        snapshots=list(existing.snapshots) if existing else [],
        lastFulfillReport=existing.last_fulfill_report if existing else None,
    )
    await asave_proposal_draft(draft)

    fact_check_ids = [
        s.id
        for s in partial_emit
        if (s.content or "").strip()
        and not _is_legacy_monolith_section_id(s.id)
        and prior_content_by_id.get(s.id) != (s.content or "").strip()
    ]
    if fact_check_ids:
        await _incremental_fact_check_after_sections(rfp_id, fact_check_ids)

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
    existing = await aget_proposal_draft(rfp_id)
    prior_content_by_id = (
        {s.id: (s.content or "") for s in existing.sections} if existing else {}
    )
    drafted_ids = {section.id for section in drafted_rfp_sections}
    stubs: list[ProposalSection] = []
    for mapped in rfp_sections:
        if mapped.id in drafted_ids:
            continue
        if should_skip_rfp_section_as_static_duplicate(
            title=mapped.title or "",
            duplicate_of_static_section=mapped.duplicate_of_static_section,
        ):
            continue
        prior = prior_content_by_id.get(mapped.id, "")
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
                content=prior,
                status="generated" if prior.strip() else "outline",
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

    new_ids = [
        s.id
        for s in drafted_rfp_sections
        if (s.content or "").strip()
        and prior_content_by_id.get(s.id) != (s.content or "").strip()
    ]
    if new_ids:
        await _incremental_fact_check_after_sections(rfp_id, new_ids)


def _load_rfp_for_proposal(rfp_id: str) -> tuple[RfpRecord, RfpContentInfo, str]:
    return load_rfp_for_proposal(rfp_id)


async def run_phase2_retrieval(rfp_id: str) -> ProposalResearchCache:
    """Phase 2: Proposal Intelligence Layer → plan + optional shared evidence corpus."""
    if not llm.is_configured():
        raise ProposalError("LLM not configured.", status_code=503)

    with pipeline_phase("phase-2", rfp_id=rfp_id):
        return await _run_phase2_retrieval_inner(rfp_id)


async def _run_phase2_retrieval_inner(rfp_id: str) -> ProposalResearchCache:
    rfp, _content, rfp_context = _load_rfp_for_proposal(rfp_id)
    prior_research = await aget_research_cache(rfp_id)

    logger.info("Phase 2 intelligence starting for %s", rfp_id)
    from app.services.proposal_generation_cancel import check_generation_cancelled

    await check_generation_cancelled(rfp_id)
    with pipeline_step(
        "intelligence_graph",
        rfp_context_chars=len(rfp_context or ""),
        client=str(rfp.client or "")[:80],
    ):
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
            step_trace(
                "phase2_intelligence_blocked",
                rfp_id=rfp_id,
                reason="IntelligenceError",
                error_message=str(exc)[:300],
            )
            raise ProposalError(str(exc), status_code=422) from exc

    if plan.validation.readiness_status == "blocked":
        step_trace(
            "phase2_plan_blocked",
            rfp_id=rfp_id,
            blockers=list(plan.validation.blockers or [])[:12],
        )
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

    roster_excerpt = ""
    try:
        roster_excerpt, _roster_sources = await proposal_knowledge_base_tools.fetch_master_team_roster(
            rfp_client=rfp.client,
            rfp_sector=rfp.sector,
            rfp_context=rfp_context,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Phase 2 roster fetch for locks failed (non-fatal): %s", exc)

    from app.services.proposal_manuscript_locks import build_manuscript_locks

    manuscript_locks = await build_manuscript_locks(
        rfp=rfp,
        rfp_context=rfp_context,
        plan=plan,
        roster_excerpt=roster_excerpt or "",
    )

    from app.core.config import settings as app_settings
    from app.services.evidence_allocator import build_evidence_allocation_ledger
    from app.services.evidence_corpus_builder import build_shared_evidence_corpus

    evidence_corpus: list = []
    evidence_allocation = None
    if app_settings.persist_phase2_evidence_corpus:
        evidence_corpus = await build_shared_evidence_corpus(
            plan=plan,
            rfp_client=rfp.client,
            proof_points=proof_points,
        )
        evidence_allocation = build_evidence_allocation_ledger(
            proof_points=proof_points,
            evidence_corpus=evidence_corpus,
            rfp_sections=rfp_sections,
        )
        logger.info(
            "Phase 2 shared corpus persisted for %s: items=%d allocation_entries=%d",
            rfp_id,
            len(evidence_corpus),
            len(evidence_allocation.entries),
        )
    else:
        logger.info(
            "Phase 2 corpus persistence disabled (persist_phase2_evidence_corpus=false) for %s",
            rfp_id,
        )

    now = datetime.now(timezone.utc).isoformat()
    research = ProposalResearchCache(
        rfpId=rfp.id,
        rfpSections=rfp_sections,
        questions=prior_research.questions if prior_research else [],
        brandVoice=prior_research.brand_voice if prior_research else None,
        evidenceCorpus=evidence_corpus,
        sectionQueries=section_queries,
        retrievalRounds=0,
        coverageThreshold=85,
        lossLessons=loss_lessons,
        writingAvoidances=writing_avoidances,
        proofPoints=proof_points,
        manuscriptLocks=manuscript_locks,
        proposalExecutionPlan=plan,
        budget=prior_research.budget if prior_research else None,
        presubmitReview=prior_research.presubmit_review if prior_research else None,
        pipelineCheckpoint=prior_research.pipeline_checkpoint if prior_research else None,
        factLedger=prior_research.fact_ledger if prior_research else None,
        evidenceAllocation=(
            evidence_allocation.model_dump(by_alias=True) if evidence_allocation else None
        ),
        updatedAt=now,
        provider=plan.metadata.provider,
    )
    await asave_research_cache(research)

    logger.info(
        "Phase 2 complete for %s: plan=%s sections=%d decisions=%d evidence=%d",
        rfp_id,
        plan.validation.readiness_status,
        len(rfp_sections),
        len(plan.decision_log),
        len(evidence_corpus),
    )
    step_trace(
        "phase2_complete",
        rfp_id=rfp_id,
        readiness=plan.validation.readiness_status,
        rfp_section_count=len(rfp_sections),
        rfp_section_titles=[str(s.title)[:80] for s in rfp_sections[:30]],
        decision_count=len(plan.decision_log),
        proof_point_count=len(proof_points),
        evidence_corpus_count=len(evidence_corpus),
        allocation_entries=(
            len(evidence_allocation.entries) if evidence_allocation else 0
        ),
        corpus_persisted=bool(app_settings.persist_phase2_evidence_corpus),
        loss_lesson_count=len(loss_lessons or []),
        primary_contact=(
            getattr(manuscript_locks, "primary_contact_name", None) or ""
        )[:80],
    )
    for index, section in enumerate(rfp_sections, 1):
        logger.info(
            "  Phase 2 required tab %02d: %s (weight=%s, %d requirements)",
            index,
            section.title,
            section.evaluation_weight,
            len(section.requirements or []),
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

    with pipeline_phase(
        "sections-1-3",
        rfp_id=rfp_id,
        force_regenerate=bool(force_regenerate),
    ):
        return await _generate_sections_1_3_inner(
            rfp_id, force_regenerate=force_regenerate
        )


async def _generate_sections_1_3_inner(
    rfp_id: str,
    *,
    force_regenerate: bool = False,
) -> tuple[ProposalDraft, ProposalBrandVoice, ProposalResearchCache]:
    rfp, _content, rfp_context = _load_rfp_for_proposal(rfp_id)

    existing_draft = await aget_proposal_draft(rfp_id)
    existing_sections_1_3: list[ProposalSection] = []
    has_section1 = has_section2 = has_section3 = False
    existing_section1: list[ProposalSection] = []
    existing_section2: list[ProposalSection] = []
    existing_section3: list[ProposalSection] = []

    if force_regenerate:
        logger.info(
            "Force-regenerating sections 1–3 for %s (soft — no draft delete)",
            rfp_id,
        )
        # Archive + in-draft snapshot first. NEVER delete the proposal_drafts row here —
        # that permanently destroyed filled manuscripts when a later run skipped/failed.
        if existing_draft and any(
            (s.content or "").strip() for s in existing_draft.sections or []
        ):
            from app.services.proposal_draft_archives import (
                REASON_BEFORE_SECTIONS_1_3_REGEN,
                archive_filled_draft,
            )

            await archive_filled_draft(
                existing_draft,
                reason=REASON_BEFORE_SECTIONS_1_3_REGEN,
                label="Before Sections 1–3 regenerate",
            )
            existing_draft = await aget_proposal_draft(rfp_id)
        existing_sections_1_3 = []
        # Fall through to full regen of 1–3 while keeping the live row + RFP tabs.
    elif existing_draft:
        # Check if we already have COMPLETE sections 1-3 with content
        existing_sections_1_3 = [
            s for s in existing_draft.sections
            if s.id.startswith(("section-1-", "section-2-", "section-3-"))
        ]

        # Modern subsections only — a leftover company-overview monolith is NOT "complete".
        # Require ALL Section 1 cards (esp. Who We Are). Filling 1.2–1.5 alone must not
        # skip regeneration and jump to Team Bios.
        has_section1 = section_1_subsections_complete(existing_sections_1_3)
        has_section2 = any(
            s.id.startswith("section-2-bio-")
            and s.id != "section-2-bio-placeholder"
            and s.content.strip()
            for s in existing_sections_1_3
        )
        has_section3 = any(
            s.id.startswith("section-3-work-")
            and s.id != "section-3-work-placeholder"
            and s.content.strip()
            for s in existing_sections_1_3
        )

        if has_section1 and has_section2 and has_section3:
            logger.info(
                "Sections 1–3 already complete for %s — using cached version. "
                "Use RESET to regenerate.",
                rfp_id,
            )
            research = await aget_research_cache(rfp_id)
            brand_voice = (
                research.brand_voice
                if research and research.brand_voice
                else ProposalBrandVoice(
                    tone="professional", style="narrative", voice="first_person"
                )
            )
            step_trace(
                "sections_1_3_cache_hit",
                rfp_id=rfp_id,
                **summarize_sections(
                    [
                        s
                        for s in (existing_draft.sections if existing_draft else [])
                        if str(s.id).startswith(("section-1-", "section-2-", "section-3-"))
                    ]
                ),
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

    # Lock primary contact + RFQ KPIs BEFORE writing Sections 1–3 so Team Bios
    # cannot invent a different day-to-day primary than Methodology will use.
    prior_research = await aget_research_cache(rfp_id)
    manuscript_locks = prior_research.manuscript_locks if prior_research else None
    if manuscript_locks is None or not manuscript_locks.primary_contact_name:
        roster_excerpt = ""
        try:
            roster_excerpt, _ = await proposal_knowledge_base_tools.fetch_master_team_roster(
                rfp_client=rfp.client,
                rfp_sector=rfp.sector,
                rfp_context=rfp_context,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Early locks roster fetch failed (non-fatal): %s", exc)
        from app.services.proposal_manuscript_locks import build_manuscript_locks

        manuscript_locks = await build_manuscript_locks(
            rfp=rfp,
            rfp_context=rfp_context,
            plan=prior_research.proposal_execution_plan if prior_research else None,
            roster_excerpt=roster_excerpt or "",
        )
        now = datetime.now(timezone.utc).isoformat()
        research_seed = prior_research or ProposalResearchCache(
            rfpId=rfp.id,
            updatedAt=now,
        )
        research_seed = research_seed.model_copy(
            update={
                "manuscript_locks": manuscript_locks,
                "updated_at": now,
            }
        )
        await asave_research_cache(research_seed)

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
        manuscript_locks=(
            manuscript_locks.model_dump(by_alias=True) if manuscript_locks else None
        ),
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
    # Never fold legacy monoliths back in after a regenerate.
    draft_after_graph = await aget_proposal_draft(rfp_id)
    if draft_after_graph:
        by_id = {
            s.id: s
            for s in sections_1_3
            if not _is_legacy_monolith_section_id(s.id)
        }
        for section in draft_after_graph.sections:
            if not _is_static_1_3_section_id(section.id):
                continue
            if _is_legacy_monolith_section_id(section.id):
                continue
            prior = by_id.get(section.id)
            by_id[section.id] = (
                _prefer_richer_section(prior, section) if prior is not None else section
            )
        ordered_ids: list[str] = []
        for section in sections_1_3:
            if _is_legacy_monolith_section_id(section.id):
                continue
            if section.id not in ordered_ids:
                ordered_ids.append(section.id)
        for section in draft_after_graph.sections:
            if (
                _is_static_1_3_section_id(section.id)
                and not _is_legacy_monolith_section_id(section.id)
                and section.id not in ordered_ids
            ):
                ordered_ids.append(section.id)
        sections_1_3 = [by_id[sid] for sid in ordered_ids if sid in by_id]

    def _group_has_content(prefix: str) -> bool:
        if prefix == "section-1-":
            return section_1_subsections_complete(sections_1_3)
        return any(
            s.id.startswith(prefix)
            and not _is_section_placeholder_id(s.id)
            and (s.content or "").strip()
            and not _is_optional_empty_shell(s)
            for s in sections_1_3
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

    # Drop structural shells once real bios / case studies exist so they cannot
    # trigger a full graph retry (empty placeholders were wasting tokens in prod).
    sections_1_3 = _strip_satisfied_placeholders(sections_1_3)

    empty_ids = _empty_required_section_ids(sections_1_3)
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
            # Never skip Section 1 while Who We Are (or any required 1.x) is empty —
            # that was causing Team Bios to run with 1.1 still OUTLINE.
            skip_section_1=skip_section_1 or section_1_subsections_complete(sections_1_3),
            skip_section_2=skip_section_2 or _group_has_content("section-2-"),
            skip_section_3=skip_section_3 or _group_has_content("section-3-"),
        )
        # Re-fold draft after retry — still never resurrect legacy monoliths
        draft_after_retry = await aget_proposal_draft(rfp_id)
        if draft_after_retry:
            by_id = {
                s.id: s
                for s in sections_1_3
                if not _is_legacy_monolith_section_id(s.id)
            }
            for section in draft_after_retry.sections:
                if not _is_static_1_3_section_id(section.id):
                    continue
                if _is_legacy_monolith_section_id(section.id):
                    continue
                prior = by_id.get(section.id)
                by_id[section.id] = (
                    _prefer_richer_section(prior, section) if prior is not None else section
                )
            ordered_ids = []
            for section in sections_1_3:
                if _is_legacy_monolith_section_id(section.id):
                    continue
                if section.id not in ordered_ids:
                    ordered_ids.append(section.id)
            for section in draft_after_retry.sections:
                if (
                    _is_static_1_3_section_id(section.id)
                    and not _is_legacy_monolith_section_id(section.id)
                    and section.id not in ordered_ids
                ):
                    ordered_ids.append(section.id)
            sections_1_3 = [by_id[sid] for sid in ordered_ids if sid in by_id]

        sections_1_3 = _strip_satisfied_placeholders(sections_1_3)
        empty_ids = _empty_required_section_ids(sections_1_3)
        still_missing = [
            label
            for label, check in (
                ("Section 1 (Company)", lambda: section_1_subsections_complete(sections_1_3)),
                ("Section 2 (Team)", lambda: _group_has_content("section-2-")),
                ("Section 3 (Our Work)", lambda: _group_has_content("section-3-")),
            )
            if not check()
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
                    improved, _, _, _, _, _ = await improve_proposal_section(
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
            sections_1_3 = _strip_satisfied_placeholders(sections_1_3)
            empty_ids = _empty_required_section_ids(sections_1_3)
            still_missing = [
                label
                for label, check in (
                    (
                        "Section 1 (Company)",
                        lambda: section_1_subsections_complete(sections_1_3),
                    ),
                    ("Section 2 (Team)", lambda: _group_has_content("section-2-")),
                    ("Section 3 (Our Work)", lambda: _group_has_content("section-3-")),
                )
                if not check()
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
    existing = await aget_proposal_draft(rfp_id)
    base_sections = []
    if existing:
        for s in existing.sections:
            if not _is_static_1_3_section_id(s.id) and not _is_legacy_monolith_section_id(s.id):
                base_sections.append(s)
    else:
        for s in _default_sections(rfp.page_limit):
            if not _is_static_1_3_section_id(s.id) and not _is_legacy_monolith_section_id(s.id):
                base_sections.append(s)

    sections_1_3 = _strip_legacy_monolith_sections(sections_1_3)
    merged = _strip_legacy_monolith_sections([*sections_1_3, *base_sections])


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
    from app.services.proposal_integrity_guards import apply_manuscript_integrity_guards

    draft, integrity_logs = apply_manuscript_integrity_guards(draft)
    for line in integrity_logs[:8]:
        logger.info("Sections 1–3 integrity: %s — %s", rfp_id, line)
    await asave_proposal_draft(draft)

    prior_research = await aget_research_cache(rfp_id)
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
    await asave_research_cache(research)

    logger.info("Sections 1–3 complete for %s (run Phase 2 separately for KB retrieval)", rfp_id)
    static_only = [
        s
        for s in draft.sections
        if str(s.id).startswith(("section-1-", "section-2-", "section-3-"))
    ]
    step_trace(
        "sections_1_3_complete",
        rfp_id=rfp_id,
        provider=str(provider or ""),
        **summarize_sections(static_only),
    )
    return draft, brand_voice, research


async def run_phase3_drafting(rfp_id: str) -> tuple[ProposalDraft, ProposalResearchCache]:
    """Phase 3: draft all RFP-mapped sections from evidence corpus with [E#] citations."""
    if not llm.is_configured():
        raise ProposalError("LLM not configured.", status_code=503)

    with pipeline_phase("phase-3", rfp_id=rfp_id):
        return await _run_phase3_drafting_inner(rfp_id)


async def _run_phase3_drafting_inner(
    rfp_id: str,
) -> tuple[ProposalDraft, ProposalResearchCache]:
    rfp, _content, rfp_context = _load_rfp_for_proposal(rfp_id)
    research = await aget_research_cache(rfp_id)
    if not _phase2_plan_ready(research):
        step_trace(
            "phase3_blocked",
            rfp_id=rfp_id,
            reason="phase2_plan_not_ready",
        )
        raise ProposalError(
            "Phase 2 Proposal Execution Plan required. Run Phase 2 intelligence first.",
            status_code=400,
        )
    assert research is not None
    if not research.rfp_sections:
        step_trace(
            "phase3_blocked",
            rfp_id=rfp_id,
            reason="no_rfp_sections",
        )
        raise ProposalError(
            "No RFP sections mapped. Re-run Phase 2.",
            status_code=400,
        )

    existing = await aget_proposal_draft(rfp_id)
    static_sections = _static_sections_from_draft(existing, rfp.page_limit)
    if not any(section.content.strip() for section in static_sections):
        logger.info(
            "Phase 3 for %s: static Sections 1–3 empty — run Generate Sections 1–3 or Full Proposal first",
            rfp_id,
        )
        step_trace(
            "phase3_static_empty_warning",
            rfp_id=rfp_id,
            static_summary=summarize_sections(static_sections),
        )

    existing_by_id = {
        section.id: section for section in (existing.sections if existing else [])
    }
    from app.services.proposal_drafting_graph import partition_phase3_sections

    sections_to_draft, already_filled = partition_phase3_sections(
        research.rfp_sections,
        existing_by_id,
    )

    logger.info(
        "Phase 3 drafting starting for %s (%d to draft, %d already filled, %d evidence items)",
        rfp_id,
        len(sections_to_draft),
        len(already_filled),
        len(research.evidence_corpus),
    )
    step_trace(
        "phase3_draft_plan",
        rfp_id=rfp_id,
        to_draft=len(sections_to_draft),
        already_filled=len(already_filled),
        evidence_corpus_count=len(research.evidence_corpus or []),
        to_draft_titles=[str(s.title)[:80] for s in sections_to_draft[:30]],
        has_fact_ledger=bool(research.fact_ledger),
        has_evidence_allocation=bool(research.evidence_allocation),
    )

    async def _on_phase3_batch(
        drafted_sections: list[ProposalSection],
        batch_provider: str,
    ) -> None:
        await _persist_phase3_partial(
            rfp_id,
            static_sections=static_sections,
            drafted_rfp_sections=[*already_filled, *drafted_sections],
            rfp_sections=research.rfp_sections,
            provider=batch_provider,
        )
        step_trace(
            "phase3_batch_persisted",
            rfp_id=rfp_id,
            provider=batch_provider,
            **summarize_sections(drafted_sections),
        )

    await _persist_phase3_partial(
        rfp_id,
        static_sections=static_sections,
        drafted_rfp_sections=list(already_filled),
        rfp_sections=research.rfp_sections,
        provider="phase-3",
    )

    if not sections_to_draft:
        logger.info("Phase 3 for %s: all draftable RFP sections already filled", rfp_id)
        step_trace(
            "phase3_skipped_all_filled",
            rfp_id=rfp_id,
            **summarize_sections(already_filled),
        )
        draft = await aget_proposal_draft(rfp_id)
        if draft is None:
            raise ProposalError("Proposal draft missing after Phase 3 seed.", status_code=500)
        return draft, research

    doc_word_budget = _remaining_word_budget(
        rfp=rfp,
        already_written=[*static_sections, *already_filled],
        drafting_count=len(sections_to_draft),
    )

    with pipeline_step("drafting_graph", section_count=len(sections_to_draft)):
        drafted_rfp_sections, provider, jit_corpus = await run_drafting_graph(
            rfp_id=rfp.id,
            rfp_title=rfp.title,
            rfp_client=rfp.client,
            rfp_sector=rfp.sector,
            rfp_location=rfp.location or None,
            rfp_context=rfp_context,
            rfp_sections=sections_to_draft,
            evidence_corpus=research.evidence_corpus,
            brand_voice=research.brand_voice,
            zo_template_sections=static_sections,
            writing_avoidances=research.writing_avoidances,
            loss_lessons=research.loss_lessons,
            proof_points=research.proof_points,
            manuscript_locks=(
                research.manuscript_locks.model_dump(by_alias=True)
                if research.manuscript_locks
                else None
            ),
            execution_plan=(
                research.proposal_execution_plan.model_dump(by_alias=True)
                if hasattr(research.proposal_execution_plan, "model_dump")
                else research.proposal_execution_plan
            ),
            fact_ledger=research.fact_ledger,
            evidence_allocation=research.evidence_allocation,
            doc_word_budget=doc_word_budget,
            on_sections_drafted=_on_phase3_batch,
        )

    if jit_corpus:
        research = research.model_copy(update={"evidence_corpus": jit_corpus})
        await asave_research_cache(research)

    merged_sections = _merge_static_with_rfp_sections(
        static_sections,
        [*already_filled, *drafted_rfp_sections],
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
    from app.services.proposal_integrity_guards import apply_manuscript_integrity_guards

    draft, integrity_logs = apply_manuscript_integrity_guards(draft)
    for line in integrity_logs[:12]:
        logger.info("Phase 3 integrity: %s — %s", rfp_id, line)
    await asave_proposal_draft(draft)

    updated_research = research.model_copy(
        update={"updated_at": now, "provider": provider}
    )
    await asave_research_cache(updated_research)

    logger.info(
        "Phase 3 complete for %s: %d static + %d RFP sections (%d total)",
        rfp_id,
        len(static_sections),
        len(drafted_rfp_sections),
        len(merged_sections),
    )
    step_trace(
        "phase3_complete",
        rfp_id=rfp_id,
        provider=str(provider or ""),
        static_count=len(static_sections),
        drafted_count=len(drafted_rfp_sections),
        jit_corpus_count=len(jit_corpus or []),
        drafted_summary=summarize_sections(drafted_rfp_sections),
        manuscript_summary=summarize_sections(merged_sections),
    )
    return draft, updated_research


async def run_phase3_6_self_edit(rfp_id: str):
    """Phase 3.6: senior-editor self-edit loop (section-wise KB repair)."""
    with pipeline_phase("phase-3-6-self-edit", rfp_id=rfp_id):
        draft, research, report = await run_self_edit_loop(rfp_id)
        step_trace(
            "phase3_6_complete",
            rfp_id=rfp_id,
            iterations=getattr(report, "iterations_run", None),
            section_log_count=len(getattr(report, "section_logs", []) or []),
            manuscript_summary=summarize_sections(
                draft.sections if draft else []
            ),
        )
        return draft, research, report


async def run_phase3_5_budget_reconcile(
    rfp_id: str,
) -> tuple[ProposalDraft, ProposalResearchCache, ProposalBudget]:
    """Reconcile cached budget math, re-render budget section, sync fee narrative (no LLM regen)."""
    from app.services.proposal_pricing_service import reconcile_cached_budget

    budget, research = await reconcile_cached_budget(rfp_id)
    rfp_context = load_rfp_for_proposal(rfp_id)[2]
    from app.services.proposal_budget_content import (
        prepare_budget_for_client_display,
        reconcile_draft_budget_summaries,
        rfp_wants_blended_pricing_form,
    )

    if rfp_wants_blended_pricing_form(rfp_context):
        budget = budget.model_copy(update={"budget_format": "blended_rate_form"})

    # Final math BEFORE narrative sync so fee claims cannot drift after.
    budget = run_budget_editor_pass(
        budget,
        rfp_sections=research.rfp_sections if research else [],
        rfp_context=rfp_context[:28_000],
    )
    budget = prepare_budget_for_client_display(budget)
    research = research.model_copy(update={"budget": budget})
    await asave_research_cache(research)

    draft = await incorporate_budget_into_draft(rfp_id, budget, rfp_text=rfp_context)
    if not draft:
        raise ProposalError("No proposal draft to incorporate budget.", status_code=400)

    draft, _ = reconcile_draft_budget_summaries(draft, budget)
    draft = await align_fee_narrative_with_budget(
        rfp_id=rfp_id,
        draft=draft,
        budget=budget,
    )
    mismatches = await run_budget_grounding_check(
        rfp_id=rfp_id,
        draft=draft,
        budget=budget,
    )
    if mismatches:
        from app.services.proposal_pricing_sync_repair import (
            run_pricing_sync_repair_or_handoff,
        )

        step_trace(
            "phase3_5_grounding_mismatches",
            rfp_id=rfp_id,
            mismatch_count=len(mismatches),
            mismatch_sample=[
                (m.note or m.sentence or str(m))[:120] for m in mismatches[:8]
            ],
        )
        draft, research, budget, _sync_report = await run_pricing_sync_repair_or_handoff(
            rfp_id=rfp_id,
            draft=draft,
            budget=budget,
            research=research,
            initial_mismatches=mismatches,
            rfp_text=rfp_context,
        )
    else:
        budget = budget.model_copy(update={"narrative_mismatches": []})
        if research:
            research = research.model_copy(update={"budget": budget})
    if research:
        await asave_research_cache(research)
    await asave_proposal_draft(draft)

    logger.info(
        "Budget reconcile complete for %s: revenue=%s, passthrough=%s, invoicing=%s",
        rfp_id,
        budget.agency_revenue_estimate,
        budget.client_media_passthrough,
        budget.total_client_invoicing,
    )
    return draft, research, budget


async def _assert_proposal_not_reset(rfp_id: str) -> None:
    """Refuse to persist if the user reset the proposal while a phase was running."""
    draft = await aget_proposal_draft(rfp_id)
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

    from app.core.config import settings as app_settings

    draft_existing = await aget_proposal_draft(rfp_id)
    has_manuscript = bool(
        draft_existing and any(s.content.strip() for s in draft_existing.sections)
    )
    if not has_manuscript and not app_settings.budget_before_drafting:
        raise ProposalError(
            "Phase 3 manuscript required before budget. Run full proposal or Phase 3 drafting first.",
            status_code=400,
        )

    logger.info("Phase 3.5 budget starting for %s", rfp_id)
    with pipeline_phase(
        "phase-3-5-budget",
        rfp_id=rfp_id,
        budget_before_drafting=bool(app_settings.budget_before_drafting),
        has_manuscript=has_manuscript,
    ):
        return await _run_phase3_5_budget_inner(
            rfp_id,
            app_settings=app_settings,
            has_manuscript=has_manuscript,
        )


async def _run_phase3_5_budget_inner(
    rfp_id: str,
    *,
    app_settings: object,
    has_manuscript: bool,
) -> tuple[ProposalDraft, ProposalResearchCache, ProposalBudget]:
    step_trace(
        "phase3_5_budget_start",
        rfp_id=rfp_id,
        budget_before_drafting=bool(getattr(app_settings, "budget_before_drafting", False)),
        has_manuscript=has_manuscript,
    )
    try:
        with pipeline_step("generate_proposal_budget"):
            budget, research = await generate_proposal_budget(rfp_id)
    except Exception as exc:  # noqa: BLE001
        step_trace(
            "phase3_5_budget_generate_failed",
            rfp_id=rfp_id,
            error_type=exc.__class__.__name__,
            error_message=str(exc)[:300],
        )
        raise

    rate_card = (research.pricing_rate_card or {}) if research else {}
    rates = rate_card.get("rates") or rate_card.get("Rates") or []
    contract = (research.pricing_contract or {}) if research else {}
    step_trace(
        "phase3_5_budget_llm_generated",
        rfp_id=rfp_id,
        **summarize_budget(budget),
        rate_card_rates=len(rates) if isinstance(rates, list) else 0,
        contract_fee_model=contract.get("feeModel") or contract.get("fee_model"),
        contract_confidence=contract.get("confidence"),
    )

    # User may have clicked Reset while budget was computing — do not rewrite wiped data.
    await _assert_proposal_not_reset(rfp_id)

    rfp_context = load_rfp_for_proposal(rfp_id)[2]
    from app.services.proposal_budget_content import (
        prepare_budget_for_client_display,
        reconcile_draft_budget_summaries,
        rfp_wants_blended_pricing_form,
    )

    if rfp_wants_blended_pricing_form(rfp_context):
        budget = budget.model_copy(update={"budget_format": "blended_rate_form"})

    # Final math first — then manuscript sync/grounding against the frozen totals.
    try:
        budget = run_budget_editor_pass(
            budget,
            rfp_sections=research.rfp_sections if research else [],
            rfp_context=rfp_context[:28_000],
        )
    except Exception as exc:
        logger.exception("Budget editor pass failed for %s: %s", rfp_id, exc)
        step_trace(
            "phase3_5_budget_editor_failed",
            rfp_id=rfp_id,
            error_type=exc.__class__.__name__,
        )
        raise ProposalError(
            f"Budget editor pass failed: {exc}",
            status_code=502,
        ) from exc
    step_trace(
        "phase3_5_budget_editor_ok",
        rfp_id=rfp_id,
        **summarize_budget(budget),
    )

    budget = prepare_budget_for_client_display(budget)
    await _assert_proposal_not_reset(rfp_id)
    if research:
        research = research.model_copy(update={"budget": budget})
        await asave_research_cache(research)

    with pipeline_step("incorporate_budget"):
        draft = await incorporate_budget_into_draft(rfp_id, budget, rfp_text=rfp_context)
    if not draft:
        if getattr(app_settings, "budget_before_drafting", False) and not has_manuscript:
            logger.info(
                "Phase 3.5 budget-before-drafting: no manuscript yet for %s — skipping incorporate",
                rfp_id,
            )
            step_trace(
                "phase3_5_incorporate_skipped",
                rfp_id=rfp_id,
                reason="budget_before_drafting_no_manuscript",
            )
            now = datetime.now(timezone.utc).isoformat()
            draft = ProposalDraft(rfpId=rfp_id, sections=[], updatedAt=now, generatedAt=now)
            await asave_proposal_draft(draft)
            if research:
                research = research.model_copy(update={"budget": budget})
                await asave_research_cache(research)
            return draft, research, budget
        raise ProposalError("No proposal draft to incorporate budget.", status_code=400)

    draft, _ = reconcile_draft_budget_summaries(draft, budget)
    draft = await align_fee_narrative_with_budget(
        rfp_id=rfp_id,
        draft=draft,
        budget=budget,
    )

    from app.services.proposal_budget_slots import render_draft_budget_slots

    draft, unresolved_slots = render_draft_budget_slots(draft, budget)
    if unresolved_slots:
        logger.warning(
            "Phase 3.5 unresolved money slots for %s: %s",
            rfp_id,
            unresolved_slots[:12],
        )
        step_trace(
            "phase3_5_unresolved_money_slots",
            rfp_id=rfp_id,
            unresolved_count=len(unresolved_slots),
            unresolved_sample=list(unresolved_slots)[:12],
        )

    # Phase 3.5d — repair or hand off if manuscript pricing claims contradict canonical budget.
    mismatches = await run_budget_grounding_check(
        rfp_id=rfp_id,
        draft=draft,
        budget=budget,
    )
    if mismatches:
        from app.services.proposal_pricing_sync_repair import (
            run_pricing_sync_repair_or_handoff,
        )

        step_trace(
            "phase3_5_grounding_mismatches",
            rfp_id=rfp_id,
            mismatch_count=len(mismatches),
            mismatch_sample=[
                (m.note or m.sentence or str(m))[:120] for m in mismatches[:8]
            ],
        )
        draft, research, budget, _sync_report = await run_pricing_sync_repair_or_handoff(
            rfp_id=rfp_id,
            draft=draft,
            budget=budget,
            research=research,
            initial_mismatches=mismatches,
            rfp_text=rfp_context,
        )
    else:
        budget = budget.model_copy(update={"narrative_mismatches": []})
        if research:
            research = research.model_copy(update={"budget": budget})
    if research:
        await asave_research_cache(research)
    await _assert_proposal_not_reset(rfp_id)
    await asave_proposal_draft(draft)

    logger.info(
        "Phase 3.5 budget complete for %s: tier=%s, %d line items, revenue=%s",
        rfp_id,
        budget.pricing_tier,
        len(budget.line_items),
        budget.agency_revenue_estimate,
    )
    step_trace(
        "phase3_5_budget_complete",
        rfp_id=rfp_id,
        **summarize_budget(budget),
        manuscript_summary=summarize_sections(draft.sections if draft else []),
        unresolved_slots=len(unresolved_slots or []),
    )
    return draft, research, budget


async def run_phase4_presubmit_review(rfp_id: str) -> tuple[PreSubmitReview, ProposalResearchCache]:
    """Stage 4: audit → optional adversarial repair → pre-submit + ending report."""
    rfp = get_rfp(rfp_id)
    if not rfp:
        raise ProposalError("RFP not found", status_code=404)

    draft = await aget_proposal_draft(rfp_id)
    if not draft or not any(s.content.strip() for s in draft.sections):
        raise ProposalError(
            "No proposal content to review. Generate proposal sections first.",
            status_code=400,
        )

    research = await aget_research_cache(rfp_id)
    from app.core.config import settings as app_settings
    from app.services.proposal_presubmit_review import run_presubmit_review_with_manual_flags

    extra_issues: list = []
    if app_settings.adversarial_repair_loop:
        with pipeline_phase("adversarial-repair", rfp_id=rfp_id):
            draft, research, _audit, repair_report = await run_adversarial_repair_loop(
                rfp=rfp,
                draft=draft,
                research=research,
            )
            await asave_proposal_draft(draft)
            await asave_research_cache(research)
            extra_issues = list(adversarial_repair_blocking_issues(repair_report))
            logger.info(
                "Phase 4 review adversarial repair for %s: resolved=%s stopped=%s rounds=%s",
                rfp_id,
                repair_report.resolved,
                repair_report.stopped_reason,
                repair_report.rounds_run,
            )
            step_trace(
                "adversarial_repair_complete",
                rfp_id=rfp_id,
                resolved=bool(repair_report.resolved),
                stopped_reason=str(repair_report.stopped_reason or ""),
                rounds_run=repair_report.rounds_run,
                escalation_count=len(getattr(repair_report, "escalations", []) or []),
            )
    else:
        step_trace(
            "adversarial_repair_skipped",
            rfp_id=rfp_id,
            reason="adversarial_repair_loop=false",
        )

    # Scan any surviving [VERIFY] tags against the RFP before building the review
    # the user sees: keep a tag only if the RFP explicitly requires that fact,
    # drop it otherwise (never invent). Without this the preview panel showed the
    # raw, un-scrubbed count even when Senior Editor's earlier pass had already
    # run — e.g. tags reintroduced by the adversarial-repair step above.
    from app.services.go_no_go_service import _assess_rfp_content, combine_rfp_text
    from app.services.proposal_budget_content import find_budget_section_index
    from app.services.proposal_verify_optional_scrub import (
        count_verify_tags,
        scrub_draft_optional_verify_tags,
    )

    budget_idx = find_budget_section_index(draft.sections)
    budget_section_id = draft.sections[budget_idx].id if budget_idx is not None else None
    verify_ids = {
        s.id
        for s in draft.sections
        if s.id != budget_section_id and count_verify_tags(s.content or "") > 0
    }
    if verify_ids:
        content_info = _assess_rfp_content(rfp)
        rfp_text = combine_rfp_text(content_info.description or "", content_info.pdf_text or "")
        scrubbed_sections, scrub_logs = await scrub_draft_optional_verify_tags(
            list(draft.sections),
            rfp_text=rfp_text or "",
            section_filter_ids=verify_ids,
        )
        by_old = {s.id: (s.content or "") for s in draft.sections}
        if any(by_old.get(s.id, "") != (s.content or "") for s in scrubbed_sections):
            draft = draft.model_copy(
                update={
                    "sections": scrubbed_sections,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            await asave_proposal_draft(draft)
            if scrub_logs:
                logger.info(
                    "Phase 4 review VERIFY scrub for %s: %s",
                    rfp_id,
                    "; ".join(scrub_logs[:5]),
                )

    # Money intelligence Pass A/B (ledger↔RFP already enforced in 3.5; this triages
    # residual `$` noise and pricing narrative integrity).
    if research and research.budget:
        try:
            from app.services.proposal_money_intelligence import run_money_intelligence

            money_issues = await run_money_intelligence(
                draft=draft, budget=research.budget
            )
            if money_issues:
                extra_issues = list(extra_issues or []) + list(money_issues)
                logger.info(
                    "Phase 4 money intelligence for %s: %d issue(s)",
                    rfp_id,
                    len(money_issues),
                )
        except Exception:
            logger.exception("Phase 4 money intelligence failed for %s", rfp_id)

    review = run_presubmit_review_with_manual_flags(
        rfp=rfp,
        draft=draft,
        research=research,
        extra_issues=extra_issues or None,
        finalized=False,
    )

    from app.services.proposal_ending_report import (
        build_proposal_ending_report,
        ending_report_as_dict,
    )

    now = datetime.now(timezone.utc).isoformat()
    # Attach review before ending report so next-actions / readyToSubmit match.
    research_with_audit = await _attach_phase4_manuscript_audit(
        rfp=rfp,
        draft=draft,
        research=research,
    )
    # Preserve repair report if the audit attach path returned a copy without it.
    if (
        research
        and research.adversarial_repair_report is not None
        and research_with_audit.adversarial_repair_report is None
    ):
        research_with_audit = research_with_audit.model_copy(
            update={"adversarial_repair_report": research.adversarial_repair_report}
        )
    research_for_ending = research_with_audit.model_copy(
        update={
            "presubmit_review": review,
            "updated_at": now,
        }
    )
    ending = build_proposal_ending_report(
        rfp=rfp, draft=draft, research=research_for_ending
    )
    updated_research = research_for_ending.model_copy(
        update={
            "ending_report": ending_report_as_dict(ending),
            "updated_at": now,
        }
    )
    await asave_research_cache(updated_research)

    logger.info(
        "Phase 4 pre-submit review for %s: %d issues, ready=%s, ending_reqs=%d/%d",
        rfp_id,
        len(review.issues),
        review.ready_to_submit,
        ending.requirements_covered,
        ending.requirements_total,
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

    draft = await aget_proposal_draft(rfp_id)
    if not draft or not any(s.content.strip() for s in draft.sections):
        raise ProposalError(
            "No proposal content to fix. Generate proposal sections first.",
            status_code=400,
        )

    research = await aget_research_cache(rfp_id)
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

    await asave_proposal_draft(updated_draft)

    now = datetime.now(timezone.utc).isoformat()
    base_research = updated_research_cache or research
    from app.core.config import settings as app_settings

    if app_settings.adversarial_repair_loop:
        updated_draft, base_research, _audit, repair_report = (
            await run_adversarial_repair_loop(
                rfp=rfp,
                draft=updated_draft,
                research=base_research,
                use_llm_audit=use_llm,
                use_llm_repair=use_llm,
            )
        )
        review = run_presubmit_review_with_manual_flags(
            rfp=rfp,
            draft=updated_draft,
            research=base_research,
            extra_issues=adversarial_repair_blocking_issues(repair_report),
            kb_searched=True,
            finalized=True,
        )
        await asave_proposal_draft(updated_draft)
    updated_research = await _attach_phase4_manuscript_audit(
        rfp=rfp,
        draft=updated_draft,
        research=base_research,
        use_llm=use_llm,
    )
    updated_research = updated_research.model_copy(
        update={"presubmit_review": review, "updated_at": now}
    )
    await asave_research_cache(updated_research)

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

    draft = await aget_proposal_draft(rfp_id)
    if not draft or not any(s.content.strip() for s in draft.sections):
        raise ProposalError(
            "No proposal content to finalize. Generate proposal sections first.",
            status_code=400,
        )

    research = await aget_research_cache(rfp_id)
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
    saved_research = await _attach_phase4_manuscript_audit(
        rfp=rfp,
        draft=updated_draft,
        research=updated_research,
    )
    saved_research = saved_research.model_copy(
        update={"presubmit_review": review, "updated_at": now}
    )
    await asave_research_cache(saved_research)
    await asave_proposal_draft(updated_draft)

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
    """Full pipeline: Sections 1–3 → Phase 2 → Phase 3 draft → Budget → Senior editor."""
    if not llm.is_configured():
        raise ProposalError("LLM not configured.", status_code=503)

    import uuid

    from app.services.llm_call_context import llm_call_context
    from app.services.llm_call_log import (
        format_cost_breakdown_log,
        get_run_cost_breakdown,
    )

    run_id = str(uuid.uuid4())
    logger.info(
        "Full proposal pipeline starting for %s run_id=%s",
        rfp_id,
        run_id,
    )

    from app.core.config import settings as app_settings

    with llm_call_context(rfp_id=rfp_id, run_id=run_id), pipeline_run(
        rfp_id=rfp_id,
        run_id=run_id,
        budget_before_drafting=bool(app_settings.budget_before_drafting),
        adversarial_repair_loop=bool(app_settings.adversarial_repair_loop),
        adversarial_audit_block=bool(
            getattr(app_settings, "adversarial_audit_block", False)
        ),
        persist_phase2_evidence_corpus=bool(
            getattr(app_settings, "persist_phase2_evidence_corpus", False)
        ),
    ):
        _draft, brand_voice, _research = await generate_sections_1_3(rfp_id)
        await run_phase2_retrieval(rfp_id)

        if app_settings.budget_before_drafting:
            logger.info(
                "Full proposal budget-before-drafting enabled for %s",
                rfp_id,
            )
            draft, research, _budget = await run_phase3_5_budget(rfp_id)
            draft, research = await run_phase3_drafting(rfp_id)
            # Re-render money slots after drafting against canonical budget.
            if research and research.budget:
                from app.services.proposal_budget_slots import render_draft_budget_slots

                draft, _unresolved = render_draft_budget_slots(draft, research.budget)
                await asave_proposal_draft(draft)
                step_trace(
                    "money_slots_rerender_after_draft",
                    rfp_id=rfp_id,
                    unresolved=len(_unresolved or []),
                    **summarize_budget(research.budget),
                )
        else:
            draft, research = await run_phase3_drafting(rfp_id)
            draft, research, _budget = await run_phase3_5_budget(rfp_id)

        # Compulsory closing + RFP-demanded forms/attachments (before senior editor).
        rfp = get_rfp(rfp_id)
        if rfp:
            from app.services.proposal_fulfill_rfp_gaps import (
                ensure_closing_sections,
                _merge_closing_into_research_map,
            )
            from app.services.proposal_rfp_submission_requirements import (
                ensure_all_rfp_submission_requirements,
                merge_deliverables_into_research,
            )
            from app.services.rfp_content import combine_rfp_text, load_local_rfp_text

            with pipeline_phase("closing-and-submission", rfp_id=rfp_id):
                _desc, pdf_text, _pdf_exists, _missing, _pages, _img = load_local_rfp_text(
                    rfp, max_chars=250_000
                )
                full_rfp_text = combine_rfp_text(
                    _desc or (rfp.description or ""), pdf_text, max_chars=250_000
                )
                if len(full_rfp_text.strip()) < 200:
                    full_rfp_text = load_rfp_for_proposal(rfp_id)[2]

                with pipeline_step("ensure_closing_sections"):
                    draft, closing_added, close_logs = await ensure_closing_sections(
                        draft=draft,
                        rfp=rfp,
                        rfp_text=full_rfp_text,
                    )
                research = _merge_closing_into_research_map(research, closing_added) or research
                for line in close_logs[:8]:
                    logger.info("Full proposal closing: %s — %s", rfp_id, line)
                step_trace(
                    "closing_sections_done",
                    rfp_id=rfp_id,
                    added=len(closing_added or []),
                    added_titles=[
                        getattr(c, "title", str(c))[:80] for c in (closing_added or [])[:15]
                    ],
                    log_sample=list(close_logs or [])[:8],
                    **summarize_sections(
                        [
                            s
                            for s in draft.sections
                            if any(
                                getattr(c, "section_id", None) == s.id
                                for c in (closing_added or [])
                            )
                        ]
                        if closing_added
                        else []
                    ),
                )

                try:
                    with pipeline_step("ensure_submission_requirements"):
                        draft, deliverables_added, sub_logs, _checklist = (
                            await ensure_all_rfp_submission_requirements(
                                draft=draft,
                                rfp=rfp,
                                rfp_text=full_rfp_text,
                                research=research,
                            )
                        )
                    research = (
                        merge_deliverables_into_research(research, deliverables_added)
                        or research
                    )
                    for line in sub_logs[:8]:
                        logger.info("Full proposal submission: %s — %s", rfp_id, line)
                    step_trace(
                        "submission_requirements_done",
                        rfp_id=rfp_id,
                        added=len(deliverables_added or []),
                        log_sample=list(sub_logs or [])[:8],
                        manuscript_summary=summarize_sections(draft.sections),
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "Full proposal submission attach pass skipped for %s: %s",
                        rfp_id,
                        exc,
                    )
                    step_trace(
                        "submission_requirements_skipped",
                        rfp_id=rfp_id,
                        error_type=exc.__class__.__name__,
                        error_message=str(exc)[:300],
                    )

                await _assert_proposal_not_reset(rfp_id)
                await asave_proposal_draft(draft)
                await asave_research_cache(research)

        draft, research, edit_report = await run_phase3_6_self_edit(rfp_id)

        if brand_voice and not research.brand_voice:
            research = research.model_copy(update={"brand_voice": brand_voice})
            await asave_research_cache(research)

        rfp = get_rfp(rfp_id)
        if rfp:
            extra_issues = self_edit_exhausted_issues(edit_report.section_logs, draft)
            from app.core.config import settings as app_settings

            if app_settings.adversarial_repair_loop:
                with pipeline_phase("adversarial-repair", rfp_id=rfp_id):
                    draft, research, _audit, repair_report = await run_adversarial_repair_loop(
                        rfp=rfp,
                        draft=draft,
                        research=research,
                    )
                    await asave_proposal_draft(draft)
                    await asave_research_cache(research)
                    extra_issues = [
                        *extra_issues,
                        *adversarial_repair_blocking_issues(repair_report),
                    ]
                    logger.info(
                        "Full proposal adversarial repair for %s: resolved=%s stopped=%s rounds=%s",
                        rfp_id,
                        repair_report.resolved,
                        repair_report.stopped_reason,
                        repair_report.rounds_run,
                    )
                    step_trace(
                        "adversarial_repair_complete",
                        rfp_id=rfp_id,
                        resolved=bool(repair_report.resolved),
                        stopped_reason=str(repair_report.stopped_reason or ""),
                        rounds_run=repair_report.rounds_run,
                        escalation_count=len(getattr(repair_report, "escalations", []) or []),
                    )
            else:
                step_trace(
                    "adversarial_repair_skipped",
                    rfp_id=rfp_id,
                    reason="adversarial_repair_loop=false",
                )

            with pipeline_phase("phase-4-presubmit", rfp_id=rfp_id):
                review = run_presubmit_review(
                    rfp=rfp,
                    draft=draft,
                    research=research,
                    extra_issues=extra_issues,
                )
                from app.services.proposal_ending_report import (
                    build_proposal_ending_report,
                    ending_report_as_dict,
                )

                now = datetime.now(timezone.utc).isoformat()
                research_with_audit = await _attach_phase4_manuscript_audit(
                    rfp=rfp,
                    draft=draft,
                    research=research,
                )
                research_for_ending = research_with_audit.model_copy(
                    update={
                        "presubmit_review": review,
                        "updated_at": now,
                    }
                )
                ending = build_proposal_ending_report(
                    rfp=rfp, draft=draft, research=research_for_ending
                )
                research = research_for_ending.model_copy(
                    update={
                        "ending_report": ending_report_as_dict(ending),
                        "updated_at": now,
                    }
                )
                await asave_research_cache(research)
                logger.info(
                    "Phase 4 pre-submit review (auto) for %s: %d issues, ready=%s, ending_reqs=%d/%d",
                    rfp_id,
                    len(review.issues),
                    review.ready_to_submit,
                    ending.requirements_covered,
                    ending.requirements_total,
                )
                step_trace(
                    "phase4_presubmit_complete",
                    rfp_id=rfp_id,
                    issue_count=len(review.issues or []),
                    ready_to_submit=bool(review.ready_to_submit),
                    ending_covered=ending.requirements_covered,
                    ending_total=ending.requirements_total,
                    manuscript_summary=summarize_sections(draft.sections),
                    budget_summary=summarize_budget(
                        research.budget if research else None
                    ),
                )

        try:
            assert_manuscript_ready(
                draft=draft,
                research=research,
                rfp=rfp,
                require_budget=True,
            )
            step_trace(
                "manuscript_ready_assert_ok",
                rfp_id=rfp_id,
                **summarize_sections(draft.sections),
            )
        except Exception as exc:
            step_trace(
                "manuscript_ready_assert_failed",
                rfp_id=rfp_id,
                error_type=exc.__class__.__name__,
                error_message=str(exc)[:300],
                **summarize_sections(draft.sections if draft else []),
            )
            raise

        try:
            breakdown = get_run_cost_breakdown(run_id)
            logger.info("%s", format_cost_breakdown_log(breakdown))
            step_trace(
                "llm_cost_breakdown",
                rfp_id=rfp_id,
                run_id=run_id,
                total_cost_usd=breakdown.get("total_cost_usd"),
                call_count=breakdown.get("call_count"),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "LLM cost breakdown unavailable for run_id=%s: %s",
                run_id,
                str(exc)[:200],
            )

        logger.info(
            "Full proposal complete for %s: %d sections, budget tier=%s, run_id=%s",
            rfp_id,
            len(draft.sections),
            research.budget.pricing_tier if research.budget else "n/a",
            run_id,
        )
        step_trace(
            "full_proposal_summary",
            rfp_id=rfp_id,
            run_id=run_id,
            manuscript_summary=summarize_sections(draft.sections),
            budget_summary=summarize_budget(research.budget if research else None),
        )
        return draft, brand_voice, research
