"""Post–Phase 3 self-edit loop: KB gap-fill + section patches until quality gate passes."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.models.proposal import ProposalDraft, ProposalResearchCache, ProposalSection
from app.models.rfp import RfpRecord
from app.services.proposal_common import ProposalError, load_rfp_for_proposal
from app.services.proposal_consistency import patch_improves_section, self_edit_exhausted_issues
from app.services.proposal_repository import (
    get_proposal_draft,
    get_research_cache,
    save_proposal_draft,
    save_research_cache,
)
from app.services.proposal_section_quality import (
    is_weak_section,
    verify_count,
    weakness_score,
    word_count,
)

logger = logging.getLogger(__name__)

MAX_SELF_EDIT_ITERATIONS = 2
SELF_EDIT_TIME_BUDGET_SEC = 480
SELF_EDIT_PARALLEL = 4

AUTO_REPAIR_MESSAGE = """This section is incomplete or still has [VERIFY] placeholders from the first draft pass.
Run a deep knowledge-base search and write full submission-ready prose for every RFP requirement in this section.
Remove [VERIFY] when evidence supports the answer. Use [E#] citations. Keep zö first-person narrative voice (we/our).
Do not return the same placeholder text."""


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
    rfp_client: str,
    rfp_title: str,
    requirements: list[str],
) -> str:
    from app.services.proposal_langchain_agents import senior_editor_patch_instructions

    patch = await senior_editor_patch_instructions(
        rfp_id=rfp_id,
        section_title=section.title,
        section_content=section.content,
        word_target=section.word_target,
        rfp_client=rfp_client,
        rfp_title=rfp_title,
        requirements=requirements,
    )
    if patch:
        return f"{AUTO_REPAIR_MESSAGE}\n\nSenior editor patch notes:\n{patch}"
    return AUTO_REPAIR_MESSAGE


async def _repair_one_section(
    rfp_id: str,
    section_id: str,
    *,
    use_senior_editor: bool,
    rfp: RfpRecord,
    rfp_client: str,
    rfp_title: str,
    budget: object | None,
) -> tuple[str, bool, str]:
    """Section Repair LangChain agent (KB tools + patch JSON)."""
    from app.models.proposal import ProposalBudget
    from app.services.proposal_langchain_agents import (
        AgentRole,
        redraft_section_agent,
    )

    draft = get_proposal_draft(rfp_id)
    if not draft:
        return section_id, False, "no draft"
    before = next((s for s in draft.sections if s.id == section_id), None)
    if not before:
        return section_id, False, "missing section"

    research = get_research_cache(rfp_id)
    rfp_section = None
    if research:
        for mapped in research.rfp_sections:
            if mapped.id == section_id:
                rfp_section = mapped
                break

    requirements = (rfp_section.requirements if rfp_section else []) or []
    message = AUTO_REPAIR_MESSAGE
    if use_senior_editor:
        message = await _senior_editor_instructions(
            rfp_id=rfp_id,
            section=before,
            rfp_client=rfp_client,
            rfp_title=rfp_title,
            requirements=requirements,
        )

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
        failure_prefix = (
            f"Agent failed ({type(exc).__name__}): {exc}. "
            "Preserve last good draft facts; fix only the listed gaps.\n\n"
        )
        try:
            from app.services.proposal_section_editor import improve_proposal_section

            _section, updated_draft, _research, provider, _msg = await improve_proposal_section(
                rfp_id,
                section_id,
                failure_prefix + message,
            )
            after = next(
                (s for s in updated_draft.sections if s.id == section_id),
                before,
            )
            if patch_improves_section(before, after, rfp=rfp, budget=typed_budget):
                return (
                    section_id,
                    True,
                    f"fallback improve verify {verify_count(before.content)}→{verify_count(after.content)}",
                )
            return section_id, False, f"fallback no improvement: {_msg[:80]}"
        except Exception as fallback_exc:
            return section_id, False, f"agent error: {exc}; fallback: {fallback_exc}"

    content = str(raw.get("content") or "").strip()
    if not content:
        return section_id, False, "empty agent response"

    from app.services.proposal_voice_enforcement import enforce_narrative_voice

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
        save_proposal_draft(updated_draft)
        tools_note = f" tools={','.join(tool_log[:4])}" if tool_log else ""
        return (
            section_id,
            True,
            f"verify {verify_count(before.content)}→{verify_count(after.content)} "
            f"words {word_count(before.content)}→{word_count(after.content)}{tools_note}",
        )

    return section_id, False, "reverted (no improvement)"


async def run_self_edit_loop(
    rfp_id: str,
    *,
    max_iterations: int = MAX_SELF_EDIT_ITERATIONS,
    time_budget_sec: int = SELF_EDIT_TIME_BUDGET_SEC,
    parallel: int = SELF_EDIT_PARALLEL,
) -> tuple[ProposalDraft, ProposalResearchCache | None, SelfEditReport]:
    """KB gap-fill + section-wise patches with strict improvement gate."""
    draft = get_proposal_draft(rfp_id)
    if not draft:
        raise ProposalError("No proposal draft for self-edit.", status_code=400)

    research = get_research_cache(rfp_id)
    rfp: RfpRecord | None = None
    rfp_client = ""
    rfp_title = ""
    try:
        rfp, _, _ = load_rfp_for_proposal(rfp_id)
        rfp_client = rfp.client
        rfp_title = rfp.title
    except ProposalError:
        pass

    if not rfp:
        raise ProposalError("RFP not found for self-edit.", status_code=404)

    budget = research.budget if research else None
    report = SelfEditReport()
    deadline = time.monotonic() + time_budget_sec
    sem = asyncio.Semaphore(parallel)

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
        if time.monotonic() >= deadline:
            report.stopped_reason = "time_budget"
            break

        draft = get_proposal_draft(rfp_id) or draft
        weak = [s for s in draft.sections if is_weak_section(s)]
        if not weak:
            report.stopped_reason = "all_sections_ok"
            break

        weak.sort(key=weakness_score, reverse=True)
        report.iterations_run = iteration
        report.sections_targeted += len(weak)

        logger.info(
            "Self-edit iteration %d for %s: %d weak sections (parallel=%d)",
            iteration,
            rfp_id,
            len(weak),
            parallel,
        )

        tasks = [_run_one(s.id, True) for s in weak]
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

        logger.info(
            "Self-edit iteration %d done: %d improved, %d unchanged",
            iteration,
            improved_this_round,
            len(weak) - improved_this_round,
        )

        if improved_this_round == 0:
            report.stopped_reason = "no_improvement"
            break

    if not report.stopped_reason:
        report.stopped_reason = "max_iterations"

    draft = get_proposal_draft(rfp_id) or draft
    research = get_research_cache(rfp_id)

    if research and research.budget:
        try:
            from app.services.proposal_generator import run_phase3_5_budget_reconcile

            draft, research, _ = await run_phase3_5_budget_reconcile(rfp_id)
            budget = research.budget
            logger.info("Post-self-edit budget reconcile complete for %s", rfp_id)
        except ProposalError as exc:
            logger.warning("Post-self-edit budget reconcile skipped for %s: %s", rfp_id, exc)

    if research:
        research = research.model_copy(
            update={"updated_at": datetime.now(timezone.utc).isoformat()}
        )
        save_research_cache(research)

    logger.info(
        "Self-edit for %s: %d iterations, %d improved, stopped=%s",
        rfp_id,
        report.iterations_run,
        report.sections_improved,
        report.stopped_reason,
    )
    return draft, research, report
