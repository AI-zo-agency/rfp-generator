"""Keep fee/pricing narrative aligned with the canonical Stage 3 budget."""

from __future__ import annotations

import logging
import re

from app.models.proposal import ProposalBudget, ProposalDraft, ProposalSection
from app.services import llm
from app.services.go_no_go_service import _assess_rfp_content, _build_rfp_context
from app.services.llm import LlmError
from app.services.proposal_budget_content import find_budget_section_index
from app.services.rfp_repository import get_rfp

logger = logging.getLogger(__name__)

_FEE_CONTENT_RE = re.compile(
    r"\b("
    r"pricing\s+tier|low\s+tier|average\s+tier|high\s+tier|"
    r"agency\s+revenue|commission|pass[\s-]*through|lump\s*sum|"
    r"investment\s+reflects|fee\s+structure|option\s+year|"
    r"\$[\d,]+"
    r")\b",
    re.I,
)

FEE_SYNC_PROMPT = """You align proposal narrative sections with the CANONICAL budget (single source of truth).

Rules:
1. Use ONLY dollar values from the canonical budget block — do not invent amounts.
2. agencyRevenueEstimate / agencyFeeSubtotal = zö's actual fee income ONLY — never pass-through client media.
3. clientMediaPassthrough and totalClientInvoicing are separate — use when narrative discusses media spend vs agency commission.
4. Opening paragraphs, Option Terms, Investment Framing, and Section 1 commission statements must cite agencyRevenueEstimate (fee), NOT totalClientInvoicing.
5. Multi-year math (e.g. "3 × $X") must use agencyRevenueEstimate as the annual agency fee base — never multiply pass-through media into "agency revenue."
6. qualifyingLanguage must use the SAME pricingTier as the budget.
7. Preserve all non-pricing content (statutory citations, team, approach, compliance, MWBE, living wage).
8. Never add [VERIFY] tags or "verify before submission" reconciliation notes.
9. Change only pricing-related sentences — preserve strong existing prose elsewhere.
10. NEVER write $0 for agency revenue, annual commission, or "zö fee income" when canonical agencyRevenueEstimate is a positive number — replace every $0 fee line with the canonical amount.
11. NEVER contradict canonical budget: if narrative says $37,500/year commission, Budget Summary and Investment Framing must all match agencyRevenueEstimate exactly.
12. Do not defer reference contacts, workforce %, PSA acknowledgments, or hours tables — those are separate compliance rules; only fix fee/pricing sentences here.

Return ONLY JSON:
{"sections":[{"sectionId":"...","content":"full updated section prose"}]}"""


def _canonical_budget_facts(budget: ProposalBudget) -> str:
    from app.services.proposal_budget_validation import sum_line_items_extended

    line_subtotal = sum_line_items_extended(budget)
    direct = float(budget.direct_expenses_total or 0)
    agency_fee = float(budget.agency_fee_subtotal or line_subtotal)
    passthrough = float(budget.client_media_passthrough or 0)
    lines = [
        f"pricingTier: {budget.pricing_tier or 'Average'}",
        f"lineItemSum (all table rows): {budget.line_item_sum or line_subtotal}",
        f"agencyFeeSubtotal (zö fee rows only): {budget.agency_fee_subtotal or agency_fee}",
        f"clientMediaPassthrough (NOT agency revenue): {passthrough or 0}",
        f"directExpensesTotal: {direct}",
        (
            "agencyRevenueEstimate (USE FOR 'agency revenue' / commission / fee income): "
            f"{budget.agency_revenue_estimate}"
        ),
        (
            "totalClientInvoicing (media pass-through + agency fees — NOT agency revenue): "
            f"{budget.total_client_invoicing or (line_subtotal + direct)}"
        ),
        f"commissionRate: {budget.commission_rate}",
        f"lumpSumTotal: {budget.lump_sum_total}",
        f"feeStructure: {budget.fee_structure}",
        f"budgetFormat: {budget.budget_format}",
        f"commissionModel: {budget.commission_model or '(none)'}",
    ]
    if budget.option_term_notes.strip():
        lines.append(f"optionTermNotes (canonical):\n{budget.option_term_notes[:1200]}")
    if budget.qualifying_language.strip():
        lines.append(f"qualifyingLanguage:\n{budget.qualifying_language[:2000]}")
    if budget.media_spend_notes.strip():
        lines.append(f"mediaSpendNotes:\n{budget.media_spend_notes[:800]}")
    revenue = float(budget.agency_revenue_estimate or 0)
    if revenue <= 0:
        lines.append(
            "CRITICAL: agencyRevenueEstimate is ZERO — do NOT write $0 in narrative; "
            "run budget reconcile or set commissionRate × clientMediaPassthrough first."
        )
    return "\n".join(lines)


def _needs_fee_sync(section: ProposalSection, budget_idx: int | None, index: int) -> bool:
    if budget_idx is not None and index == budget_idx:
        return False
    if not section.content.strip():
        return False
    return bool(_FEE_CONTENT_RE.search(section.content))


async def align_fee_narrative_with_budget(
    *,
    rfp_id: str,
    draft: ProposalDraft,
    budget: ProposalBudget,
) -> ProposalDraft:
    """Rewrite ALL non-budget sections that mention fees/tiers/dollars to match canonical budget."""
    sections = list(draft.sections)
    budget_idx = find_budget_section_index(sections)
    targets: list[tuple[int, ProposalSection]] = []
    for index, section in enumerate(sections):
        if _needs_fee_sync(section, budget_idx, index):
            targets.append((index, section))

    if not targets:
        return draft

    rfp = get_rfp(rfp_id)
    if not rfp:
        logger.warning("Fee sync skipped — RFP %s not found", rfp_id)
        return draft
    content = _assess_rfp_content(rfp)
    rfp_context = _build_rfp_context(rfp, content)
    canonical = _canonical_budget_facts(budget)

    updated_sections = list(sections)
    batch_size = 6
    for batch_start in range(0, len(targets), batch_size):
        batch = targets[batch_start : batch_start + batch_size]
        payload = [
            {
                "sectionId": section.id,
                "title": section.title,
                "content": section.content[:6000],
            }
            for _, section in batch
        ]
        try:
            raw, _provider = await llm.chat_json(
                [
                    {"role": "system", "content": FEE_SYNC_PROMPT},
                    {
                        "role": "user",
                        "content": (
                            f"RFP: {rfp.title}\nClient: {rfp.client}\n\n"
                            f"=== CANONICAL BUDGET (only source for tiers and totals) ===\n"
                            f"{canonical}\n\n"
                            f"=== SECTIONS TO ALIGN ({len(payload)} sections) ===\n"
                            f"{payload}\n\n"
                            f"RFP fee excerpt:\n{rfp_context[:4000]}"
                        ),
                    },
                ],
                max_tokens=8192,
                temperature=0.2,
            )
        except LlmError as exc:
            logger.warning(
                "Fee narrative sync batch %d skipped for %s: %s",
                batch_start // batch_size + 1,
                rfp_id,
                exc,
            )
            continue

        updates = raw.get("sections") or []
        if not isinstance(updates, list):
            continue

        by_id = {
            str(item.get("sectionId") or item.get("id") or ""): item
            for item in updates
            if isinstance(item, dict)
        }

        for index, section in batch:
            item = by_id.get(section.id)
            if not item:
                continue
            new_content = str(item.get("content") or "").strip()
            if new_content:
                updated_sections[index] = section.model_copy(update={"content": new_content})
                logger.info("Fee sync updated section %s (%s)", section.id, section.title)

    return draft.model_copy(update={"sections": updated_sections})
