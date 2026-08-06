"""Task 17 — run the deterministic budget machinery inside Scan-RFP itself.

The Scan-RFP button (``run_verify_scrub_only_scan``) never touched the
budget: its own module docstring says "Does NOT add closing tabs, structure,
budget, or KPI passes — VERIFY scrub only." ``run_fulfill_budget_scan`` only
runs on ``mode="full"``, which the frontend never sends (it hardcodes
``mode="verify_scrub_only"``). So none of the budget protections this project
already built — cross-field prose arithmetic, the underbid floor against
00_Guide_Pricing, RFP-forbidden travel/reimbursable lines, line-item
classification — ever ran when a user clicked "Scan RFP".

This module reuses that existing machinery (does not reimplement any of it):

- ``proposal_budget_editor.run_budget_editor_pass`` — the same deterministic
  reconcile + invariant/floor/RFP-constraint gate Phase 3.5 and the legacy
  ``mode="full"`` budget scan already use.
- ``proposal_budget_validation.reconcile_proposal_budget`` /
  ``validate_budget_canonical`` — used, non-raising, only to CLASSIFY a
  halt from ``run_budget_editor_pass`` (arithmetic-unrepairable vs a
  pricing-judgement gate) so the report can say which one happened.
- ``proposal_budget_floor.collect_underbid_violations`` /
  ``collect_rfp_constraint_violations`` — same classification use.
- ``proposal_budget_sync.collect_prose_arithmetic_violations`` — checks the
  manuscript's CURRENT rendered Budget & Pricing section text, independent of
  the canonical object (a stale LLM-authored sentence can be wrong even when
  the canonical fields are not).

``run_budget_editor_pass`` raises ``ProposalError(422)`` on invariant/floor/
constraint failure — correct for generation (halt the whole run), wrong for a
scan on an already-existing proposal (the user clicked a button; a bad
budget must surface as a finding, never abort the scan or 500). Every call
into it here is caught.

Never fabricates a dollar amount. When the residual problem is a pricing
JUDGEMENT call (total below the guide floor, RFP-forbidden travel line) or a
genuinely unrepairable arithmetic defect, this reports it for a human
(Sonja) instead of inventing numbers to force a clean pass.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from app.models.proposal import ProposalBudget, ProposalDraft, ProposalResearchCache, ProposalSection
from app.services.proposal_budget_content import find_budget_section_index, render_budget_markdown
from app.services.proposal_budget_editor import run_budget_editor_pass
from app.services.proposal_budget_floor import (
    collect_rfp_constraint_violations,
    collect_underbid_violations,
)
from app.services.proposal_budget_sync import collect_prose_arithmetic_violations
from app.services.proposal_budget_validation import (
    reconcile_proposal_budget,
    validate_budget_canonical,
)
from app.services.proposal_common import ProposalError

logger = logging.getLogger(__name__)

# Canonical numeric fields worth naming explicitly when they change — keeps
# the repair note human-readable instead of a raw model diff.
_DIFF_FIELDS: tuple[tuple[str, str], ...] = (
    ("line_item_sum", "line-item total"),
    ("agency_fee_subtotal", "agency fee subtotal"),
    ("client_media_passthrough", "client media pass-through"),
    ("direct_expenses_total", "direct expenses"),
    ("agency_revenue_estimate", "agency revenue estimate"),
    ("total_client_invoicing", "total client invoicing"),
    ("lump_sum_total", "lump sum total"),
)


@dataclass
class BudgetScanCheckResult:
    draft: ProposalDraft
    research: ProposalResearchCache | None
    # "none" (no budget yet) | "ok" (checked, clean) | "repaired"
    # (deterministic fix applied) | "needs_human" (pricing judgement /
    # unrepairable — nothing changed) | "repaired_needs_human" (deterministic
    # part applied, a judgement issue remains).
    status: str
    changed: bool
    repaired_notes: list[str]
    escalation_notes: list[str]
    logs: list[str]


def _describe_budget_repair(before: ProposalBudget, after: ProposalBudget) -> list[str]:
    notes: list[str] = []
    for field_name, label in _DIFF_FIELDS:
        b = getattr(before, field_name)
        a = getattr(after, field_name)
        if b != a:
            notes.append(f"{label} corrected (${b if b is not None else 0} → ${a if a is not None else 0})")

    if len(before.line_items) != len(after.line_items):
        notes.append(
            f"line items {len(before.line_items)} → {len(after.line_items)} "
            "(envelope/duplicate rows reconciled)"
        )
    else:
        changed_items = sum(
            1
            for b_item, a_item in zip(before.line_items, after.line_items)
            if b_item.extended != a_item.extended
            or b_item.line_item_type != a_item.line_item_type
            or b_item.rate != a_item.rate
        )
        if changed_items:
            notes.append(
                f"{changed_items} line item(s) recalculated (classification and/or arithmetic — "
                "e.g. a travel line no longer double-counted as an agency fee)"
            )

    new_flags = [f for f in (after.pricing_flags or []) if f not in (before.pricing_flags or [])]
    if new_flags:
        notes.append(f"{len(new_flags)} new pricing flag(s) recorded for review")

    if not notes:
        notes.append("budget prose re-synced to canonical totals")
    return notes


def _rate_card_from_research(research: ProposalResearchCache, *, rfp_id: str):
    """Mirrors run_fulfill_budget_scan's rate-card load — same underbid floor
    check must fire here too, or a budget generated before the floor check
    existed would never be floor-checked by the (only reachable) Scan-RFP
    button. A missing/invalid card never halts (see collect_underbid_violations)."""
    if not research.pricing_rate_card:
        return None
    try:
        from app.models.pricing_rate_card import PricingRateCard

        return PricingRateCard.model_validate(research.pricing_rate_card)
    except Exception:  # noqa: BLE001
        logger.warning(
            "pricing_rate_card invalid on cache for %s — skipping underbid floor check in scan",
            rfp_id,
        )
        return None


def _sync_budget_section(draft: ProposalDraft, budget: ProposalBudget, *, rfp_text: str) -> ProposalDraft:
    content = render_budget_markdown(budget, rfp_text=rfp_text)
    sections = list(draft.sections)
    idx = find_budget_section_index(sections)
    if idx is not None:
        sections[idx] = sections[idx].model_copy(update={"content": content, "status": "generated"})
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
    return draft.model_copy(
        update={"sections": sections, "updated_at": datetime.now(timezone.utc).isoformat()}
    )


def check_and_repair_budget_for_scan(
    *,
    rfp_id: str,
    draft: ProposalDraft,
    research: ProposalResearchCache | None,
    rfp_text: str,
) -> BudgetScanCheckResult:
    """Check the persisted budget during Scan-RFP; repair deterministically when possible.

    No LLM calls. No budget on the proposal yet is not an error (status
    "none"). A clean budget makes no changes (status "ok" — idempotent: a
    second scan on an already-repaired budget also lands here). A defect
    reconcile can fix outright lands as "repaired" and is persisted. A defect
    that is a pricing JUDGEMENT (underbid vs the guide floor, an RFP-forbidden
    travel line) or a genuinely unrepairable invariant is reported for a
    human and never papered over with invented numbers.
    """
    logs: list[str] = []
    if research is None or research.budget is None:
        logs.append("Budget check: no pricing model on this proposal yet — skipped.")
        return BudgetScanCheckResult(
            draft=draft,
            research=research,
            status="none",
            changed=False,
            repaired_notes=[],
            escalation_notes=[],
            logs=logs,
        )

    budget = research.budget
    rate_card = _rate_card_from_research(research, rfp_id=rfp_id)
    rfp_context = (rfp_text or "")[:80_000]

    # The manuscript's CURRENT rendered Budget & Pricing section can be
    # internally inconsistent (fee + direct + pass-through != total) even
    # when the canonical ProposalBudget object is fine — e.g. stale
    # LLM-authored prose from an earlier pass. Checked independently of the
    # object-level repair below so a re-render still happens when only the
    # prose, not the object, is broken.
    sections = list(draft.sections)
    budget_idx = find_budget_section_index(sections)
    prose_violations: list[str] = []
    if budget_idx is not None:
        prose_violations = collect_prose_arithmetic_violations(sections[budget_idx].content or "")
        if prose_violations:
            logs.append(
                "Budget check: rendered budget prose does not add up — "
                + "; ".join(prose_violations)
            )

    try:
        repaired = run_budget_editor_pass(
            budget,
            rfp_sections=research.rfp_sections,
            rfp_context=rfp_context,
            rate_card=rate_card,
        )
    except ProposalError as exc:
        # run_budget_editor_pass already retried reconcile once internally
        # and still halted (it always does, before ever raising — see
        # run_budget_editor_pass). Classify the residual defect WITHOUT
        # re-raising: a bad budget must surface as a finding here, never
        # abort the whole Scan-RFP click.
        reconciled = reconcile_proposal_budget(
            budget, rfp_sections=research.rfp_sections, rfp_context=rfp_context
        )
        invariant_errors = validate_budget_canonical(reconciled)
        if invariant_errors:
            logs.append(
                "Budget check: math could not be reconciled deterministically — "
                "escalated for review, budget left untouched."
            )
            return BudgetScanCheckResult(
                draft=draft,
                research=research,
                status="needs_human",
                changed=False,
                repaired_notes=[],
                escalation_notes=list(invariant_errors),
                logs=logs,
            )

        # Arithmetic/classification is clean once reconciled — apply that
        # deterministic fix even though a pricing-judgement gate still
        # blocks a clean pass (underbid floor / RFP-forbidden travel).
        # Never invent dollars to clear either gate.
        underbid = collect_underbid_violations(reconciled, rate_card)
        rfp_violations = collect_rfp_constraint_violations(reconciled, rfp_context)
        escalation = [*underbid, *rfp_violations]
        if not escalation:
            # Defensive: run_budget_editor_pass raised for a reason this
            # single extra reconcile pass did not reproduce — surface the
            # original message rather than silently dropping it.
            escalation = [str(exc)]

        changed = reconciled.model_dump() != budget.model_dump()
        repaired_notes = _describe_budget_repair(budget, reconciled) if changed else []
        status = "repaired_needs_human" if changed else "needs_human"
        logs.append(
            ("Budget check: repaired arithmetic and " if changed else "Budget check: ")
            + "escalated a pricing-judgement issue for review — "
            + "; ".join(escalation)
        )
        out_research = research.model_copy(update={"budget": reconciled}) if changed else research
        out_draft = (
            _sync_budget_section(draft, reconciled, rfp_text=rfp_text) if changed else draft
        )
        return BudgetScanCheckResult(
            draft=out_draft,
            research=out_research,
            status=status,
            changed=changed,
            repaired_notes=repaired_notes,
            escalation_notes=escalation,
            logs=logs,
        )

    object_changed = repaired.model_dump() != budget.model_dump()
    changed = object_changed or bool(prose_violations)
    if not changed:
        logs.append("Budget check: reconciled cleanly — no changes needed.")
        return BudgetScanCheckResult(
            draft=draft,
            research=research,
            status="ok",
            changed=False,
            repaired_notes=[],
            escalation_notes=[],
            logs=logs,
        )

    repaired_notes = _describe_budget_repair(budget, repaired) if object_changed else [
        "rendered budget prose re-synced to canonical totals"
    ]
    out_research = research.model_copy(update={"budget": repaired})
    out_draft = _sync_budget_section(draft, repaired, rfp_text=rfp_text)
    logs.append("Budget check: repaired — " + "; ".join(repaired_notes))
    return BudgetScanCheckResult(
        draft=out_draft,
        research=out_research,
        status="repaired",
        changed=True,
        repaired_notes=repaired_notes,
        escalation_notes=[],
        logs=logs,
    )
