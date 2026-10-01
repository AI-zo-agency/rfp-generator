"""Stage 3 budget: zö pricing docs + Stage 1/2 context + RFP text."""

from __future__ import annotations

import logging

from app.models.proposal import (
    ProposalBudget,
    ProposalResearchCache,
)
from app.models.rfp import RfpRecord
from app.services import llm, pricing_kb, supermemory
from app.services.proposal_common import ProposalError, load_rfp_for_proposal
from app.services.proposal_repository import aget_research_cache, asave_research_cache

logger = logging.getLogger(__name__)

PRICING_DOCS_MARKER = "=== zö pricing docs (Pricing Book + Rules and Wording) ==="


async def fetch_pricing_guide_context(
    rfp: RfpRecord,
    *,
    stage_two: str = "",
    focus_hint: str = "",
) -> tuple[str, list[str]]:
    """Client-safe pricing context for chat explain and section edits.

    The Rules and Wording doc plus the Pricing Book (catalog prices, fees, billing terms). The internal
    doc (costs, margins, loaded rates) is never returned here; only the budget engine reads it.
    """
    if not supermemory.is_configured():
        return "(Supermemory not configured.)", []
    try:
        book = (await pricing_kb.load_pricing_book()).book
    except ProposalError as exc:
        logger.warning("pricing docs unavailable for rfp_id=%s: %s", rfp.id, exc)
        return f"(No usable pricing docs in the KB: {exc})", []
    text = f"{book.rules_md}\n\n{book.book_md}"
    return text, [f"Pricing Book {book.version}", f"Rules and Wording {book.version}"]


# --- Phase 3.5 -----------------------------------------------------------------


async def generate_proposal_budget(rfp_id: str) -> tuple[ProposalBudget, ProposalResearchCache]:
    """Phase 3.5 budget: pricing plan (asks -> LLM describes the work -> code prices and checks -> render)."""
    if not llm.is_configured():
        raise ProposalError("LLM not configured.", status_code=503)

    from app.services.go_no_go_service import combine_rfp_text
    from app.services.pricing_plan_service import generate_pricing_plan_budget

    _rfp, content, _rfp_context = load_rfp_for_proposal(rfp_id)
    prior_research = await aget_research_cache(rfp_id)
    full_rfp = combine_rfp_text(content.description, content.pdf_text)
    target = prior_research.target_budget_usd if prior_research else None
    budget = await generate_pricing_plan_budget(rfp_id, full_rfp, target_budget_usd=target)
    research = prior_research or ProposalResearchCache(
        rfpId=rfp_id, updatedAt=budget.updated_at, provider=budget.provider
    )
    research = research.model_copy(update={"budget": budget})
    await asave_research_cache(research)
    return budget, research


async def reconcile_cached_budget(rfp_id: str) -> tuple[ProposalBudget, ProposalResearchCache]:
    """Return the cached budget unchanged (pricing plans are final; legacy budgets are frozen)."""
    research = await aget_research_cache(rfp_id)
    if not research or not research.budget:
        raise ProposalError(
            "No cached budget to reconcile. Run Phase 3.5 budget generation first.",
            status_code=400,
        )
    return research.budget, research
