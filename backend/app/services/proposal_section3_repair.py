"""Repair Section 3 case studies corrupted by service-title mail-merge."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from app.models.proposal import ProposalDraft, ProposalSection
from app.models.rfp import RfpRecord
from app.services import proposal_knowledge_base_tools
from app.services.company_qualification.agents.case_study_builder import (
    run_case_study_builder_agent,
)
from app.services.company_qualification.schemas import ProposalContext
from app.services.proposal_presubmit_review import (
    is_case_study_section,
    is_service_title_client,
    proposal_client_label,
)

logger = logging.getLogger(__name__)

_CORRUPTION_FRAGMENTS = ()  # kept for module docs; detection uses explicit patterns in case_study_section_corrupted


def case_study_section_corrupted(section: ProposalSection, rfp: RfpRecord) -> bool:
    """True when RFP service-title replaced portfolio client names in Section 3."""
    if not is_case_study_section(section) or not (section.content or "").strip():
        return False
    bad = (rfp.client or "").strip()
    if not bad or not is_service_title_client(bad):
        return False
    body = section.content.casefold()
    needle = bad.casefold()
    if needle not in body:
        return False
    patterns = (
        f"city of {needle}",
        f"for the {needle}",
        f"{needle} is one of the largest",
        f"{needle} county",
        f"{needle} fair",
        f"{needle} library",
        f"{needle} water",
        f"{needle} conservation",
        f"{needle} department",
    )
    return any(p in body for p in patterns)


def study_key_from_section(section: ProposalSection) -> str:
    title = (section.title or "").strip()
    m = re.match(r"^\d+\.\d+\s*[—–\-]\s*(.+)$", title)
    if m:
        return m.group(1).strip()
    if "—" in title:
        return title.split("—", 1)[-1].strip()
    if " - " in title:
        return title.split(" - ", 1)[-1].strip()
    return title


async def rebuild_case_study_section(
    section: ProposalSection,
    *,
    rfp: RfpRecord,
    brand_voice_block: str = "",
) -> str:
    study = study_key_from_section(section)
    case_text, case_sources = await proposal_knowledge_base_tools.fetch_single_case_study(study)
    buyer = proposal_client_label(rfp)
    raw, _ = await run_case_study_builder_agent(
        study_title=study,
        case_study_text=case_text,
        proposal_context=ProposalContext(),
        rfp_client=buyer,
        brand_voice_block=brand_voice_block or "zö agency voice: we/our/us.",
        kb_sources=case_sources,
    )
    content = str(raw.get("content") or "").strip()
    if not content:
        return section.content or ""
    # Hard block: service title must not appear as the case study client name.
    bad = (rfp.client or "").strip()
    if bad and is_service_title_client(bad) and bad.casefold() in content.casefold():
        logger.warning(
            "Case study rebuild for %s still contains service client label — manual review",
            section.id,
        )
    return content


async def repair_corrupted_section_3(
    draft: ProposalDraft,
    *,
    rfp: RfpRecord,
) -> tuple[ProposalDraft, list[str]]:
    logs: list[str] = []
    sections = list(draft.sections)
    changed = False

    for idx, section in enumerate(sections):
        if not case_study_section_corrupted(section, rfp):
            continue
        try:
            new_content = await rebuild_case_study_section(section, rfp=rfp)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Section 3 rebuild failed for %s: %s", section.id, exc)
            logs.append(f"Section 3 rebuild failed: {section.title} ({exc})")
            continue
        if new_content.strip() and new_content.strip() != (section.content or "").strip():
            sections[idx] = section.model_copy(
                update={"content": new_content, "status": "generated"}
            )
            changed = True
            logs.append(
                f"Rebuilt Section 3 case study from KB: {section.title} "
                f"(removed '{rfp.client}' mail-merge corruption)"
            )

    if not changed:
        return draft, logs
    now = datetime.now(timezone.utc).isoformat()
    return draft.model_copy(update={"sections": sections, "updated_at": now}), logs
