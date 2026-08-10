"""Scan RFP fulfill — budget reconcile/sync and KPI verification vs full RFP text."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

from app.models.proposal import ProposalDraft, ProposalResearchCache, ProposalSection
from app.models.rfp import RfpRecord
from app.services.proposal_budget_content import (
    find_budget_section_index,
    render_budget_markdown,
    reshape_budget_for_rfp_form,
    rfp_wants_blended_pricing_form,
)
from app.services.proposal_budget_editor import run_budget_editor_pass
from app.services.proposal_budget_sync import collect_prose_arithmetic_violations
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


_TRAVEL_ONLY_TOTAL_RE = re.compile(
    r"(?is)direct\s+travel\s*/\s*reimbursables:\s*\$?\s*([\d,]+(?:\.\d+)?)"
    r".{0,400}?total\s+proposed\s+investment:\s*\$?\s*([\d,]+(?:\.\d+)?)"
)


def _parse_money(raw: str) -> float:
    try:
        return float(raw.replace(",", ""))
    except ValueError:
        return 0.0


def pricing_model_lacks_professional_fees(budget: object | None) -> bool:
    """True when the cached budget is travel/pass-through only — not a real fee proposal."""
    if budget is None:
        return True
    from app.services.proposal_budget_validation import (
        infer_line_item_type,
        split_line_item_totals,
    )

    line_items = list(getattr(budget, "line_items", None) or [])
    lump = float(getattr(budget, "lump_sum_total", None) or 0)
    revenue = float(getattr(budget, "agency_revenue_estimate", None) or 0)
    direct = float(getattr(budget, "direct_expenses_total", None) or 0)

    if not line_items and lump <= 0 and revenue <= 0:
        return True

    _passthrough, agency_fee, _ = split_line_item_totals(line_items)
    professional = [
        item
        for item in line_items
        if infer_line_item_type(item) not in {"direct_expense", "client_passthrough"}
        and float(getattr(item, "extended", None) or 0) > 0
    ]
    if professional and (agency_fee > 0 or revenue > 0):
        return False
    # Travel-only envelope: lump ≈ direct, no agency fee lines.
    if agency_fee <= 0 and revenue <= 0 and direct > 0 and (
        lump <= 0 or abs(lump - direct) < 1.0
    ):
        return True
    if not professional and lump <= direct + 1.0:
        return True
    return False


def manuscript_cost_section_is_hollow(content: str) -> bool:
    """True when Cost Proposal prose is empty, stub, or travel-equals-total only."""
    text = (content or "").strip()
    if not text:
        return True
    upper = text.upper()
    if "[MANUAL FILL" in upper and "$" not in text:
        return True
    match = _TRAVEL_ONLY_TOTAL_RE.search(text)
    if match:
        travel = _parse_money(match.group(1))
        total = _parse_money(match.group(2))
        if travel > 0 and abs(travel - total) < 1.0:
            return True
    # "Total proposed investment: $X ($X in direct travel expenses)" with no labor table.
    if re.search(
        r"(?is)total proposed investment:\s*\$[\d,]+.*\(\$[\d,]+\s+in\s+direct\s+travel",
        text,
    ) and not re.search(r"(?i)\|[^|\n]*(?:labor|strategy|development|design|hours)", text):
        return True
    return False


_NON_BUDGET_HANDOFF_ON_PRICING_RE = re.compile(
    r"(?is)\[MANUAL\s+FILL:[^\]]*(?:"
    r"manuscript_locks|primary\s+contact\s+lock|"
    r"deterministic\.manuscript_locks|"
    r"names?\s+sonja|sonja\s+anderson\s+as\s+primary"
    r")[^\]]*\]"
)


def budget_manuscript_needs_restore(
    content: str,
    budget: object | None,
) -> bool:
    """True when Complete & Clean should re-sync Pricing/Budget from the pricing model.

    Covers the DuPage failure mode: a healthy fee form was overwritten / painted with
    a primary-contact MANUAL FILL so the tab shows "needs input" even though dollars
    still look populated.
    """
    text = (content or "").strip()
    if not text:
        return True
    if _NON_BUDGET_HANDOFF_ON_PRICING_RE.search(text):
        return True
    # Botched cents rewrite remnants: "32 ($150,526.32 in professional fees…"
    if re.search(
        r"(?<![\d.])\d{1,3}\s*\(\$[\d,]+(?:\.\d{2})?\s+in\s+professional\s+fees",
        text,
    ):
        return True
    if budget is None:
        return False
    canon = 0.0
    for attr in ("total_client_invoicing", "lump_sum_total", "agency_revenue_estimate"):
        raw = getattr(budget, attr, None)
        if raw is not None and float(raw) > 0:
            canon = float(raw)
            break
    if canon <= 0:
        return False
    # Canon total missing from a supposedly complete pricing tab → restore.
    money_tokens = re.findall(r"\$\s*([\d,]+(?:\.\d{2})?)", text)
    if not money_tokens:
        return True
    amounts = [_parse_money(tok) for tok in money_tokens]
    if not any(abs(a - canon) <= max(1.0, canon * 0.02) for a in amounts if a > 0):
        return True
    return False


def manuscript_budget_is_missing(
    draft: ProposalDraft,
    research: ProposalResearchCache | None,
) -> bool:
    """True when Scan RFP should regenerate Phase 3.5 budget (not just reconcile)."""
    budget = research.budget if research else None
    if pricing_model_lacks_professional_fees(budget):
        return True

    idx = find_budget_section_index(draft.sections)
    if idx is None:
        # Pricing model has fees — reconcile path appends Budget & Pricing.
        return False
    content = draft.sections[idx].content or ""
    # Empty "Proposed Compensation…" / Cost tabs must not stay undrafted.
    if manuscript_cost_section_is_hollow(content):
        return True
    return False


async def _regen_budget_via_phase_3_5(rfp_id: str):
    """Indirection so tests can mock Phase 3.5 without importing proposal_generator."""
    from app.services.proposal_generator import run_phase3_5_budget

    return await run_phase3_5_budget(rfp_id)


async def run_fulfill_budget_scan(
    *,
    rfp_id: str,
    rfp: RfpRecord,
    draft: ProposalDraft,
    research: ProposalResearchCache | None,
    rfp_text: str,
    use_llm: bool,
    skip_section_ids: set[str],
) -> tuple[ProposalDraft, ProposalResearchCache | None, list[str], dict[str, Any]]:
    """Thorough budget pass: regenerate if missing, else reconcile + grounding."""
    logs: list[str] = []
    meta: dict[str, Any] = {
        "budgetStatus": "none",
        "budgetChanged": False,
        "budgetRegenerated": False,
        "budgetRepairedNotes": [],
        "budgetEscalationNotes": [],
    }

    if manuscript_budget_is_missing(draft, research):
        logs.append(
            "Budget: pricing model / Cost Proposal missing or travel-only — "
            "regenerating full Phase 3.5 fee proposal."
        )
        try:
            draft, research, _budget = await _regen_budget_via_phase_3_5(rfp_id)
            meta["budgetRegenerated"] = True
            meta["budgetChanged"] = True
            meta["budgetStatus"] = "repaired"
            meta["budgetRepairedNotes"] = ["regenerated Phase 3.5 budget into manuscript"]
            logs.append(
                "Budget: regenerated via Phase 3.5 and written into the proposal."
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Phase 3.5 budget regen during Scan RFP failed: %s", exc)
            logs.append(f"Budget: regeneration failed ({exc}).")
            meta["budgetStatus"] = "needs_human"
            meta["budgetEscalationNotes"] = [f"regeneration failed: {exc}"]
            return draft, research, logs, meta

    if not research or not research.budget:
        logs.append(
            "Budget scan: still no pricing model after regen attempt — human must run Budget."
        )
        meta["budgetStatus"] = "needs_human"
        meta["budgetEscalationNotes"] = ["no pricing model available"]
        return draft, research, logs, meta

    # Reuse the rate card already persisted on the research cache so the Scan RFP
    # path gets the same 00_Guide_Pricing underbid floor check as Phase 3.5.
    rate_card = None
    if research.pricing_rate_card:
        try:
            from app.models.pricing_rate_card import PricingRateCard

            rate_card = PricingRateCard.model_validate(research.pricing_rate_card)
        except Exception:  # noqa: BLE001
            logger.warning(
                "pricing_rate_card invalid on cache for %s — skipping underbid floor check",
                rfp_id,
            )
            rate_card = None

    prior_budget = research.budget
    budget = run_budget_editor_pass(
        prior_budget,
        rfp_sections=research.rfp_sections,
        rfp_context=rfp_text[:80_000],
        rate_card=rate_card,
    )
    # Ignore updatedAt churn — only real money/line changes force a rewrite.
    object_changed = prior_budget.model_dump(
        exclude={"updated_at", "updatedAt"}, by_alias=True
    ) != budget.model_dump(exclude={"updated_at", "updatedAt"}, by_alias=True)
    research = research.model_copy(update={"budget": budget})
    logs.append("Budget: reconciled line items and canonical totals from RFP/pricing model.")
    if not meta["budgetRegenerated"]:
        meta["budgetStatus"] = "ok"

    sections = list(draft.sections)
    idx = find_budget_section_index(sections)
    before = sections[idx].content if idx is not None else ""
    prose_broken = bool(
        idx is not None and collect_prose_arithmetic_violations(before or "")
    )
    hollow = idx is None or manuscript_cost_section_is_hollow(before or "")
    polluted = idx is not None and budget_manuscript_needs_restore(before or "", budget)
    # Complete & Clean must NOT wipe a healthy Pricing / Budget tab just to
    # re-render from canon. Refresh when object changed, prose math is broken,
    # section is missing/hollow, or the tab was polluted (e.g. contact-lock
    # MANUAL FILL dumped onto the fee form — second scan restores from model).
    needs_manuscript_refresh = (
        object_changed
        or prose_broken
        or hollow
        or polluted
        or meta["budgetRegenerated"]
    )

    if needs_manuscript_refresh:
        content = render_budget_markdown(budget, rfp_text=rfp_text)
        if idx is not None:
            sections[idx] = sections[idx].model_copy(
                update={"content": content, "status": "generated"}
            )
            if before != content:
                meta["budgetChanged"] = True
                if meta["budgetStatus"] == "ok":
                    meta["budgetStatus"] = "repaired"
                    meta["budgetRepairedNotes"].append(
                        "refreshed Budget & Pricing manuscript table"
                        + (" (restored polluted pricing tab)" if polluted else "")
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
            meta["budgetChanged"] = True
            meta["budgetStatus"] = "repaired"
            meta["budgetRepairedNotes"].append("added missing Budget & Pricing section")
        now = datetime.now(timezone.utc).isoformat()
        draft = draft.model_copy(update={"sections": sections, "updated_at": now})
    else:
        logs.append(
            "Budget: manuscript already matches reconciled totals — left Pricing/Budget tab unchanged."
        )

    if needs_manuscript_refresh and rfp_wants_blended_pricing_form(rfp_text):
        reshaped = reshape_budget_for_rfp_form(draft, budget, rfp_text=rfp_text)
        if reshaped is not None:
            draft = reshaped
            logs.append("Budget: aligned to RFP Pricing Proposal Form (hourly / monthly / annual).")
            meta["budgetChanged"] = True

    excerpt = evaluation_and_kpi_excerpt(rfp_text)
    facts = await extract_rfp_scoring_facts_llm(excerpt or rfp_text[:60_000])
    draft, patch_logs = patch_budget_section_for_rfp(draft, rfp_text=rfp_text, facts=facts)
    logs.extend(patch_logs)
    if patch_logs:
        meta["budgetChanged"] = True

    if use_llm and needs_manuscript_refresh:
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
                meta["budgetChanged"] = True
        except Exception as exc:  # noqa: BLE001
            logger.warning("Fee narrative sync during scan skipped: %s", exc)
            logs.append(f"Budget: fee narrative sync skipped ({exc}).")

        try:
            from app.services.proposal_budget_sync import run_budget_grounding_check

            mismatches = await run_budget_grounding_check(
                rfp_id=rfp_id,
                draft=draft,
                budget=budget,
            )
            if mismatches:
                from app.services.proposal_pricing_sync_repair import (
                    run_pricing_sync_repair_or_handoff,
                )

                draft, research, budget, sync_report = await run_pricing_sync_repair_or_handoff(
                    rfp_id=rfp_id,
                    draft=draft,
                    budget=budget,
                    research=research,
                    initial_mismatches=mismatches,
                    rfp_text=rfp_text,
                )
                meta["budgetChanged"] = True
                if getattr(sync_report, "handoff", False):
                    meta["budgetStatus"] = "repaired_needs_human"
                    meta["budgetEscalationNotes"].append(
                        f"{len(mismatches)} pricing mismatch(es) need human review"
                    )
                else:
                    meta["budgetStatus"] = "repaired"
                    meta["budgetRepairedNotes"].append(
                        f"grounding repair for {len(mismatches)} pricing mismatch(es)"
                    )
                logs.append(
                    f"Budget: thorough grounding check handled {len(mismatches)} mismatch(es)."
                )
            else:
                logs.append("Budget: thorough grounding check — no pricing mismatches.")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Budget grounding during Scan RFP skipped: %s", exc)
            logs.append(f"Budget: grounding check skipped ({exc}).")

    return draft, research, logs, meta


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
