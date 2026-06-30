"""Post–Phase 3 self-edit loop: KB gap-fill + section patches until quality gate passes."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.models.proposal import ProposalDraft, ProposalResearchCache, ProposalSection
from app.services.proposal_common import ProposalError
from app.services.proposal_repository import (
    get_proposal_draft,
    get_research_cache,
    save_proposal_draft,
    save_research_cache,
)

logger = logging.getLogger(__name__)

MAX_SELF_EDIT_ITERATIONS = 2
SELF_EDIT_TIME_BUDGET_SEC = 480
SELF_EDIT_PARALLEL = 4
MIN_WORDS_RATIO = 0.2

_VERIFY_RE = re.compile(r"\[VERIFY:", re.I)
_STUB_RE = re.compile(
    r"insufficient evidence in corpus|section drafting failed|generation failed|"
    r"error generating|failed to generate|drafting error",
    re.I,
)
_GRAMMAR_GLITCH_RE = re.compile(
    r"\bbeing we\b|\bwe we\b|\bthe the\b|\bcall back\b.*\bwe\b",
    re.I,
)

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


def _word_count(text: str) -> int:
    return len(text.split())


def _verify_count(text: str) -> int:
    return len(_VERIFY_RE.findall(text))


def _is_stub(content: str) -> bool:
    return bool(_STUB_RE.search(content))


def _weakness_score(section: ProposalSection) -> int:
    content = section.content or ""
    if not content.strip():
        return 1000
    score = _verify_count(content) * 25
    if _is_stub(content):
        score += 200
    if _GRAMMAR_GLITCH_RE.search(content):
        score += 40
    target = max(section.word_target or 400, 200)
    words = _word_count(content)
    if words < int(target * MIN_WORDS_RATIO):
        score += 80
    elif words < int(target * 0.45):
        score += 30
    return score


def _is_weak_section(section: ProposalSection) -> bool:
    return _weakness_score(section) >= 30


def _is_strict_improvement(
    before: ProposalSection,
    after: ProposalSection,
) -> bool:
    """Accept patch only if measurable quality improved."""
    if not after.content.strip():
        return False
    if after.content.strip() == before.content.strip():
        return False

    before_score = _weakness_score(before)
    after_score = _weakness_score(after)
    if after_score < before_score:
        return True

    bv, av = _verify_count(before.content), _verify_count(after.content)
    bw, aw = _word_count(before.content), _word_count(after.content)
    target = max(before.word_target or 400, 200)

    if _is_stub(before.content) and not _is_stub(after.content) and aw >= 80:
        return True
    if bv > 0 and av < bv and aw >= bw:
        return True
    if bw < int(target * MIN_WORDS_RATIO) and aw >= int(target * MIN_WORDS_RATIO):
        return True
    if _GRAMMAR_GLITCH_RE.search(before.content) and not _GRAMMAR_GLITCH_RE.search(after.content):
        return True
    return False


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
    rfp_client: str,
    rfp_title: str,
) -> tuple[str, bool, str]:
    """Section Repair LangChain agent (KB tools + patch JSON)."""
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

    message = AUTO_REPAIR_MESSAGE
    if use_senior_editor:
        message = await _senior_editor_instructions(
            rfp_id=rfp_id,
            section=before,
            rfp_client=rfp_client,
            rfp_title=rfp_title,
            requirements=requirements,
        )

    requirements = (rfp_section.requirements if rfp_section else []) or []
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
        try:
            from app.services.proposal_section_editor import improve_proposal_section

            _section, updated_draft, _research, provider, _msg = await improve_proposal_section(
                rfp_id,
                section_id,
                message,
            )
            after = next(
                (s for s in updated_draft.sections if s.id == section_id),
                before,
            )
            if _is_strict_improvement(before, after):
                return (
                    section_id,
                    True,
                    f"fallback improve verify {_verify_count(before.content)}→{_verify_count(after.content)}",
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

    if _is_strict_improvement(before, after):
        save_proposal_draft(updated_draft)
        tools_note = f" tools={','.join(tool_log[:4])}" if tool_log else ""
        return (
            section_id,
            True,
            f"verify {_verify_count(before.content)}→{_verify_count(after.content)} "
            f"words {_word_count(before.content)}→{_word_count(after.content)}{tools_note}",
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
    rfp_client = ""
    rfp_title = ""
    try:
        from app.services.proposal_common import load_rfp_for_proposal

        rfp, _, _ = load_rfp_for_proposal(rfp_id)
        rfp_client = rfp.client
        rfp_title = rfp.title
    except ProposalError:
        pass

    report = SelfEditReport()
    deadline = time.monotonic() + time_budget_sec
    sem = asyncio.Semaphore(parallel)

    async def _run_one(sid: str, use_senior: bool) -> tuple[str, bool, str]:
        async with sem:
            return await _repair_one_section(
                rfp_id,
                sid,
                use_senior_editor=use_senior,
                rfp_client=rfp_client,
                rfp_title=rfp_title,
            )

    for iteration in range(1, max_iterations + 1):
        if time.monotonic() >= deadline:
            report.stopped_reason = "time_budget"
            break

        draft = get_proposal_draft(rfp_id) or draft
        weak = [s for s in draft.sections if _is_weak_section(s)]
        if not weak:
            report.stopped_reason = "all_sections_ok"
            break

        weak.sort(key=_weakness_score, reverse=True)
        report.iterations_run = iteration
        report.sections_targeted += len(weak)

        logger.info(
            "Self-edit iteration %d for %s: %d weak sections (parallel=%d)",
            iteration,
            rfp_id,
            len(weak),
            parallel,
        )

        use_senior = True
        tasks = [_run_one(s.id, use_senior) for s in weak]
        results = await asyncio.gather(*tasks)

        improved_this_round = 0
        for sid, improved, detail in results:
            report.section_logs.append(
                {"sectionId": sid, "iteration": str(iteration), "detail": detail}
            )
            if not improved:
                logger.warning("Self-edit section %s: %s", sid, detail)
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
