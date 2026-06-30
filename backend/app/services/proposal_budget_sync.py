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
    r"agency\s+revenue|lump\s*sum|investment\s+reflects|fee\s+structure|"
    r"\$[\d,]+"
    r")\b",
    re.I,
)

FEE_SYNC_PROMPT = """You align proposal narrative sections with the CANONICAL budget (single source of truth).

Rules:
1. Remove or rewrite any pricing tier, dollar total, or fee structure that contradicts the canonical budget.
2. Do NOT invent new dollar amounts — use only values from the canonical budget block.
3. verifiedAgencyRevenue and agencyRevenueEstimate are the SAME number — use it everywhere (Budget Summary, Option Terms, fee narrative).
4. If option-year math appears (e.g. "3 × $X"), X MUST equal verifiedAgencyRevenue / base-year amount — recalculate multiples from the canonical base.
5. qualifyingLanguage-style blocks must use the SAME pricingTier as the budget — never cite a different tier.
6. Preserve all non-pricing content (statutory citations, team, approach, compliance).
7. Never add [VERIFY] tags — leave factual gaps unchanged if not in the budget.
8. Never add pricing reconciliation flags or "verify before submission" notes — math is already verified.

Return ONLY JSON:
{"sections":[{"sectionId":"...","content":"full updated section prose"}]}"""


def _canonical_budget_facts(budget: ProposalBudget) -> str:
    from app.services.proposal_budget_validation import sum_line_items_extended

    line_subtotal = sum_line_items_extended(budget)
    direct = float(budget.direct_expenses_total or 0)
    verified_total = line_subtotal + direct
    lines = [
        f"pricingTier: {budget.pricing_tier or 'Average'}",
        f"lineItemsSubtotal: {line_subtotal}",
        f"directExpensesTotal: {direct}",
        f"verifiedAgencyRevenue (line items + direct): {verified_total}",
        f"agencyRevenueEstimate (USE THIS EXACT VALUE): {budget.agency_revenue_estimate}",
        f"lumpSumTotal (USE THIS EXACT VALUE): {budget.lump_sum_total}",
        f"feeStructure: {budget.fee_structure}",
        f"budgetFormat: {budget.budget_format}",
    ]
    if budget.option_term_notes.strip():
        lines.append(f"optionTermNotes: {budget.option_term_notes[:800]}")
    if budget.qualifying_language.strip():
        lines.append(f"qualifyingLanguage (use this tier language only):\n{budget.qualifying_language[:2000]}")
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
    """Rewrite non-budget sections that mention fees/tiers so they match the budget."""
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
    payload = [
        {
            "sectionId": section.id,
            "title": section.title,
            "content": section.content[:6000],
        }
        for _, section in targets[:6]
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
                        f"{_canonical_budget_facts(budget)}\n\n"
                        f"=== SECTIONS TO ALIGN ===\n"
                        f"{payload}\n\n"
                        f"RFP fee excerpt:\n{rfp_context[:4000]}"
                    ),
                },
            ],
            max_tokens=6144,
            temperature=0.2,
        )
    except LlmError as exc:
        logger.warning("Fee narrative sync skipped for %s: %s", rfp_id, exc)
        return draft

    updates = raw.get("sections") or []
    if not isinstance(updates, list):
        return draft

    by_id = {str(item.get("sectionId") or item.get("id") or ""): item for item in updates if isinstance(item, dict)}

    for index, section in targets:
        item = by_id.get(section.id)
        if not item:
            continue
        content = str(item.get("content") or "").strip()
        if content:
            sections[index] = section.model_copy(update={"content": content})
            logger.info("Fee sync updated section %s (%s)", section.id, section.title)

    return draft.model_copy(update={"sections": sections})
