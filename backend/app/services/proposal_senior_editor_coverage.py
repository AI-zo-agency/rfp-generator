"""Mechanical senior-editor audit — every RFP tab must exist with real content."""

from __future__ import annotations

import logging
from typing import Any

from app.models.proposal import ProposalDraft, ProposalResearchCache, ProposalSection
from app.services.proposal_fulfill_rfp_structure import (
    ensure_missing_scored_section_stubs,
    extract_rfp_scored_section_specs,
    is_pointer_only_company_delegation,
    repair_pointer_only_rfp_sections,
    specs_from_intelligence_outline,
)
from app.services.proposal_outline_dedup import outline_titles_near_duplicate
from app.services.proposal_section_quality import word_count

logger = logging.getLogger(__name__)

_MIN_SUBSTANTIVE_WORDS = 45


def _find_section_for_title(
    draft: ProposalDraft,
    *,
    section_id: str,
    title: str,
) -> ProposalSection | None:
    for section in draft.sections:
        if section_id and section.id == section_id:
            return section
    for section in draft.sections:
        if outline_titles_near_duplicate(section.title or "", title):
            return section
    return None


def _section_needs_coverage_ticket(section: ProposalSection) -> tuple[bool, str]:
    body = (section.content or "").strip()
    if not body:
        return True, "Section is empty."
    if is_pointer_only_company_delegation(body):
        return True, "Tab is only a cross-reference to Sections 1–3 — evaluators need full content here."
    title_cf = (section.title or "").casefold()
    if "reference" in title_cf:
        from app.services.proposal_closing_hollow_repair import (
            references_section_is_hollow,
        )

        if references_section_is_hollow(body):
            return True, "References tab promises contacts but lists none."
    if "addend" in title_cf and body.count("[MANUAL FILL") >= 3:
        return True, "Addenda table is still a MANUAL FILL stub — needs a clean acknowledgment."
    wc = word_count(body)
    if wc < _MIN_SUBSTANTIVE_WORDS:
        return True, f"Tab is too thin (~{wc} words) for an RFP-required section."
    if body.count("[MANUAL FILL") >= 1 and wc < 90:
        return True, "Tab is still a stub — draft full RFP-specific substance."
    return False, ""


async def apply_senior_editor_section_coverage_audit(
    draft: ProposalDraft,
    *,
    research: ProposalResearchCache | None,
    rfp_text: str,
    rfp_title: str = "",
    use_llm_toc: bool = True,
) -> tuple[ProposalDraft, list[str], list[dict[str, Any]]]:
    """Repair pointer stubs, add missing TOC tabs, emit mechanical coverage tickets."""
    logs: list[str] = []
    coverage_tickets: list[dict[str, Any]] = []

    draft, pointer_logs = repair_pointer_only_rfp_sections(draft)
    logs.extend(pointer_logs)

    from app.services.proposal_budget_content import reformat_budget_terms_in_markdown
    from app.services.proposal_closing_hollow_repair import repair_hollow_closing_sections
    from app.services.proposal_verify_optional_scrub import restore_empty_money_table_cells

    draft, closing_logs = repair_hollow_closing_sections(draft)
    logs.extend(closing_logs)

    rewritten: list[ProposalSection] = []
    changed = False
    for section in draft.sections:
        body = section.content or ""
        updated = body
        title_cf = (section.title or "").casefold()
        is_money_tab = any(
            token in title_cf
            for token in ("budget", "pricing", "fee", "cost proposal", "price")
        )
        if is_money_tab:
            formatted = reformat_budget_terms_in_markdown(updated)
            if formatted != updated:
                logs.append(f"Reformatted Terms in «{section.title}» to tables/bullets.")
                updated = formatted
            filled, n_fill = restore_empty_money_table_cells(updated)
            if n_fill:
                logs.append(
                    f"Restored {n_fill} empty cost cell(s) in «{section.title}» "
                    "with MANUAL FILL so the gap stays visible."
                )
                updated = filled
        if updated != body:
            rewritten.append(section.model_copy(update={"content": updated}))
            changed = True
        else:
            rewritten.append(section)
    if changed:
        draft = draft.model_copy(update={"sections": rewritten})

    specs = []
    if use_llm_toc:
        try:
            specs = await extract_rfp_scored_section_specs(
                rfp_text,
                rfp_title=rfp_title,
                existing_section_titles=[s.title for s in draft.sections if s.title],
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Senior editor TOC extract skipped: %s", exc)
    if not specs and research and research.rfp_sections:
        specs = specs_from_intelligence_outline(
            [
                (m.title or "", m.duplicate_of_static_section or "")
                for m in research.rfp_sections
            ],
        )

    if specs:
        draft, stub_logs = ensure_missing_scored_section_stubs(draft, specs)
        logs.extend(stub_logs)

    mapped = list(research.rfp_sections or []) if research else []
    seen_ticket_ids: set[str] = set()
    for item in mapped:
        title = (item.title or "").strip()
        if not title:
            continue
        sid = str(item.id or "").strip()
        eval_w = getattr(item, "evaluation_weight", None)
        try:
            scored = eval_w is not None and float(eval_w) > 0
        except (TypeError, ValueError):
            scored = False
        required = bool(getattr(item, "required", True))
        if not required and not scored:
            continue

        section = _find_section_for_title(draft, section_id=sid, title=title)
        ticket_id = section.id if section else (sid or title)

        if section is None:
            if ticket_id in seen_ticket_ids:
                continue
            seen_ticket_ids.add(ticket_id)
            coverage_tickets.append(
                {
                    "sectionId": ticket_id,
                    "unmetRequirements": [f"Missing RFP tab in manuscript: {title}"],
                    "rewriteBrief": (
                        f"Draft the full «{title}» section for this RFP. Cover every scored "
                        "and required ask — substantive prose/tables; one brief cross-ref OK, "
                        "never pointer-only."
                    ),
                }
            )
            continue

        needs, reason = _section_needs_coverage_ticket(section)
        if needs and ticket_id not in seen_ticket_ids:
            seen_ticket_ids.add(ticket_id)
            reqs = list(item.requirements or [])[:6]
            unmet = reqs if reqs else [reason]
            coverage_tickets.append(
                {
                    "sectionId": section.id,
                    "unmetRequirements": unmet,
                    "rewriteBrief": (
                        f"Expand «{section.title or title}»: {reason} "
                        "Write complete RFP-specific content — length is fine; do not shorten "
                        "by deleting required asks or replacing with 'see Section 1' pointers."
                    ),
                }
            )

    return draft, logs, coverage_tickets
