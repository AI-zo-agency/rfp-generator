"""LLM submission polish — senior editor surgical fixes with cross-section context."""

from __future__ import annotations

import logging

from app.models.proposal import ProposalDraft, ProposalResearchCache
from app.models.rfp import RfpRecord
from app.services.proposal_common import ProposalError, load_rfp_for_proposal
from app.services.proposal_consistency import patch_improves_section
from app.services.proposal_manuscript_cleanup import (
    SubmissionBlocker,
    build_submission_repair_brief,
    scan_submission_blockers,
    sections_with_submission_blockers,
)
from app.services.proposal_repository import (
    aget_proposal_draft,
    aget_research_cache,
)

logger = logging.getLogger(__name__)


async def run_submission_polish_pass(
    rfp_id: str,
    *,
    rfp: RfpRecord | None = None,
    draft: ProposalDraft | None = None,
    research: ProposalResearchCache | None = None,
) -> tuple[ProposalDraft, list[str]]:
    """
    Target sections with submission blockers; senior editor + section repair with full context.
    Returns updated draft and per-section log lines.
    """
    from app.services.proposal_self_edit_loop import _repair_one_section

    if rfp is None:
        rfp, _, _ = load_rfp_for_proposal(rfp_id)
    draft = draft or await aget_proposal_draft(rfp_id)
    if not draft:
        raise ProposalError("No proposal draft for submission polish.", status_code=400)
    research = research if research is not None else await aget_research_cache(rfp_id)
    budget = research.budget if research else None

    blockers = scan_submission_blockers(draft=draft, research=research)
    if not blockers:
        logger.info("Submission polish for %s: no blockers found", rfp_id)
        return draft, []

    by_section: dict[str, list[SubmissionBlocker]] = {}
    for blocker in blockers:
        by_section.setdefault(blocker.section_id, []).append(blocker)

    logs: list[str] = []
    rfp_client = rfp.client
    rfp_title = rfp.title

    logger.info(
        "Submission polish for %s: %d blocker(s) in %d section(s)",
        rfp_id,
        len(blockers),
        len(by_section),
    )

    for section_id, section_blockers in by_section.items():
        from app.services.proposal_fulfill_guard import section_id_preserved_in_fulfill

        if section_id_preserved_in_fulfill(section_id, draft.sections):
            logs.append(f"{section_id}: skipped — preserved section")
            continue
        brief = build_submission_repair_brief(
            section_blockers,
            draft=draft,
            research=research,
        )
        sid, improved, detail = await _repair_one_section(
            rfp_id,
            section_id,
            use_senior_editor=False,
            rfp=rfp,
            rfp_client=rfp_client,
            rfp_title=rfp_title,
            budget=budget,
            repair_message=brief,
        )
        log_line = f"{sid}: {'fixed' if improved else 'unchanged'} — {detail[:120]}"
        logs.append(log_line)
        logger.info("Submission polish section %s", log_line)
        draft = await aget_proposal_draft(rfp_id) or draft

    remaining = scan_submission_blockers(draft=draft, research=research)
    if remaining:
        logger.warning(
            "Submission polish for %s: %d blocker(s) remain after LLM pass",
            rfp_id,
            len(remaining),
        )
    else:
        logger.info("Submission polish for %s: all blockers cleared", rfp_id)

    return draft, logs


def submission_blocker_section_ids(
    draft: ProposalDraft,
    research: ProposalResearchCache | None,
) -> set[str]:
    return sections_with_submission_blockers(draft, research)
