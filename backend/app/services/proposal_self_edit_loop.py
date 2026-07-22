"""Post–Phase 3 self-edit loop: KB gap-fill + section patches until quality gate passes."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.models.proposal import ProposalDraft, ProposalResearchCache, ProposalSection
from app.models.rfp import RfpRecord
from app.services.proposal_common import ProposalError, aload_rfp_for_proposal
from app.services.proposal_consistency import patch_improves_section, self_edit_exhausted_issues
from app.services.proposal_repository import (
    aget_proposal_draft,
    aget_research_cache,
    asave_proposal_draft,
    asave_research_cache,
)
from app.services.proposal_section_quality import (
    is_weak_section,
    verify_count,
    weakness_score,
    word_count,
)

logger = logging.getLogger(__name__)

MAX_SELF_EDIT_ITERATIONS = 4
SELF_EDIT_TIME_BUDGET_SEC = 480
SELF_EDIT_PARALLEL = 2
MAX_ZERO_IMPROVEMENT_ITERATIONS = 1
MAX_WEAK_SECTIONS_PER_ITERATION = 3
TARGET_FLAG_COUNT = 10

AUTO_REPAIR_MESSAGE = """This section is incomplete or still has [VERIFY] placeholders from the first draft pass.
Run a deep knowledge-base search and write full submission-ready prose for every RFP requirement in this section.
Remove [VERIFY] when evidence supports the answer. Use [E#] citations. Keep zö first-person narrative voice (we/our).
Do not return the same placeholder text.

LEGAL ATTESTATIONS — DO NOT "CLEAN UP" THESE:
- NEVER assert E-Verify enrollment / participation as fact. If the section is an E-Verify Affidavit or mentions penalty of perjury, keep or insert [VERIFY: E-Verify enrollment — unconfirmed in KB — Sonja/Operations must confirm]. Do not convert an open Go/No-Go question into a sworn certification.
- NEVER assert "we have no conflicts of interest" or "no financial relationships that would create conflicts." Keep [VERIFY: conflict-of-interest disclosure — must be confirmed by Sonja/leadership].
- Do not invent annual staffing hours (400/320/280/200/160 etc.) without KB evidence — keep [VERIFY: staffing hours].
- Do not invent a "10-year corporate-creative partnership" — agency founded 2013 (13 years as of 2026).
- For health/coalition/stigma RFPs: include Recovery Network of Oregon (RNO) in references / previous experience / case studies when comparable work is required; if missing, add [FLAG FOR SONJA: Add Recovery Network of Oregon…].

ANTI-DUPLICATION: This section has ONE job. Do NOT re-copy Who We Are, full bios, full case studies, FEIN/certs,
or brand story from other sections. One short cross-reference is OK — then add NEW detail only. Prefer concise prose.

Senior editor priorities (fix if present):
1. Legal attestations first: E-Verify + conflict disclosure must stay VERIFY until a named human confirms
2. Grammar: "We were established …, and is organized" → use "and are organized" or "organized as …"
3. Pronouns: never "of we" or "across we" — use "our firm", "zö agency", or "our studio"
4. Subcontractors: if cost proposal lists translation partners, Company Background must NOT claim "no subcontractors" — zö self-performs marketing/communications; translation partners are scoped separately
5. RFP compliance: reference contact phones and emails, workforce diversity %, budget hours table, PSA acknowledgments — never defer to unnamed attachments or "upon request"
6. Budget: never $0 agency revenue when commission applies — agencyRevenueEstimate must equal commission rate × pass-through or agency_fee line items
7. MWBE and Personnel: use identical workforce % — one HR-verified figure
8. References: full contact block (name, title, phone, email) — not "contact on request"; prefer RNO for health/coalition RFPs
9. Dedup: strip repeated company bio / case study dumps that already live in Sections 1–3
"""


@dataclass
class SelfEditReport:
    iterations_run: int = 0
    sections_targeted: int = 0
    sections_improved: int = 0
    sections_unchanged: int = 0
    stopped_reason: str = ""
    section_logs: list[dict[str, str]] = field(default_factory=list)


async def _senior_editor_instructions(
    *,
    rfp_id: str,
    section: ProposalSection,
    rfp: RfpRecord,
    rfp_client: str,
    rfp_title: str,
    requirements: list[str],
    draft: ProposalDraft | None = None,
    research: ProposalResearchCache | None = None,
    budget: object | None = None,
) -> str:
    from app.services.proposal_langchain_agents import senior_editor_patch_instructions
    from app.services.proposal_manuscript_cleanup import (
        build_submission_repair_brief,
        scan_submission_blockers,
    )
    from app.services.proposal_rfp_compliance import (
        build_rfp_compliance_repair_brief,
        compliance_gaps_for_section,
        scan_rfp_compliance_gaps,
    )

    patch = await senior_editor_patch_instructions(
        rfp_id=rfp_id,
        section_title=section.title,
        section_content=section.content,
        word_target=section.word_target,
        rfp_client=rfp_client,
        rfp_title=rfp_title,
        requirements=requirements,
    )
    section_blockers = []
    compliance_brief = ""
    if draft:
        section_blockers = [
            b
            for b in scan_submission_blockers(draft=draft, research=research)
            if b.section_id == section.id
        ]
        compliance_gaps = compliance_gaps_for_section(
            scan_rfp_compliance_gaps(draft=draft, research=research, rfp=rfp),
            section.id,
        )
        if compliance_gaps:
            compliance_brief = build_rfp_compliance_repair_brief(
                compliance_gaps,
                draft=draft,
                research=research,
                rfp=rfp,
            )
    if section_blockers or compliance_brief:
        brief_parts: list[str] = []
        if compliance_brief:
            brief_parts.append(compliance_brief)
        if section_blockers:
            brief_parts.append(
                build_submission_repair_brief(
                    section_blockers,
                    draft=draft or ProposalDraft(rfpId=rfp_id, sections=[section]),
                    research=research,
                )
            )
        brief = "\n\n".join(brief_parts)
        return f"{brief}\n\nSenior editor agent notes:\n{patch or '(see defects above)'}"
    if patch:
        return f"{AUTO_REPAIR_MESSAGE}\n\nSenior editor patch notes:\n{patch}"
    return AUTO_REPAIR_MESSAGE


def _dedup_brief_for_repair(
    draft: ProposalDraft | None,
    *,
    section_id: str,
) -> str:
    if not draft:
        return ""
    from app.services.proposal_section_dedup import (
        format_anti_duplication_rules,
        format_prior_sections_block,
    )

    prior = [s for s in draft.sections if s.id != section_id and (s.content or "").strip()]
    block = format_prior_sections_block(prior, exclude_ids={section_id})
    parts = [format_anti_duplication_rules()]
    if block:
        parts.append(block)
    return "\n\n".join(parts)


def _locks_brief_for_repair(research: ProposalResearchCache | None) -> str:
    from app.services.proposal_manuscript_locks import format_manuscript_locks_block

    if not research or not research.manuscript_locks:
        return ""
    return format_manuscript_locks_block(research.manuscript_locks)


def _manuscript_digest_for_senior_editor(draft: ProposalDraft, *, max_chars: int = 55_000) -> str:
    parts: list[str] = []
    used = 0
    for section in draft.sections:
        body = (section.content or "").strip()
        if not body:
            chunk = f"### {section.id} — {section.title}\n(empty)\n"
        else:
            # Keep head of each section so cover letter + who-we-are stay visible.
            excerpt = body[:2200]
            chunk = f"### {section.id} — {section.title}\n{excerpt}\n"
        if used + len(chunk) > max_chars:
            break
        parts.append(chunk)
        used += len(chunk)
    return "\n".join(parts)


def _requirements_by_section_id(research: ProposalResearchCache | None) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    if not research:
        return out
    for mapped in research.rfp_sections or []:
        out[mapped.id] = list(mapped.requirements or [])
    return out


def _ticket_rewrite_brief(ticket: dict[str, Any]) -> str:
    brief = str(ticket.get("rewriteBrief") or ticket.get("trimGuidance") or "").strip()
    unmet = ticket.get("unmetRequirements") or []
    parts = [brief] if brief else []
    if isinstance(unmet, list) and unmet:
        parts.append("Unmet RFP requirements:\n" + "\n".join(f"- {u}" for u in unmet[:12]))
    return "\n".join(parts).strip()


async def _redraft_section_via_phase3_isolated(
    *,
    rfp_id: str,
    section_id: str,
    rewrite_brief: str,
    rfp: RfpRecord,
    draft: ProposalDraft,
    research: ProposalResearchCache | None,
) -> tuple[ProposalDraft, ProposalResearchCache | None, bool, str]:
    """Phase 3 single-section draft; hard-isolate so siblings (e.g. cover letter) never change."""
    from app.services.proposal_drafting_graph import draft_single_rfp_section_phase3
    from app.services.proposal_kb_fact_checker import run_kb_fact_check_section_ids
    from app.services.proposal_section_isolation import (
        SectionIsolationError,
        replace_section_isolated,
        snapshot_section_contents,
    )

    before_snap = snapshot_section_contents(draft)
    mapped = None
    if research:
        mapped = next((s for s in research.rfp_sections if s.id == section_id), None)
    existing = next((s for s in draft.sections if s.id == section_id), None)
    if not existing:
        return draft, research, False, "missing section"

    if mapped is None:
        # Template/static sections: use Section Repair with the ticket brief (still isolated).
        sid, improved, detail = await _repair_one_section(
            rfp_id,
            section_id,
            use_senior_editor=False,
            rfp=rfp,
            rfp_client=rfp.client,
            rfp_title=rfp.title,
            budget=research.budget if research else None,
            repair_message=(
                f"{rewrite_brief}\n\n"
                "Rewrite ONLY this section. Do not change any other section."
            ),
        )
        draft2 = await aget_proposal_draft(rfp_id) or draft
        try:
            from app.services.proposal_section_isolation import assert_only_section_changed

            assert_only_section_changed(before_snap, draft2, allowed_section_id=section_id)
        except SectionIsolationError as exc:
            logger.error("Isolation violation after repair %s: %s — discarding", section_id, exc)
            return draft, research, False, f"isolation_violation: {exc}"
        return draft2, research, improved, detail

    from app.services.proposal_common import load_rfp_for_proposal

    _, _, rfp_context = load_rfp_for_proposal(rfp_id)
    static = [s for s in draft.sections if s.source == "template"][:6]
    try:
        drafted, _provider, jit = await draft_single_rfp_section_phase3(
            rfp_id=rfp_id,
            rfp_title=rfp.title,
            rfp_client=rfp.client,
            rfp_sector=rfp.sector,
            rfp_location=rfp.location or None,
            rfp_context=rfp_context,
            section=mapped,
            evidence_corpus=(research.evidence_corpus if research else []) or [],
            brand_voice=research.brand_voice if research else None,
            zo_template_sections=static,
            writing_avoidances=list(research.writing_avoidances or []) if research else None,
            loss_lessons=list(research.loss_lessons or []) if research else None,
            proof_points=list(research.proof_points or []) if research else None,
            manuscript_locks=(
                research.manuscript_locks.model_dump(by_alias=True)
                if research and research.manuscript_locks
                else None
            ),
            execution_plan=(
                research.proposal_execution_plan.model_dump(by_alias=True)
                if research and research.proposal_execution_plan
                else None
            ),
            rewrite_brief=rewrite_brief,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Phase 3 single-section failed for %s: %s", section_id, exc)
        return draft, research, False, f"phase3_failed: {exc}"

    # Preserve title/id from existing; only content/status from Phase 3.
    updated = existing.model_copy(
        update={
            "content": drafted.content or existing.content,
            "status": "generated",
            "kb_refs": drafted.kb_refs or existing.kb_refs,
            "designer_note": drafted.designer_note or existing.designer_note,
        }
    )
    try:
        next_draft = replace_section_isolated(draft, updated)
    except SectionIsolationError as exc:
        logger.error("Isolation reject for %s: %s", section_id, exc)
        return draft, research, False, f"isolation_violation: {exc}"

    if research and jit:
        research = research.model_copy(update={"evidence_corpus": jit})
        await asave_research_cache(research)

    await asave_proposal_draft(next_draft)

    # Fact-check only this section.
    next_draft, _fc = await run_kb_fact_check_section_ids(
        next_draft,
        [section_id],
        rfp=rfp,
        rfp_context=rfp_context,
        research=research,
    )
    # Re-assert isolation after fact-check
    try:
        from app.services.proposal_section_isolation import assert_only_section_changed

        assert_only_section_changed(before_snap, next_draft, allowed_section_id=section_id)
    except SectionIsolationError as exc:
        logger.error("Fact-check isolation violation %s: %s — reverting to pre-fc", section_id, exc)
        # Keep Phase 3 content without fact-check mutation of siblings
        next_draft = replace_section_isolated(draft, updated)
        await asave_proposal_draft(next_draft)
        return next_draft, research, True, "phase3_ok_factcheck_skipped_isolation"

    await asave_proposal_draft(next_draft)
    improved = (updated.content or "") != (existing.content or "")
    return next_draft, research, improved, "phase3_ticket_redraft"


async def _run_senior_editor_ticket_pass(
    *,
    rfp_id: str,
    rfp: RfpRecord,
    draft: ProposalDraft,
    research: ProposalResearchCache | None,
    report: SelfEditReport,
    max_tickets: int = 3,
) -> tuple[ProposalDraft, ProposalResearchCache | None]:
    """Emit Senior Editor tickets and dispatch Phase 3 single-section redrafts."""
    from app.services.proposal_langchain_agents import senior_editor_emit_tickets

    tickets = await senior_editor_emit_tickets(
        rfp_client=rfp.client,
        rfp_title=rfp.title,
        manuscript_digest=_manuscript_digest_for_senior_editor(draft),
        requirements_by_section=_requirements_by_section_id(research),
    )
    coverage = list(tickets.get("coverageTickets") or [])
    dedupe = list(tickets.get("dedupeTickets") or [])
    # Coverage first, then dedupe; unique by sectionId.
    ordered: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in [*coverage, *dedupe]:
        if not isinstance(raw, dict):
            continue
        sid = str(raw.get("sectionId") or "").strip()
        if not sid or sid in seen:
            continue
        if not any(s.id == sid for s in draft.sections):
            continue
        seen.add(sid)
        ordered.append(raw)
        if len(ordered) >= max_tickets:
            break

    if not ordered:
        report.section_logs.append(
            {"section": "senior-editor", "detail": "no tickets emitted"}
        )
        return draft, research

    for ticket in ordered:
        sid = str(ticket.get("sectionId") or "")
        brief = _ticket_rewrite_brief(ticket)
        draft, research, improved, detail = await _redraft_section_via_phase3_isolated(
            rfp_id=rfp_id,
            section_id=sid,
            rewrite_brief=brief or "Address Senior Editor ticket for this section only.",
            rfp=rfp,
            draft=draft,
            research=research,
        )
        report.sections_targeted += 1
        if improved:
            report.sections_improved += 1
        else:
            report.sections_unchanged += 1
        report.section_logs.append(
            {
                "sectionId": sid,
                "detail": detail,
                "ticket": "coverage" if ticket in coverage else "dedupe",
            }
        )
    return draft, research


async def _repair_one_section(
    rfp_id: str,
    section_id: str,
    *,
    use_senior_editor: bool,
    rfp: RfpRecord,
    rfp_client: str,
    rfp_title: str,
    budget: object | None,
    repair_message: str | None = None,
) -> tuple[str, bool, str]:
    """Section Repair LangChain agent (KB tools + patch JSON)."""
    from app.models.proposal import ProposalBudget
    from app.services.proposal_langchain_agents import (
        AgentRole,
        redraft_section_agent,
    )

    draft = await aget_proposal_draft(rfp_id)
    if not draft:
        return section_id, False, "no draft"
    before = next((s for s in draft.sections if s.id == section_id), None)
    if not before:
        return section_id, False, "missing section"

    if not (before.content or "").strip():
        generate_msg = (
            "This section has no draft body yet. Search the knowledge base and write "
            "full submission-ready prose for every RFP requirement. Use [E#] citations. "
            "Do not return placeholders or an empty response."
        )
        return await _fallback_improve_section(
            rfp_id=rfp_id,
            section_id=section_id,
            before=before,
            message=generate_msg,
            rfp=rfp,
            budget=budget,
            reason="Empty section — generate instead of repair",
        )

    research = await aget_research_cache(rfp_id)
    rfp_section = None
    if research:
        for mapped in research.rfp_sections:
            if mapped.id == section_id:
                rfp_section = mapped
                break

    requirements = (rfp_section.requirements if rfp_section else []) or []
    if repair_message:
        message = repair_message
    elif use_senior_editor:
        message = await _senior_editor_instructions(
            rfp_id=rfp_id,
            section=before,
            rfp=rfp,
            rfp_client=rfp_client,
            rfp_title=rfp_title,
            requirements=requirements,
            draft=draft,
            research=research,
            budget=budget,
        )
    else:
        message = AUTO_REPAIR_MESSAGE

    evidence_block = ""
    if research and research.evidence_corpus:
        tagged = [e for e in research.evidence_corpus if section_id in e.section_ids]
        pool = tagged[:12] if tagged else research.evidence_corpus[:8]
        evidence_block = "\n\n".join(
            f"[{e.id}] {e.source}\n{e.excerpt[:1500]}" for e in pool
        )

    user_content = (
        f"Client: {rfp_client}\nRFP: {rfp_title}\n"
        f"Section: {before.title}\nWord target: {before.word_target}\n"
        f"Requirements:\n" + "\n".join(f"- {r}" for r in requirements)
        + f"\n\nRepair task:\n{message}\n\n"
        f"{_locks_brief_for_repair(research)}\n\n"
        f"{_dedup_brief_for_repair(draft, section_id=section_id)}\n\n"
        f"Previous draft:\n{before.content[:5000]}\n\n"
        f"Evidence corpus (cite as [E#]):\n{evidence_block or '(search tools for more)'}"
    )

    typed_budget = budget if isinstance(budget, ProposalBudget) else None

    try:
        raw, provider, tool_log = await redraft_section_agent(
            role=AgentRole.SECTION_REPAIR,
            rfp_id=rfp_id,
            rfp_title=rfp_title,
            rfp_client=rfp_client,
            user_content=user_content,
        )
    except Exception as exc:
        logger.warning(
            "Section Repair agent failed for %s (%s) — falling back to chat_json improve",
            section_id,
            exc,
        )
        return await _fallback_improve_section(
            rfp_id=rfp_id,
            section_id=section_id,
            before=before,
            message=message,
            rfp=rfp,
            budget=budget,
            reason=f"Agent failed ({type(exc).__name__}): {exc}",
        )

    content = str(raw.get("content") or "").strip()
    if not content:
        logger.warning(
            "Section Repair agent empty content for %s — falling back to chat_json improve",
            section_id,
        )
        return await _fallback_improve_section(
            rfp_id=rfp_id,
            section_id=section_id,
            before=before,
            message=message,
            rfp=rfp,
            budget=budget,
            reason="Empty tool-agent response",
        )

    from app.services.proposal_manuscript_cleanup import scan_submission_blockers
    from app.services.proposal_rfp_compliance import scan_rfp_compliance_gaps
    from app.services.proposal_voice_enforcement import enforce_narrative_voice

    before_blockers = len(
        [
            b
            for b in scan_submission_blockers(draft=draft, research=research)
            if b.section_id == section_id
        ]
    )
    before_compliance = len(
        [g for g in scan_rfp_compliance_gaps(draft=draft, research=research, rfp=rfp) if g.section_id == section_id]
    )

    content = enforce_narrative_voice(
        content,
        section_id=before.id,
        title=before.title,
        zo_mode=before.mode,
    )
    after = before.model_copy(
        update={"content": content, "status": "generated"}
    )
    updated_draft = draft.model_copy(
        update={
            "sections": [after if s.id == section_id else s for s in draft.sections],
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "provider": provider,
        }
    )

    if patch_improves_section(before, after, rfp=rfp, budget=typed_budget):
        await asave_proposal_draft(updated_draft)
        tools_note = f" tools={','.join(tool_log[:4])}" if tool_log else ""
        return (
            section_id,
            True,
            f"verify {verify_count(before.content)}→{verify_count(after.content)} "
            f"words {word_count(before.content)}→{word_count(after.content)}{tools_note}",
        )

    after_draft = updated_draft
    after_blockers = len(
        [
            b
            for b in scan_submission_blockers(draft=after_draft, research=research)
            if b.section_id == section_id
        ]
    )
    after_compliance = len(
        [
            g
            for g in scan_rfp_compliance_gaps(draft=after_draft, research=research, rfp=rfp)
            if g.section_id == section_id
        ]
    )
    if after_blockers < before_blockers:
        await asave_proposal_draft(updated_draft)
        return (
            section_id,
            True,
            f"submission blockers {before_blockers}→{after_blockers}",
        )
    if after_compliance < before_compliance:
        await asave_proposal_draft(updated_draft)
        return (
            section_id,
            True,
            f"compliance gaps {before_compliance}→{after_compliance}",
        )
    before_weakness = weakness_score(before)
    after_weakness = weakness_score(after)
    if after_weakness < before_weakness:
        await asave_proposal_draft(updated_draft)
        return (
            section_id,
            True,
            f"weakness score {before_weakness}→{after_weakness}",
        )

    return section_id, False, "reverted (no improvement)"


async def _fallback_improve_section(
    *,
    rfp_id: str,
    section_id: str,
    before: ProposalSection,
    message: str,
    rfp: RfpRecord,
    budget: object | None,
    reason: str,
) -> tuple[str, bool, str]:
    """chat_json improve when tool agent returns empty or errors."""
    from app.models.proposal import ProposalBudget
    from app.services.proposal_section_editor import improve_proposal_section

    typed_budget = budget if isinstance(budget, ProposalBudget) else None
    failure_prefix = (
        f"{reason}. Preserve last good draft facts; fix only the listed gaps.\n\n"
    )
    try:
        _section, updated_draft, updated_research, provider, detail, _ = await improve_proposal_section(
            rfp_id,
            section_id,
            failure_prefix + message,
            persist=False,
        )
        after = next(
            (s for s in updated_draft.sections if s.id == section_id),
            before,
        )
        if patch_improves_section(before, after, rfp=rfp, budget=typed_budget):
            await asave_proposal_draft(updated_draft)
            if updated_research:
                await asave_research_cache(updated_research)
            return (
                section_id,
                True,
                f"fallback improve verify {verify_count(before.content)}→{verify_count(after.content)}",
            )
        return section_id, False, f"fallback no improvement: {detail[:80]}"
    except Exception as fallback_exc:
        return section_id, False, f"fallback failed: {fallback_exc}"


def _total_manuscript_flags(
    draft: ProposalDraft,
    research: ProposalResearchCache | None,
    rfp: RfpRecord,
) -> int:
    from app.services.proposal_manuscript_cleanup import scan_submission_blockers
    from app.services.proposal_manuscript_locks import scan_manuscript_lock_issues
    from app.services.proposal_rfp_compliance import scan_rfp_compliance_gaps

    return (
        len(scan_submission_blockers(draft=draft, research=research))
        + len(scan_rfp_compliance_gaps(draft=draft, research=research, rfp=rfp))
        + len(scan_manuscript_lock_issues(draft=draft, research=research))
    )


async def run_self_edit_loop(
    rfp_id: str,
    *,
    max_iterations: int = MAX_SELF_EDIT_ITERATIONS,
    time_budget_sec: int = SELF_EDIT_TIME_BUDGET_SEC,
    parallel: int = SELF_EDIT_PARALLEL,
) -> tuple[ProposalDraft, ProposalResearchCache | None, SelfEditReport]:
    """KB gap-fill + section-wise patches with strict improvement gate."""
    draft = await aget_proposal_draft(rfp_id)
    if not draft:
        raise ProposalError("No proposal draft for self-edit.", status_code=400)

    from app.services.proposal_generator import (
        generate_sections_1_3,
        static_sections_1_3_have_content,
    )

    if not static_sections_1_3_have_content(draft):
        logger.warning(
            "Self-edit preflight: sections 1–3 incomplete for %s — generating before polish",
            rfp_id,
        )
        await generate_sections_1_3(rfp_id)
        draft = await aget_proposal_draft(rfp_id)
        if not draft or not static_sections_1_3_have_content(draft):
            raise ProposalError(
                "Sections 1–3 must be generated before senior editor polish. "
                "Section 3 (Case Studies) is still empty — check KB and retry.",
                status_code=400,
            )

    research = await aget_research_cache(rfp_id)
    rfp: RfpRecord | None = None
    rfp_client = ""
    rfp_title = ""
    try:
        rfp, _, _ = await aload_rfp_for_proposal(rfp_id)
        rfp_client = rfp.client
        rfp_title = rfp.title
    except ProposalError:
        pass

    if not rfp:
        raise ProposalError("RFP not found for self-edit.", status_code=404)

    from app.services.proposal_common import load_rfp_for_proposal
    from app.services.proposal_kb_fact_checker import run_kb_fact_check_pass
    from app.services.proposal_pipeline_checkpoint import record_pipeline_activity

    await record_pipeline_activity(
        rfp_id,
        label="Senior editor: Checking facts",
        detail="Scanning for invented claims, weak citations, and knowledge-base mismatches",
        step_index=1,
        step_total=5,
        in_progress_phase="phase-3-6-self-edit",
    )

    _, _, rfp_context = load_rfp_for_proposal(rfp_id)
    draft, fc_report = await run_kb_fact_check_pass(
        draft,
        rfp=rfp,
        rfp_context=rfp_context,
        research=research,
    )
    if fc_report.logs:
        await asave_proposal_draft(draft)
        report_preface = SelfEditReport(
            stopped_reason="",
            section_logs=[{"section": "fact-check", "detail": log} for log in fc_report.logs[:20]],
        )
        logger.info(
            "KB fact-check before self-edit for %s: %d log lines",
            rfp_id,
            len(fc_report.logs),
        )
        await record_pipeline_activity(
            rfp_id,
            label="Senior editor: Checking facts",
            detail=f"{len(fc_report.logs)} issue(s) found — next: find gaps and fix sections",
            step_index=1,
            step_total=5,
        )
    else:
        report_preface = None
        await record_pipeline_activity(
            rfp_id,
            label="Senior editor: Checking facts",
            detail="No fact issues found — continuing to find gaps and fix sections",
            step_index=1,
            step_total=5,
        )

    budget = research.budget if research else None
    report = SelfEditReport()
    if report_preface and report_preface.section_logs:
        report.section_logs.extend(report_preface.section_logs)
    deadline = time.monotonic() + time_budget_sec
    sem = asyncio.Semaphore(parallel)
    zero_improve_streak = 0
    zero_flag_streak = 0
    flags_at_start = _total_manuscript_flags(draft, research, rfp)
    from app.services.proposal_manuscript_locks import scan_manuscript_lock_issues

    lock_issues_at_start = scan_manuscript_lock_issues(draft=draft, research=research)
    if flags_at_start <= TARGET_FLAG_COUNT and not lock_issues_at_start:
        report.stopped_reason = "flag_target_met"
        logger.info(
            "Self-edit skipped for %s: %d flags already at target (≤%d)",
            rfp_id,
            flags_at_start,
            TARGET_FLAG_COUNT,
        )
        return draft, research, report

    def _time_left() -> bool:
        return time.monotonic() < deadline

    from app.services.proposal_manuscript_cleanup import sections_with_submission_blockers
    from app.services.proposal_manuscript_locks import scan_manuscript_lock_issues
    from app.services.proposal_rfp_compliance import sections_with_compliance_gaps

    async def _run_one(sid: str, use_senior: bool) -> tuple[str, bool, str]:
        async with sem:
            return await _repair_one_section(
                rfp_id,
                sid,
                use_senior_editor=use_senior,
                rfp=rfp,
                rfp_client=rfp_client,
                rfp_title=rfp_title,
                budget=budget,
            )

    for iteration in range(1, max_iterations + 1):
        from app.services.proposal_generation_cancel import check_generation_cancelled

        await check_generation_cancelled(rfp_id)
        if not _time_left():
            report.stopped_reason = "time_budget"
            break

        draft = await aget_proposal_draft(rfp_id) or draft
        blocker_ids = sections_with_submission_blockers(draft, research)
        compliance_ids = sections_with_compliance_gaps(draft, research, rfp)
        lock_ids = {
            i.section_id
            for i in scan_manuscript_lock_issues(draft=draft, research=research)
            if i.section_id
        }
        # KPI gaps may attach to first reporting section; also target all reporting tabs
        if any(
            i.category == "manuscript_locks" and "KPI" in (i.message or "")
            for i in scan_manuscript_lock_issues(draft=draft, research=research)
        ):
            for s in draft.sections:
                title_l = s.title.casefold()
                if any(
                    m in title_l
                    for m in (
                        "methodolog",
                        "report",
                        "analytics",
                        "optimiz",
                        "measurement",
                        "kpi",
                        "metric",
                    )
                ):
                    lock_ids.add(s.id)
        weak = [
            s
            for s in draft.sections
            if is_weak_section(s)
            or s.id in blocker_ids
            or s.id in compliance_ids
            or s.id in lock_ids
        ]
        if not weak:
            report.stopped_reason = "all_sections_ok"
            break

        weak.sort(key=weakness_score, reverse=True)
        # Prefer lock-conflict sections first
        weak.sort(key=lambda s: (0 if s.id in lock_ids else 1, -weakness_score(s)))
        if len(weak) > MAX_WEAK_SECTIONS_PER_ITERATION:
            weak = weak[:MAX_WEAK_SECTIONS_PER_ITERATION]
        report.iterations_run = iteration
        report.sections_targeted += len(weak)

        flags_before = _total_manuscript_flags(draft, research, rfp)
        remaining_locks = scan_manuscript_lock_issues(draft=draft, research=research)
        if flags_before <= TARGET_FLAG_COUNT and not remaining_locks:
            report.stopped_reason = "flag_target_met"
            break

        logger.info(
            "Self-edit iteration %d for %s: %d weak sections, %d flags (parallel=%d)",
            iteration,
            rfp_id,
            len(weak),
            flags_before,
            parallel,
        )

        from app.services.proposal_pipeline_checkpoint import record_pipeline_activity

        # Coverage / dedupe pass before weak-section repair.
        if iteration == 1:
            await record_pipeline_activity(
                rfp_id,
                label="Senior editor: Finding gaps",
                detail="Looking for missing RFP answers and repeated content to fix",
                step_index=2,
                step_total=5,
            )
            draft, research = await _run_senior_editor_ticket_pass(
                rfp_id=rfp_id,
                rfp=rfp,
                draft=draft,
                research=research,
                report=report,
            )
            draft = await aget_proposal_draft(rfp_id) or draft
            research = await aget_research_cache(rfp_id) or research
            flags_after_tickets = _total_manuscript_flags(draft, research, rfp)
            if flags_after_tickets <= TARGET_FLAG_COUNT:
                report.stopped_reason = "flag_target_met"
                break
            # Refresh weak set after ticket redrafts
            blocker_ids = sections_with_submission_blockers(draft, research)
            compliance_ids = sections_with_compliance_gaps(draft, research, rfp)
            lock_ids = {
                i.section_id
                for i in scan_manuscript_lock_issues(draft=draft, research=research)
                if i.section_id
            }
            weak = [
                s
                for s in draft.sections
                if is_weak_section(s)
                or s.id in blocker_ids
                or s.id in compliance_ids
                or s.id in lock_ids
            ]
            weak.sort(key=lambda s: (0 if s.id in lock_ids else 1, -weakness_score(s)))
            if len(weak) > MAX_WEAK_SECTIONS_PER_ITERATION:
                weak = weak[:MAX_WEAK_SECTIONS_PER_ITERATION]
            if not weak:
                report.stopped_reason = "all_sections_ok"
                break

        kpi_in_batch = any(s.id in lock_ids for s in weak)
        first_title = weak[0].title if weak else "sections"
        titles = ", ".join(s.title for s in weak[:3])
        if len(weak) > 3:
            titles += f" +{len(weak) - 3} more"
        await record_pipeline_activity(
            rfp_id,
            label=f"Senior editor: Fixing {first_title}",
            detail=(
                f"Pass {iteration}/{max_iterations} · fixing {len(weak)} section(s): {titles}"
                + (" · KPI / lock alignment" if kpi_in_batch else "")
            ),
            step_index=3,
            step_total=5,
        )

        # Remaining weak sections: Section Repair without re-running Senior Editor fact hunt.
        tasks = [_run_one(s.id, False) for s in weak]
        results = await asyncio.gather(*tasks)

        improved_this_round = 0
        for sid, improved, detail in results:
            log_entry: dict[str, str] = {
                "sectionId": sid,
                "iteration": str(iteration),
                "detail": detail,
            }
            if not improved:
                log_entry["status"] = "self_edit_exhausted"
                logger.warning("Self-edit section %s: %s", sid, detail)
            report.section_logs.append(log_entry)
            if improved:
                improved_this_round += 1
                report.sections_improved += 1
            else:
                report.sections_unchanged += 1

        draft = await aget_proposal_draft(rfp_id) or draft
        research = await aget_research_cache(rfp_id) or research
        flags_after = _total_manuscript_flags(draft, research, rfp)

        logger.info(
            "Self-edit iteration %d done: %d improved, %d unchanged, flags %d→%d",
            iteration,
            improved_this_round,
            len(weak) - improved_this_round,
            flags_before,
            flags_after,
        )

        if flags_after <= TARGET_FLAG_COUNT:
            report.stopped_reason = "flag_target_met"
            break

        if flags_after >= flags_before:
            zero_flag_streak += 1
            if zero_flag_streak >= MAX_ZERO_IMPROVEMENT_ITERATIONS:
                report.stopped_reason = "flags_stalled"
                logger.info(
                    "Self-edit stopping for %s: flag count stalled at %d",
                    rfp_id,
                    flags_after,
                )
                break
        else:
            zero_flag_streak = 0

        if improved_this_round == 0:
            zero_improve_streak += 1
            if not any(verify_count(s.content or "") > 0 for s in draft.sections):
                report.stopped_reason = "no_improvement"
                break
            if zero_improve_streak >= MAX_ZERO_IMPROVEMENT_ITERATIONS:
                logger.info(
                    "Self-edit stopping main loop after %d zero-improvement iterations",
                    zero_improve_streak,
                )
                report.stopped_reason = "no_improvement"
                break
        else:
            zero_improve_streak = 0

    # Dedicated VERIFY loop — skip senior editor (faster); stop after one failed round
    verify_round = 0
    while _time_left() and verify_round < 1:
        draft = await aget_proposal_draft(rfp_id) or draft
        verify_sections = [
            s for s in draft.sections if verify_count(s.content or "") > 0
        ]
        if not verify_sections:
            if report.stopped_reason == "no_improvement":
                report.stopped_reason = "all_sections_ok"
            break

        verify_round += 1
        logger.info(
            "Self-edit VERIFY round %d for %s: %d sections with placeholders",
            verify_round,
            rfp_id,
            len(verify_sections),
        )
        from app.services.proposal_pipeline_checkpoint import record_pipeline_activity

        await record_pipeline_activity(
            rfp_id,
            label="Senior editor: Clearing placeholders",
            detail=f"Resolving leftover [VERIFY] tags in {len(verify_sections)} section(s)",
            step_index=4,
            step_total=5,
        )
        verify_sections.sort(key=weakness_score, reverse=True)
        verify_sections = verify_sections[:MAX_WEAK_SECTIONS_PER_ITERATION]
        results = await asyncio.gather(
            *[_run_one(s.id, False) for s in verify_sections]
        )
        improved_verify = 0
        for sid, improved, detail in results:
            report.section_logs.append(
                {
                    "sectionId": sid,
                    "iteration": f"verify-{verify_round}",
                    "detail": detail,
                    "status": "" if improved else "self_edit_exhausted",
                }
            )
            if improved:
                improved_verify += 1
                report.sections_improved += 1
            else:
                report.sections_unchanged += 1

        if improved_verify == 0:
            report.stopped_reason = "verify_exhausted"
            break

    if not report.stopped_reason:
        report.stopped_reason = "max_iterations"

    draft = await aget_proposal_draft(rfp_id) or draft
    research = await aget_research_cache(rfp_id)

    skip_polish = report.stopped_reason in {
        "flag_target_met",
        "flags_stalled",
        "no_improvement",
        "time_budget",
    }

    if not _time_left() or skip_polish:
        if skip_polish:
            logger.info(
                "Self-edit for %s: skipping polish passes (stopped=%s)",
                rfp_id,
                report.stopped_reason,
            )
        else:
            logger.warning(
                "Self-edit for %s: skipping polish passes (time budget exhausted)",
                rfp_id,
            )
    else:
        from app.services.proposal_submission_polish import run_submission_polish_pass
        from app.services.proposal_rfp_compliance import run_rfp_compliance_polish_pass

        try:
            from app.services.proposal_pipeline_checkpoint import record_pipeline_activity

            await record_pipeline_activity(
                rfp_id,
                label="Senior editor: Final polish",
                detail="Fixing blockers, compliance gaps, and cross-section consistency",
                step_index=5,
                step_total=5,
            )
            draft, polish_logs = await run_submission_polish_pass(
                rfp_id,
                rfp=rfp,
                draft=draft,
                research=research,
            )
            for line in polish_logs:
                report.section_logs.append(
                    {"sectionId": "", "iteration": "submission-polish", "detail": line}
                )
        except Exception as exc:
            logger.warning("Submission polish pass failed for %s: %s", rfp_id, exc)

        if _time_left():
            try:
                draft, compliance_logs = await run_rfp_compliance_polish_pass(
                    rfp_id,
                    rfp=rfp,
                    draft=draft,
                    research=research,
                )
                for line in compliance_logs:
                    report.section_logs.append(
                        {"sectionId": "", "iteration": "rfp-compliance-polish", "detail": line}
                    )
            except Exception as exc:
                logger.warning("RFP compliance polish pass failed for %s: %s", rfp_id, exc)

        if _time_left() and research and research.budget:
            try:
                from app.services.proposal_generator import run_phase3_5_budget_reconcile

                draft, research, _ = await run_phase3_5_budget_reconcile(rfp_id)
                budget = research.budget
                logger.info("Post-self-edit budget reconcile complete for %s", rfp_id)
            except ProposalError as exc:
                logger.warning("Post-self-edit budget reconcile skipped for %s: %s", rfp_id, exc)
            except Exception as exc:
                logger.warning(
                    "Post-self-edit budget reconcile skipped for %s: %s",
                    rfp_id,
                    exc,
                )

    if research:
        research = research.model_copy(
            update={"updated_at": datetime.now(timezone.utc).isoformat()}
        )
        await asave_research_cache(research)

    # Final legal attestation gate — VERIFY cleanup / polish must not re-assert
    # E-Verify, conflict disclosures, invented hours, or omit RNO on health RFPs.
    from app.services.evidence_trust.legal_attestation_gate import (
        apply_legal_attestation_gates,
    )

    draft, legal_report = apply_legal_attestation_gates(
        draft,
        rfp=rfp,
        rfp_context=rfp_context,
    )
    if legal_report.logs:
        await asave_proposal_draft(draft)
        for line in legal_report.logs:
            report.section_logs.append(
                {
                    "sectionId": "",
                    "iteration": "legal-attestation-gate",
                    "detail": line,
                }
            )

    remaining_locks = scan_manuscript_lock_issues(draft=draft, research=research)
    if remaining_locks:
        summary = "; ".join(
            (i.message or "")[:160] for i in remaining_locks[:4]
        )
        report.stopped_reason = "manuscript_locks_failed"
        logger.error(
            "Self-edit for %s FAILED manuscript locks (%d): %s",
            rfp_id,
            len(remaining_locks),
            summary,
        )
        raise ProposalError(
            "Senior editor could not clear manuscript locks (primary contact / RFQ KPIs): "
            + summary,
            status_code=422,
        )

    logger.info(
        "Self-edit for %s: %d iterations, %d improved, stopped=%s",
        rfp_id,
        report.iterations_run,
        report.sections_improved,
        report.stopped_reason,
    )
    return draft, research, report
