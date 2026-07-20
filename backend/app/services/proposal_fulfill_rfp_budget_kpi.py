"""Scan RFP fulfill — budget reconcile/sync and KPI verification vs full RFP text."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from app.models.proposal import ProposalDraft, ProposalResearchCache, ProposalSection
from app.models.rfp import RfpRecord
from app.services.proposal_budget_content import (
    find_budget_section_index,
    render_budget_markdown,
    reshape_budget_for_rfp_form,
    rfp_wants_blended_pricing_form,
)
from app.services.proposal_budget_editor import run_budget_editor_pass
from app.services.proposal_fulfill_rfp_accuracy import (
    RfpScoringFacts,
    _EXCEL_ATTACHMENT_RE,
    _INVERSE_COST_SCORING_RE,
    evaluation_and_kpi_excerpt,
    extract_rfp_scoring_facts_llm,
    parse_scoring_facts_from_rfp,
    scan_draft_accuracy_findings,
)
from app.services.proposal_fulfill_rfp_repairs import sections_with_wrong_kpi_framework

logger = logging.getLogger(__name__)

_CONTRACTOR_KPI_RFP_RE = re.compile(
    r"contractor.{0,120}responsible.{0,80}key performance indicator|"
    r"total visitor arrivals|average islands visited per person|"
    r"activity measure|section\s+2\.3",
    re.I | re.S,
)

_BUDGET_ATTACHMENT_NOTE = (
    "\n\n## RFP budget file (required with proposal)\n\n"
    "This solicitation requires a **separate budget attachment** (often Excel / Attachment 01). "
    "The narrative below supports the worksheet — it does **not** replace the official file. "
    "\n\n[MANUAL FILL: attach completed budget worksheet per RFP instructions before export.]\n"
)

_INVERSE_COST_NOTE = (
    "\n\n> **Cost scoring (RFP):** Price is evaluated with **inverse scoring** — "
    "a lower responsive proposed price typically earns more cost/price points. "
    "Do not assume bidding at the ceiling maximizes cost score.\n"
)


def rfp_requires_contractor_kpi_alignment(rfp_text: str) -> bool:
    return bool(_CONTRACTOR_KPI_RFP_RE.search(rfp_text or ""))


def _append_if_missing(content: str, marker: str, block: str) -> str:
    if marker.casefold() in (content or "").casefold():
        return content or ""
    return (content or "").rstrip() + block


def patch_budget_section_for_rfp(
    draft: ProposalDraft,
    *,
    rfp_text: str,
    facts: RfpScoringFacts,
) -> tuple[ProposalDraft, list[str]]:
    """Deterministic budget-section notes — attachment Excel, inverse cost scoring."""
    logs: list[str] = []
    idx = find_budget_section_index(draft.sections)
    if idx is None:
        logs.append("Budget scan: no Budget/Pricing section in manuscript.")
        return draft, logs

    section = draft.sections[idx]
    content = section.content or ""
    updated = content

    if _EXCEL_ATTACHMENT_RE.search(rfp_text or "") or (facts.budget_submission_format or "").strip():
        before = updated
        updated = _append_if_missing(updated, "separate budget attachment", _BUDGET_ATTACHMENT_NOTE)
        if updated != before:
            logs.append("Budget: added RFP separate-attachment (Excel) requirement note.")

    if facts.cost_scoring_inverse or _INVERSE_COST_SCORING_RE.search(rfp_text or ""):
        before = updated
        updated = _append_if_missing(updated, "inverse scoring", _INVERSE_COST_NOTE)
        if updated != before:
            logs.append("Budget: added inverse cost-scoring reminder.")

    if updated == content:
        return draft, logs

    sections = list(draft.sections)
    sections[idx] = section.model_copy(update={"content": updated, "status": "generated"})
    now = datetime.now(timezone.utc).isoformat()
    return draft.model_copy(update={"sections": sections, "updated_at": now}), logs


async def run_fulfill_budget_scan(
    *,
    rfp_id: str,
    rfp: RfpRecord,
    draft: ProposalDraft,
    research: ProposalResearchCache | None,
    rfp_text: str,
    use_llm: bool,
    skip_section_ids: set[str],
) -> tuple[ProposalDraft, ProposalResearchCache | None, list[str]]:
    """Reconcile Stage 3 budget, refresh manuscript budget tab, RFP form + fee alignment."""
    logs: list[str] = []
    if not research or not research.budget:
        logs.append(
            "Budget scan: no pricing model in research — generate Phase 3.5 budget first, then Scan RFP."
        )
        return draft, research, logs

    budget = run_budget_editor_pass(
        research.budget,
        rfp_sections=research.rfp_sections,
        rfp_context=rfp_text[:80_000],
    )
    research = research.model_copy(update={"budget": budget})
    logs.append("Budget: reconciled line items and canonical totals from RFP/pricing model.")

    content = render_budget_markdown(budget, rfp_text=rfp_text)
    sections = list(draft.sections)
    idx = find_budget_section_index(sections)
    if idx is not None:
        sections[idx] = sections[idx].model_copy(
            update={"content": content, "status": "generated"}
        )
    else:
        sections.append(
            ProposalSection(
                id="section-budget-pricing",
                title="Budget & Pricing",
                content=content,
                status="generated",
                source="generated",
                mode="write",
                required=True,
            )
        )
        logs.append("Budget: added Budget & Pricing section to manuscript.")
    now = datetime.now(timezone.utc).isoformat()
    draft = draft.model_copy(update={"sections": sections, "updated_at": now})

    if rfp_wants_blended_pricing_form(rfp_text):
        reshaped = reshape_budget_for_rfp_form(draft, budget, rfp_text=rfp_text)
        if reshaped is not None:
            draft = reshaped
            logs.append("Budget: aligned to RFP Pricing Proposal Form (hourly / monthly / annual).")

    excerpt = evaluation_and_kpi_excerpt(rfp_text)
    facts = await extract_rfp_scoring_facts_llm(excerpt or rfp_text[:60_000])
    draft, patch_logs = patch_budget_section_for_rfp(draft, rfp_text=rfp_text, facts=facts)
    logs.extend(patch_logs)

    if use_llm:
        try:
            from app.services.proposal_budget_sync import align_fee_narrative_with_budget

            synced = await align_fee_narrative_with_budget(
                rfp_id=rfp_id,
                draft=draft,
                budget=budget,
            )
            if skip_section_ids:
                merged = list(synced.sections)
                for i, sec in enumerate(draft.sections):
                    if sec.id in skip_section_ids:
                        merged[i] = sec
                synced = synced.model_copy(update={"sections": merged})
            if synced.model_dump() != draft.model_dump():
                draft = synced
                logs.append(
                    "Budget: synced fee/pricing sentences in narrative sections to canonical budget."
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Fee narrative sync during scan skipped: %s", exc)
            logs.append(f"Budget: fee narrative sync skipped ({exc}).")

    return draft, research, logs


async def run_fulfill_kpi_scan(
    *,
    draft: ProposalDraft,
    rfp: RfpRecord,
    rfp_text: str,
    research: ProposalResearchCache | None,
    skip_section_ids: set[str],
    use_llm: bool,
) -> tuple[ProposalDraft, list[str], list[str]]:
    """Contractor KPI alignment vs RFP Section 2.3 — deterministic spine + accuracy pass."""
    from app.services.proposal_fulfill_rfp_accuracy import run_rfp_accuracy_fulfill_pass
    from app.services.proposal_fulfill_rfp_repairs import run_global_contractor_kpi_fix

    logs: list[str] = []
    human: list[str] = []

    if not rfp_requires_contractor_kpi_alignment(rfp_text):
        logs.append("KPI scan: no contractor KPI obligation detected in RFP text.")
        return draft, logs, human

    logs.append("KPI scan: RFP defines contractor KPIs — checking full manuscript.")

    draft, kpi_logs = run_global_contractor_kpi_fix(draft, skip_section_ids=skip_section_ids)
    logs.extend(kpi_logs)

    from app.services.proposal_fulfill_kpi_detail import run_kpi_detail_thorough_pass

    draft, detail_logs, detail_human = await run_kpi_detail_thorough_pass(
        draft=draft,
        rfp=rfp,
        rfp_text=rfp_text,
        skip_section_ids=skip_section_ids,
        use_llm=use_llm,
    )
    logs.extend(detail_logs)
    human.extend(detail_human)

    if use_llm:
        draft, acc_logs, acc_human = await run_rfp_accuracy_fulfill_pass(
            draft=draft,
            rfp=rfp,
            rfp_text=rfp_text,
            research=research,
            skip_section_ids=skip_section_ids,
        )
        logs.extend(acc_logs)
        human.extend(acc_human)

    from app.services.proposal_fulfill_kpi_detail import run_kpi_detail_deterministic_pass

    draft, detail_logs2 = run_kpi_detail_deterministic_pass(
        draft, skip_section_ids=skip_section_ids
    )
    logs.extend(detail_logs2)

    draft, kpi_logs2 = run_global_contractor_kpi_fix(draft, skip_section_ids=skip_section_ids)
    if kpi_logs2:
        logs.extend(kpi_logs2)

    remaining = [
        sid
        for sid in sections_with_wrong_kpi_framework(draft)
        if sid not in skip_section_ids
    ]
    if remaining:
        titles = [
            next((s.title for s in draft.sections if s.id == sid), sid) for sid in remaining[:8]
        ]
        human.append(
            "Contractor KPI language still wrong in: "
            + ", ".join(titles)
            + " — edit manually or restore and re-scan."
        )
        logs.append(f"KPI scan: {len(remaining)} section(s) still use agency/four-KPI language.")
    else:
        logs.append("KPI scan: manuscript aligned to contractor Section 2.3 KPIs (deterministic check).")

    return draft, logs, human


def summarize_budget_kpi_findings(
    draft: ProposalDraft,
    rfp_text: str,
    research: ProposalResearchCache | None,
) -> list[str]:
    """Short summary for fulfill report."""
    excerpt = evaluation_and_kpi_excerpt(rfp_text)
    facts = parse_scoring_facts_from_rfp(excerpt or rfp_text)
    findings = scan_draft_accuracy_findings(draft, facts, rfp_text)
    lines = [
        f"{f.kind}: {f.message[:120]}…" if len(f.message) > 120 else f"{f.kind}: {f.message}"
        for f in findings
    ]
    if research and research.budget:
        cap = research.budget.rfp_budget_cap
        rev = research.budget.agency_revenue_estimate
        if cap and rev:
            lines.append(f"budget_cap: ${cap:,.0f} vs agency revenue ${float(rev):,.0f}")
    return lines
