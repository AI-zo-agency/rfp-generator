"""Scan RFP fulfill — budget reconcile/sync and KPI verification vs full RFP text."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

from app.models.proposal import ProposalDraft, ProposalResearchCache, ProposalSection
from app.models.rfp import RfpRecord
from app.services.proposal_budget_content import (
    budget_section_score,
    find_budget_section_index,
    official_pricing_form_is_filled,
    render_budget_markdown,
    reshape_budget_for_rfp_form,
    rfp_wants_blended_pricing_form,
    section_looks_like_official_pricing_form,
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
    if professional:
        return False
    # No agency-fee line rows — travel-only, empty, or stale totals with no fee lines.
    if not line_items and lump <= 0 and revenue <= 0:
        return True
    return True


def manuscript_cost_section_is_hollow(content: str) -> bool:
    """True when Cost Proposal prose is empty, stub, or travel-equals-total only."""
    text = (content or "").strip()
    if not text:
        return True
    from app.services.proposal_budget_slots import find_unresolved_budget_slots

    if find_unresolved_budget_slots(text):
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
    r")[^\]]*\]\s*"
)

_CONTACT_NAME_PLACEHOLDER_RE = re.compile(
    r"\[(?:Contact\s+Name|CONTACT\s+PERSON|Insert\s+Contact\s+Name)\]",
    re.I,
)
_CONTACT_EMAIL_PLACEHOLDER_RE = re.compile(
    r"\[(?:Contact\s+Email|CONTACT\s+EMAIL|Insert\s+Contact\s+Email)\]",
    re.I,
)


def strip_non_budget_handoffs_from_pricing(content: str) -> str:
    """Remove contact-lock MANUAL FILL tags that were wrongly stamped on fee forms."""
    text = _NON_BUDGET_HANDOFF_ON_PRICING_RE.sub("", content or "")
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def fill_pricing_form_contact_placeholders(
    content: str,
    *,
    contact_name: str = "",
    contact_email: str = "",
) -> str:
    """Fill buyer-form [Contact Name] / [Contact Email] without rewriting the form."""
    text = content or ""
    name = (contact_name or "").strip()
    email = (contact_email or "").strip()
    if name:
        text = _CONTACT_NAME_PLACEHOLDER_RE.sub(name, text)
    if email:
        text = _CONTACT_EMAIL_PLACEHOLDER_RE.sub(email, text)
    return text


def restore_unresolved_budget_token_tabs(
    draft: ProposalDraft,
    budget: object | None,
    *,
    rfp_text: str = "",
) -> tuple[ProposalDraft, list[str]]:
    """Replace leftover {{budget.*}} Cost/Pricing cells from the canonical fee ledger.

    Writers invent slot names (brand_development, media_placements) that are not
    money-slot keys. The Budget step can still go green on a sibling fee table
    while Cost Proposal stays as raw templates.
    """
    from app.services.proposal_budget_slots import (
        find_unresolved_budget_slots,
        render_budget_slots,
    )

    logs: list[str] = []
    if budget is None:
        return draft, logs
    markdown: str | None = None
    sections = list(draft.sections)
    changed = False
    for idx, section in enumerate(sections):
        body = section.content or ""
        if "{{budget." not in body:
            continue
        filled, _unresolved = render_budget_slots(body, budget)  # type: ignore[arg-type]
        leftover = find_unresolved_budget_slots(filled)
        if leftover and budget_section_score(section.title) > 0:
            if markdown is None:
                markdown = render_budget_markdown(budget, rfp_text=rfp_text)  # type: ignore[arg-type]
            filled = markdown
        if filled != body:
            sections[idx] = section.model_copy(
                update={"content": filled, "status": "generated"}
            )
            changed = True
            logs.append(
                f"Budget: filled unresolved money slots in “{section.title or section.id}”."
            )
    if not changed:
        return draft, logs
    now = datetime.now(timezone.utc).isoformat()
    return draft.model_copy(update={"sections": sections, "updated_at": now}), logs


def _apply_unresolved_budget_slot_restore(
    draft: ProposalDraft,
    research: ProposalResearchCache,
    *,
    rfp_text: str,
    logs: list[str],
    meta: dict[str, Any],
) -> ProposalDraft:
    draft, slot_logs = restore_unresolved_budget_token_tabs(
        draft, research.budget, rfp_text=rfp_text
    )
    logs.extend(slot_logs)
    if slot_logs:
        meta["budgetChanged"] = True
        notes = list(meta.get("budgetRepairedNotes") or [])
        notes.append("filled unresolved {{budget.*}} tokens from the fee ledger")
        meta["budgetRepairedNotes"] = notes
    return draft


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
    """True when Scan must run full Phase 3.5 LLM budget generation.

    Manuscript-only gaps (hollow $500 tab but healthy cached pricing model) do
    NOT belong here — re-rendering from the cached model fixes those without a
    new LLM pass that can overwrite a good fee build with travel-only output.
    """
    budget = research.budget if research else None
    return pricing_model_lacks_professional_fees(budget)


def manuscript_budget_tab_stale(
    draft: ProposalDraft,
    research: ProposalResearchCache | None,
) -> bool:
    """True when Budget & Pricing prose does not match the cached pricing model."""
    budget = research.budget if research else None
    if pricing_model_lacks_professional_fees(budget):
        return True
    idx = find_budget_section_index(draft.sections)
    if idx is None:
        return True
    content = draft.sections[idx].content or ""
    if manuscript_cost_section_is_hollow(content):
        return True
    return budget_manuscript_needs_restore(content, budget)


def _fail_closed_if_budget_still_hollow(
    *,
    draft: ProposalDraft,
    research: ProposalResearchCache | None,
    logs: list[str],
    meta: dict[str, Any],
) -> None:
    """Mark needs_human when travel-only / hollow budget would otherwise pass green."""
    final_budget = research.budget if research else None
    idx_final = find_budget_section_index(draft.sections)
    final_content = (
        draft.sections[idx_final].content if idx_final is not None else ""
    )
    still_hollow = manuscript_cost_section_is_hollow(final_content or "")
    still_travel_only = pricing_model_lacks_professional_fees(final_budget)
    if not (still_hollow or still_travel_only):
        return
    reason = (
        "pricing model is travel/pass-through only (no professional fees)"
        if still_travel_only
        else "Budget & Pricing manuscript is hollow / travel-equals-total"
    )
    meta["budgetStatus"] = "needs_human"
    notes = list(meta.get("budgetEscalationNotes") or [])
    notes.append(reason)
    meta["budgetEscalationNotes"] = notes
    logs.append(
        f"Budget: FAIL CLOSED — {reason}. Step must not pass green; "
        "re-run Budget / Phase 3.5 or fill fees before submit."
    )


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

    from app.services.proposal_budget_content import collapse_duplicate_cost_proposal_tabs

    collapsed, cost_logs = collapse_duplicate_cost_proposal_tabs(list(draft.sections))
    if cost_logs:
        draft = draft.model_copy(
            update={
                "sections": collapsed,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        logs.extend(cost_logs)
        meta["budgetChanged"] = True

    if manuscript_budget_is_missing(draft, research):
        logs.append(
            "Budget: pricing model missing professional fee line items — "
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
    elif manuscript_budget_tab_stale(draft, research):
        logs.append(
            "Budget: manuscript tab stale vs cached pricing model — "
            "re-rendering from canon (no LLM regen)."
        )
        meta["budgetRepairedNotes"].append(
            "re-rendered Budget & Pricing from cached fee model"
        )

    if not research or not research.budget:
        logs.append(
            "Budget scan: still no pricing model after regen attempt — human must run Budget."
        )
        meta["budgetStatus"] = "needs_human"
        meta["budgetEscalationNotes"] = ["no pricing model available"]
        return draft, research, logs, meta

    sections_preview = list(draft.sections)
    idx_preview = find_budget_section_index(sections_preview)
    preview = sections_preview[idx_preview].content if idx_preview is not None else ""
    preview_official = bool(
        idx_preview is not None
        and section_looks_like_official_pricing_form(sections_preview[idx_preview])
        and official_pricing_form_is_filled(preview or "")
    )
    preview_broken = bool(
        idx_preview is not None and collect_prose_arithmetic_violations(preview or "")
    )
    # Generated budgets that already add up must not be re-reconciled / re-rendered.
    # Complete & Clean was rewriting a correct fee table, dropping a phase row,
    # and leaving the old Agency Fee Subtotal in the prose.
    if (
        not meta["budgetRegenerated"]
        and idx_preview is not None
        and not manuscript_cost_section_is_hollow(preview or "")
        and not preview_broken
        and not budget_manuscript_needs_restore(preview or "", research.budget)
    ):
        logs.append(
            "Budget: fee table already adds up — Complete & Clean left Pricing/Budget "
            "unchanged (no editor rewrite, no re-render)."
        )
        meta["budgetStatus"] = "ok"
        if preview_official:
            cleaned = strip_non_budget_handoffs_from_pricing(preview or "")
            locks = research.manuscript_locks if research else None
            contact_name = (locks.primary_contact_name if locks else "") or ""
            cleaned = fill_pricing_form_contact_placeholders(
                cleaned, contact_name=contact_name, contact_email=""
            )
            if cleaned != (preview or "").strip():
                sections_preview[idx_preview] = sections_preview[idx_preview].model_copy(
                    update={"content": cleaned, "status": "generated"}
                )
                draft = draft.model_copy(
                    update={
                        "sections": sections_preview,
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
                meta["budgetChanged"] = True
                logs.append(
                    "Budget: preserved official Pricing Form — cleaned handoff tags / "
                    "filled contact placeholders (no re-render)."
                )
        excerpt = evaluation_and_kpi_excerpt(rfp_text)
        facts = await extract_rfp_scoring_facts_llm(excerpt or rfp_text[:60_000])
        idx2 = find_budget_section_index(draft.sections)
        if idx2 is not None and not section_looks_like_official_pricing_form(
            draft.sections[idx2]
        ):
            draft, patch_logs = patch_budget_section_for_rfp(
                draft, rfp_text=rfp_text, facts=facts
            )
            logs.extend(patch_logs)
            if patch_logs:
                meta["budgetChanged"] = True
        draft = _apply_unresolved_budget_slot_restore(
            draft, research, rfp_text=rfp_text, logs=logs, meta=meta
        )
        _fail_closed_if_budget_still_hollow(
            draft=draft, research=research, logs=logs, meta=meta
        )
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
    target = sections[idx] if idx is not None else None
    is_filled_official_form = bool(
        target is not None
        and section_looks_like_official_pricing_form(target)
        and official_pricing_form_is_filled(before or "")
    )
    prose_broken = bool(
        idx is not None and collect_prose_arithmetic_violations(before or "")
    )
    if prose_broken:
        logs.append(
            "Budget: arithmetic mismatch — rewriting Budget tab from the canonical "
            "fee ledger so the table, subtotal, and total match."
        )

    # Official RFQ pricing forms: never wipe with render_budget_markdown UNLESS
    # the form's own arithmetic is broken — then fall through and rebuild.
    if is_filled_official_form and idx is not None and not prose_broken:
        cleaned = strip_non_budget_handoffs_from_pricing(before or "")
        locks = research.manuscript_locks if research else None
        contact_name = (locks.primary_contact_name if locks else "") or ""
        contact_email = ""
        cleaned = fill_pricing_form_contact_placeholders(
            cleaned, contact_name=contact_name, contact_email=contact_email
        )
        if cleaned != (before or "").strip():
            sections[idx] = sections[idx].model_copy(
                update={"content": cleaned, "status": "generated"}
            )
            draft = draft.model_copy(
                update={
                    "sections": sections,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            meta["budgetChanged"] = True
            logs.append(
                "Budget: preserved official Pricing Form — cleaned handoff tags / "
                "filled contact placeholders (no re-render)."
            )
        else:
            logs.append(
                "Budget: official Pricing Form already filled — left unchanged."
            )
        # Still allow attachment / inverse-cost notes on a separate narrative
        # budget tab if one exists; never patch notes onto the official form body
        # via find_budget_section_index when it still points at the form.
        excerpt = evaluation_and_kpi_excerpt(rfp_text)
        facts = await extract_rfp_scoring_facts_llm(excerpt or rfp_text[:60_000])
        # Only patch when find_budget points at a non-form narrative section.
        idx2 = find_budget_section_index(draft.sections)
        if idx2 is not None and not section_looks_like_official_pricing_form(
            draft.sections[idx2]
        ):
            draft, patch_logs = patch_budget_section_for_rfp(
                draft, rfp_text=rfp_text, facts=facts
            )
            logs.extend(patch_logs)
            if patch_logs:
                meta["budgetChanged"] = True
        draft = _apply_unresolved_budget_slot_restore(
            draft, research, rfp_text=rfp_text, logs=logs, meta=meta
        )
        _fail_closed_if_budget_still_hollow(
            draft=draft, research=research, logs=logs, meta=meta
        )
        return draft, research, logs, meta

    hollow = idx is None or manuscript_cost_section_is_hollow(before or "")
    polluted = idx is not None and budget_manuscript_needs_restore(before or "", budget)
    stale_tab = manuscript_budget_tab_stale(draft, research)
    # Complete & Clean must NOT wipe a healthy Pricing / Budget tab just to
    # re-render from canon. Refresh when object changed, prose math is broken,
    # section is missing/hollow/stale, or Phase 3.5 just regenerated.
    needs_manuscript_refresh = (
        object_changed
        or prose_broken
        or hollow
        or polluted
        or stale_tab
        or meta["budgetRegenerated"]
    )

    if needs_manuscript_refresh:
        content = render_budget_markdown(budget, rfp_text=rfp_text)
        # Signature / cover DESIGNER NOTEs belong on closing tabs — never on fees.
        content = re.sub(
            r"(?is)\[DESIGNER\s+NOTE:[^\]]*\]\s*",
            "",
            content,
        ).strip()
        if idx is not None:
            sections[idx] = sections[idx].model_copy(
                update={"content": content, "status": "generated"}
            )
            if before != content:
                meta["budgetChanged"] = True
                if meta["budgetStatus"] == "ok":
                    meta["budgetStatus"] = "repaired"
                    meta["budgetRepairedNotes"].append(
                        "rewrote Budget tab so fee table, subtotal, and total match"
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

    draft = _apply_unresolved_budget_slot_restore(
        draft, research, rfp_text=rfp_text, logs=logs, meta=meta
    )
    _fail_closed_if_budget_still_hollow(
        draft=draft, research=research, logs=logs, meta=meta
    )
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
