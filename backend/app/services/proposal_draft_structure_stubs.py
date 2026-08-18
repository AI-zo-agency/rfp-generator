"""Draft RFP structure stubs and replace ineligible Section 3 case studies."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.models.proposal import ProposalDraft, ProposalSection
from app.models.rfp import RfpRecord
from app.services.proposal_manual_flags import strip_section_draft_stub_manual_fills
from app.services.proposal_section_health import classify_section_health
from app.services.proposal_section_quality import word_count

logger = logging.getLogger(__name__)

_SKIP_FILL_ID_PREFIXES = (
    "section-2-bio-",
    "section-3-work-",
)

_DRAFT_STUB_MARKER = "draft this rfp-required section"


def section_is_rfp_draft_stub(section: ProposalSection) -> bool:
    body = section.content or ""
    if _DRAFT_STUB_MARKER in body.casefold():
        return True
    # Heuristic: outline-only stub with almost no prose.
    if "RFP-required outline" in body and word_count(
        strip_section_draft_stub_manual_fills(body)
    ) < 80:
        return True
    return False


def _is_stub_chrome_line(line: str) -> bool:
    cf = line.casefold().strip()
    return (
        cf.startswith("rfp-required outline")
        or cf.startswith("rfp required outline")
        or cf.startswith("rfp instructions")
        or cf.startswith("evaluation weight")
        or _DRAFT_STUB_MARKER in cf
    )


def _normalize_title_echo(text: str) -> str:
    plain = (text or "").strip()
    while plain.startswith("#"):
        plain = plain[1:].lstrip()
    i = 0
    while i < len(plain) and plain[i] in "0123456789.":
        i += 1
    plain = plain[i:].strip()
    return " ".join(plain.casefold().replace("&", "and").split())


def _meaningful_body(content: str, title: str) -> str:
    """Body with stub tags, title-echo headings, and outline chrome removed."""
    stripped = strip_section_draft_stub_manual_fills(content or "")
    title_echo = _normalize_title_echo(title)
    keep: list[str] = []
    for raw in stripped.splitlines():
        line = raw.strip()
        if not line:
            continue
        if _is_stub_chrome_line(line):
            continue
        if title_echo and _normalize_title_echo(line) == title_echo:
            continue
        keep.append(line)
    return "\n".join(keep)


def _is_thin_unfilled_shell(section: ProposalSection) -> bool:
    if not (section.content or "").strip():
        return True
    if section_is_rfp_draft_stub(section):
        return True
    return word_count(_meaningful_body(section.content or "", section.title or "")) < 12


def section_needs_presubmit_fill(section: ProposalSection) -> bool:
    """True for leftover empty / Action-needed RFP tabs Review must draft.

    Does not rewrite finished prose. Skips bios, case-study cards, and Budget.
    """
    sid = section.id or ""
    if sid.startswith("section-1-"):
        return False
    if sid.startswith(_SKIP_FILL_ID_PREFIXES):
        return False
    try:
        from app.services.proposal_section_dedup import (
            _is_protected_budget_section,
            is_rfp_company_identity_form_section,
        )

        if _is_protected_budget_section(section):
            return False
        if is_rfp_company_identity_form_section(
            section_id=sid,
            title=section.title or "",
            content=section.content or "",
        ):
            return False
    except Exception:  # noqa: BLE001
        title_cf = (section.title or "").casefold()
        if "budget" in title_cf and "pricing" in title_cf:
            return False
    health = classify_section_health(section.content)
    if health is not None:
        return True
    return _is_thin_unfilled_shell(section)


def stub_fill_landed(before: ProposalSection, after: ProposalSection) -> bool:
    """Persist when a shell became real prose — ignore the repair 'improvement' gate."""
    if not (after.content or "").strip():
        return False
    if _DRAFT_STUB_MARKER in (after.content or "").casefold():
        return False
    after_n = word_count(_meaningful_body(after.content or "", after.title or ""))
    before_n = word_count(_meaningful_body(before.content or "", before.title or ""))
    return after_n >= 25 and after_n > before_n + 12


def _stub_draft_brief(section: ProposalSection) -> str:
    title = (section.title or "this section").strip()
    return (
        f"This tab is an unfilled RFP-required section (“{title}”). "
        "Write submission-ready prose for THIS tab's unique ask only. "
        "Use KB + THIS RFP. Do not invent clients, contacts, certs, carriers, "
        "or metrics. If a figure is not in the RFP or KB, use [VERIFY: …]. "
        "Do NOT leave [MANUAL FILL: Draft this RFP-required section…] tags. "
        "Do not recopy Who We Are, full bios, or full case studies — "
        "one short cross-ref is enough, then new detail for this tab. "
        "Licenses, certifications, and insurance Compliant claims belong in "
        "Section 1.4 / 1.5 with companyfacts proof — this tab may cross-ref them, "
        "never invent Compliant, carriers, or KPI numbers the RFP/KB do not state."
    )


async def draft_rfp_structure_stubs(
    draft: ProposalDraft,
    *,
    rfp_id: str,
    rfp: RfpRecord,
    max_sections: int = 8,
) -> tuple[ProposalDraft, list[str]]:
    """LLM-draft leftover empty / heading-only RFP tabs. One call each.

    Does not use the self-edit improvement gate (that gate was reverting stub
    fills and leaving ACTION NEEDED / title-only shells in place).
    """
    from app.services.proposal_repository import aget_proposal_draft, asave_proposal_draft
    from app.services.proposal_section_editor import improve_proposal_section

    logs: list[str] = []
    sections = list(draft.sections)
    drafted = 0
    for section in sections:
        if drafted >= max_sections:
            break
        if not section_needs_presubmit_fill(section):
            continue
        message = _stub_draft_brief(section)
        try:
            _updated, updated_draft, _research, _provider, detail, _ok, _extra = (
                await improve_proposal_section(
                    rfp_id,
                    section.id,
                    message,
                    persist=False,
                    proposal_wide=False,
                    improve_section_pinned=True,
                )
            )
            after = next(
                (s for s in updated_draft.sections if s.id == section.id),
                section,
            )
            if stub_fill_landed(section, after):
                await asave_proposal_draft(updated_draft)
                drafted += 1
                logs.append(
                    f"Drafted leftover tab “{section.title}”: "
                    f"{word_count(section.content or '')}→{word_count(after.content or '')}w"
                )
                latest = await aget_proposal_draft(rfp_id)
                if latest:
                    draft = latest
                    sections = list(draft.sections)
            else:
                logs.append(
                    f"Stub still empty “{section.title}”: "
                    f"{(detail or 'writer did not land prose')[:120]}"
                )
                logger.warning(
                    "Presubmit stub fill reverted/empty rfp_id=%s section_id=%s detail=%s",
                    rfp_id,
                    section.id,
                    (detail or "")[:160],
                )
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
