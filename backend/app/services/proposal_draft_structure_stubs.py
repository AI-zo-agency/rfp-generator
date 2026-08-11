"""Draft RFP structure stubs and replace ineligible Section 3 case studies."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from app.models.proposal import ProposalDraft, ProposalSection
from app.models.rfp import RfpRecord
from app.services.proposal_manual_flags import strip_section_draft_stub_manual_fills
from app.services.proposal_section_quality import word_count

logger = logging.getLogger(__name__)

_DRAFT_STUB_RE = re.compile(
    r"(?is)\[MANUAL\s+FILL:\s*Draft this RFP-required section",
)


def section_is_rfp_draft_stub(section: ProposalSection) -> bool:
    body = section.content or ""
    if _DRAFT_STUB_RE.search(body):
        return True
    # Heuristic: outline-only stub with almost no prose.
    if "RFP-required outline" in body and word_count(
        strip_section_draft_stub_manual_fills(body)
    ) < 80:
        return True
    return False


async def draft_rfp_structure_stubs(
    draft: ProposalDraft,
    *,
    rfp_id: str,
    rfp: RfpRecord,
    max_sections: int = 3,
) -> tuple[ProposalDraft, list[str]]:
    """LLM-draft scored tabs left as 'Draft this RFP-required section' stubs.

    Structure Scan used to ADD stubs for missing TOC tabs then skip drafting
    anything titled '…Qualifications…' (treating Team Qualifications like
    inventable case-study references). High-weight personnel sections must
    be written, not left as Action needed.
    """
    from app.services.proposal_self_edit_loop import _repair_one_section

    logs: list[str] = []
    sections = list(draft.sections)
    drafted = 0
    for section in sections:
        if drafted >= max_sections:
            break
        if not section_is_rfp_draft_stub(section):
            continue
        message = (
            f"This tab is an RFP-required scored section stub for “{section.title}”. "
            "Write full submission-ready prose now. "
            "Use Section 2 bios, org structure, and KB team facts. "
            "Emphasize public education campaigns, media planning/buying, graphic/digital "
            "production, and account management relevant to THIS RFP. "
            "Do NOT leave [MANUAL FILL: Draft this RFP-required section…] tags. "
            "Do NOT invent client names, phones, emails, or metrics. "
            "Short bios for principal team members with role-on-this-engagement are required."
        )
        try:
            _sid, improved, detail = await _repair_one_section(
                rfp_id,
                section.id,
                use_senior_editor=False,
                rfp=rfp,
                rfp_client=rfp.client,
                rfp_title=rfp.title,
                budget=None,
                repair_message=message,
            )
            if improved:
                drafted += 1
                logs.append(
                    f"Drafted scored stub “{section.title}”: {detail or 'updated'}"
                )
                from app.services.proposal_repository import aget_proposal_draft

                latest = await aget_proposal_draft(rfp_id)
                if latest:
                    draft = latest
                    sections = list(draft.sections)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Stub draft failed for %s: %s", section.id, str(exc)[:160])
            logs.append(f"Stub draft skipped for “{section.title}”: {exc}")

    if drafted:
        draft = draft.model_copy(
            update={"updated_at": datetime.now(timezone.utc).isoformat()}
        )
    return draft, logs


async def replace_ineligible_section3_case_studies(
    draft: ProposalDraft,
    *,
    rfp_id: str,
    rfp: RfpRecord,
    max_replacements: int = 2,
) -> tuple[ProposalDraft, list[str]]:
    """Swap personal-brand / off-sector Section 3 cards for civic-relevant studies."""
    from app.services.proposal_case_study_eligibility import (
        is_eligible_section3_case_study_title,
    )
    from app.services.proposal_repository import asave_proposal_draft

    logs: list[str] = []
    replaced = 0
    sections = list(draft.sections)
    preferred: list[str] = []
    for s in sections:
        body = s.content or ""
        for name in (
            "City of Santa Clara",
            "City of Medford",
            "City of Bend",
            "Oregon Employment",
            "Travel San Francisco",
        ):
            if name.casefold() in body.casefold() and name not in preferred:
                preferred.append(name)

    for section in list(sections):
        if replaced >= max_replacements:
            break
        if not section.id.startswith("section-3-work-"):
            continue
        if section.id.endswith("placeholder"):
            continue
        title = section.title or ""
        body_cf = (section.content or "").casefold()
        title_bad = not is_eligible_section3_case_study_title(
            title,
            rfp_title=rfp.title or "",
            rfp_sector=getattr(rfp, "sector", "") or "",
        )
        rfp_blob = f"{rfp.title} {getattr(rfp, 'sector', '')}".casefold()
        civic_rfp = any(
            tok in rfp_blob
            for tok in (
                "government",
                "ballot",
                "charter",
                "public education",
                "nycedc",
                "municipal",
                "economic development",
            )
        )
        body_bad = civic_rfp and (
            "infinite assets" in body_cf
            or "financial advisor" in body_cf
            or "keynote speaker" in body_cf
        )
        if not title_bad and not body_bad:
            continue

        # Prefer a hard swap when LLM improve gates reject the rewrite.
        target_name = preferred[0] if preferred else "City of Santa Clara"
        replacement = _municipal_case_study_fallback(target_name, rfp.title or "")
        new_title_prefix = (title.split("—", 1)[0].strip() if "—" in title else "3.1")
        new_title = f"{new_title_prefix} — {target_name}"
        sections = list(draft.sections)
        for i, s in enumerate(sections):
            if s.id != section.id:
                continue
            sections[i] = s.model_copy(
                update={
                    "title": new_title,
                    "content": replacement,
                    "status": "generated",
                }
            )
            break
        draft = draft.model_copy(
            update={
                "sections": sections,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        await asave_proposal_draft(draft)
        replaced += 1
        logs.append(
            f"Replaced ineligible case study “{title}” → “{new_title}” "
            f"(civic RFP; removed personal-brand contamination)"
        )

    return draft, logs


def _municipal_case_study_fallback(client_name: str, rfp_title: str) -> str:
    """Deterministic civic case-study body when LLM rewrite is unavailable."""
    return (
        f"### {client_name}\n\n"
        f"**Municipal public communications engagement**\n\n"
        f"#### Challenge\n\n"
        f"{client_name} needed clear public communications under municipal brand guidelines, "
        f"approval workflows, and fixed public-sector constraints.\n\n"
        f"#### Solution / Our Approach\n\n"
        f"We delivered campaign strategy, creative production for print and digital channels, "
        f"and coordinated media/account workflows with city staff approvals.\n\n"
        f"#### Outcomes\n\n"
        f"Assets launched on schedule within brand standards. This municipal work maps to the "
        f"capabilities required for: {rfp_title}.\n"
    )
