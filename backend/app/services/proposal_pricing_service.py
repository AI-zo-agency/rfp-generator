"""Stage 3 budget: 00_Guide_Pricing + Stage 1/2 context + RFP excerpt."""

from __future__ import annotations

import logging
import re

from app.models.proposal import (
    ProposalBudget,
    ProposalResearchCache,
)
from app.models.rfp import RfpRecord
from app.services import llm, supermemory
from app.services.proposal_common import ProposalError, load_rfp_for_proposal
from app.services.proposal_knowledge_base_tools import search_knowledge_base
from app.services.proposal_repository import aget_research_cache, asave_research_cache

logger = logging.getLogger(__name__)

GUIDE_SEARCH_CHAR_LIMIT = 24_000
# Full pinned guide is ~34k; do not truncate below that or PM lines (9.x) are lost.
PINNED_GUIDE_CHAR_LIMIT = 80_000
# Canonical pricing guide — always pin by filename for rate-card builds.
PRICING_GUIDE_FILE_NAMES: tuple[str, ...] = (
    "00_Guide_Pricing.docx",
    "00_Guide_Pricing.pdf",
    "00_Guide_Pricing.md",
)
# Role billable card — pin by Supermemory *title* first (UI: Labor Cost).
LABOR_RATE_CARD_TITLES: tuple[str, ...] = (
    "Labor Cost",
    "Labor Costs",
)
# Filename fallback when title lookup misses (rename / (1) variants).
LABOR_RATE_CARD_FILE_NAMES: tuple[str, ...] = (
    "Agency Role Rates & Cost Table. (1).docx",
    "Agency Role Rates & Cost Table.docx",
)
PINNED_LABOR_CHAR_LIMIT = 40_000


async def fetch_pricing_guide_context(
    rfp: RfpRecord,
    *,
    stage_two: str = "",
    focus_hint: str = "",
) -> tuple[str, list[str]]:
    """Retrieve 00_Guide_Pricing from Supermemory (Stage 3, chat explain, section edits)."""
    return await _fetch_guide_context(rfp, stage_two, focus_hint=focus_hint)


async def _fetch_pinned_pricing_guide() -> tuple[str, list[str]] | None:
    """Load the canonical pricing guide by exact filename (full indexed text).

    Prefer this over fuzzy search: hybrid hits are summaries, documents hits are
    mid-table chunks, and query thresholds can return 0 hits even when the doc exists.
    """
    if not supermemory.is_configured():
        return None
    last_error: Exception | None = None
    for file_name in PRICING_GUIDE_FILE_NAMES:
        try:
            document = await supermemory.find_document_by_file_name(file_name)
            if not document:
                continue
            custom_id = supermemory.document_fetch_key(document)
            if not custom_id:
                logger.warning(
                    "pricing_guide_pin_missing_fetch_key file_name=%s",
                    file_name,
                )
                continue
            content = await supermemory.get_document_content(custom_id=custom_id)
            if not (content or "").strip():
                logger.warning(
                    "pricing_guide_pin_empty_content file_name=%s custom_id=%s",
                    file_name,
                    custom_id,
                )
                continue
            text = content.strip()
            if len(text) > PINNED_GUIDE_CHAR_LIMIT:
                text = text[:PINNED_GUIDE_CHAR_LIMIT]
            logger.info(
                "pricing_guide_pinned file_name=%s chars=%s",
                file_name,
                len(text),
            )
            return text, [file_name]
        except supermemory.SupermemoryError as exc:
            last_error = exc
            logger.warning(
                "pricing_guide_pin_failed file_name=%s error=%s",
                file_name,
                exc,
            )
    if last_error:
        logger.warning("pricing_guide_pin_exhausted last_error=%s", last_error)
    return None


async def _fetch_pinned_labor_rate_card() -> tuple[str, list[str]] | None:
    """Load the Agency Role Rates / Labor Cost card (full text) for billable $/hr.

    Prefer Supermemory document *title* (Labor Cost). Fall back to known
    filenames so renames still resolve. Result is injected into Stage 3 context
    as an explicit retrieve chunk — not fuzzy-search-only.
    """
    if not supermemory.is_configured():
        return None

    async def _content_from_doc(document: dict, *, label: str) -> tuple[str, str] | None:
        custom_id = supermemory.document_fetch_key(document)
        if not custom_id:
            logger.warning("labor_rate_card_pin_missing_fetch_key label=%s", label)
            return None
        content = await supermemory.get_document_content(custom_id=custom_id)
        if not (content or "").strip():
            logger.warning(
                "labor_rate_card_pin_empty_content label=%s custom_id=%s",
                label,
                custom_id,
            )
            return None
        text = content.strip()
        if len(text) > PINNED_LABOR_CHAR_LIMIT:
            text = text[:PINNED_LABOR_CHAR_LIMIT]
        return text, label

    for title in LABOR_RATE_CARD_TITLES:
        try:
            document = await supermemory.find_document_by_title(title)
            if not document:
                continue
            loaded = await _content_from_doc(document, label=title)
            if loaded is None:
                continue
            text, label = loaded
            logger.info(
                "labor_rate_card_pinned_by_title title=%s chars=%s",
                label,
                len(text),
            )
            return text, [label]
        except supermemory.SupermemoryError as exc:
            logger.warning(
                "labor_rate_card_title_pin_failed title=%s error=%s",
                title,
                exc,
            )

    for file_name in LABOR_RATE_CARD_FILE_NAMES:
        try:
            document = await supermemory.find_document_by_file_name(file_name)
            if not document:
                continue
            loaded = await _content_from_doc(document, label=file_name)
            if loaded is None:
                continue
            text, label = loaded
            logger.info(
                "labor_rate_card_pinned_by_filename file_name=%s chars=%s",
                label,
                len(text),
            )
            return text, [label]
        except supermemory.SupermemoryError as exc:
            logger.warning(
                "labor_rate_card_filename_pin_failed file_name=%s error=%s",
                file_name,
                exc,
            )

    logger.warning("labor_rate_card_pin_miss — no Labor Cost title/filename hit")
    return None


async def _fetch_labor_role_rate_context(
    rfp: RfpRecord,
    *,
    focus_hint: str = "",
) -> tuple[str, list[str]]:
    """KB-wide retrieve of role / classification billable hourly tables.

    Searches pricing + reference (and unfiltered). When a hit filename looks like
    a role/labor rate card, upgrade that hit to the **full** indexed document so
    the billable table is not truncated by chunk merge.
    """
    if not supermemory.is_configured():
        return "", []

    hint = (focus_hint or "")[:200]
    queries = [
        "billable rate by role labor classification Account Manager Creative Director hourly USD",
        "role rates cost table billable per hour copywriter art director agency director",
        "hourly labor category rates Project Manager Digital Team Programming Finance",
    ]
    if hint:
        queries.insert(0, f"billable hourly role rates {hint}")

    chunks: list[str] = []
    sources: list[str] = []
    seen_chunk_keys: set[str] = set()
    for query in queries:
        for category in ("pricing", "reference", None):
            text, srcs = await search_knowledge_base(
                query,
                limit=6,
                category=category,
                max_chars=10_000,
                rfp_client=rfp.client or "",
                rfp_title=rfp.title or "",
            )
            if text and not text.startswith("("):
                key = text[:180]
                if key not in seen_chunk_keys:
                    seen_chunk_keys.add(key)
                    chunks.append(text)
            for src in srcs:
                if src not in sources:
                    sources.append(src)
            if len("\n".join(chunks)) >= 18_000:
                break
        if len("\n".join(chunks)) >= 18_000:
            break

    # Upgrade role/labor rate-card hits to full document text (search chunks truncate).
    full_docs: list[str] = []
    upgraded: list[str] = []
    for src in sources[:12]:
        name = (src or "").strip()
        if not name:
            continue
        if not re.search(
            r"(?i)role\s+rates?|labor\s+cost|billable\s+rate|rate\s+card|cost\s+table",
            name,
        ):
            continue
        # Skip the menu guide — already pinned separately.
        if re.search(r"(?i)00_guide_pricing", name):
            continue
        try:
            document = await supermemory.find_document_by_file_name(name)
            if not document:
                continue
            custom_id = supermemory.document_fetch_key(document)
            if not custom_id:
                continue
            content = await supermemory.get_document_content(custom_id=custom_id)
            body = (content or "").strip()
            if len(body) < 200:
                continue
            full_docs.append(f"[full doc: {name}]\n{body[:12_000]}")
            upgraded.append(name)
            logger.info(
                "labor_role_rates_full_doc rfp_id=%s file_name=%s chars=%s",
                rfp.id,
                name,
                len(body),
            )
        except Exception:
            logger.warning(
                "labor_role_rates_full_doc_failed rfp_id=%s file_name=%s",
                rfp.id,
                name,
                exc_info=True,
            )

    parts = [*full_docs, *chunks]
    combined = "\n\n---\n\n".join(parts)[:24_000]
    if combined:
        logger.info(
            "labor_role_rates_kb_hits rfp_id=%s sources=%s upgraded=%s chars=%s",
            rfp.id,
            sources[:8],
            upgraded[:6],
            len(combined),
        )
    return combined, sources


async def _fetch_guide_context(
    rfp: RfpRecord,
    stage_two: str,
    *,
    focus_hint: str = "",
) -> tuple[str, list[str]]:
    """Retrieve 00_Guide_Pricing (pin first) plus KB labor/role billable rates."""
    if not supermemory.is_configured():
        return "(Supermemory not configured.)", []

    guide_text = ""
    sources: list[str] = []
    pinned = await _fetch_pinned_pricing_guide()
    if pinned is not None:
        guide_text, sources = pinned
    else:
        logger.warning(
            "pricing_guide_pin_miss — falling back to search rfp_id=%s",
            rfp.id,
        )
        scope_hint = stage_two[:200] if stage_two else (rfp.sector or "")
        hint = (focus_hint or "")[:300]
        from app.services.proposal_knowledge_base_tools import sanitize_pricing_guide_query

        queries = [
            "00_Guide_Pricing tier ranges Low Average High discovery strategy content digital media project management contingency qualifying language",
            "00_Guide_Pricing 4.4 Email Newsletter Design Setup one-time average tier",
            "00_Guide_Pricing 9.1 9.2 Project Management short projects campaign-specific 5-8 percent floor",
            sanitize_pricing_guide_query(
                f"00_Guide_Pricing {scope_hint[:120]}",
                rfp_client=rfp.client or "",
                rfp_title=rfp.title or "",
            ),
        ]
        if hint:
            queries.insert(
                1,
                sanitize_pricing_guide_query(
                    f"00_Guide_Pricing {hint[:200]}",
                    rfp_client=rfp.client or "",
                    rfp_title=rfp.title or "",
                ),
            )
        chunks: list[str] = []
        seen_chunk_keys: set[str] = set()
        for query in queries:
            for category in ("pricing", "reference"):
                text, srcs = await search_knowledge_base(
                    query,
                    limit=8,
                    category=category,
                    max_chars=GUIDE_SEARCH_CHAR_LIMIT // 3,
                )
                if text and not text.startswith("("):
                    key = text[:200]
                    if key not in seen_chunk_keys:
                        seen_chunk_keys.add(key)
                        chunks.append(text)
                for src in srcs:
                    if src not in sources:
                        sources.append(src)

        guide_text = "\n\n---\n\n".join(chunks)[:GUIDE_SEARCH_CHAR_LIMIT]
        if not guide_text.strip():
            guide_text = "(No 00_Guide_Pricing content in KB — ingest pricing guide.)"

    # Pin Labor Cost / Agency Role Rates card into the retrieve bundle first so
    # billable $/hr is always present for parse + Stage 3 (not fuzzy-only).
    pinned_labor = await _fetch_pinned_labor_rate_card()
    if pinned_labor is not None:
        labor_pin_text, labor_pin_srcs = pinned_labor
        guide_text = (
            f"{guide_text.rstrip()}\n\n"
            "=== LABOR COST (pinned role billable card) ===\n"
            f"{labor_pin_text.strip()}"
        )
        for src in labor_pin_srcs:
            if src not in sources:
                sources.append(src)

    # Supplement with KB search for classification / role billable hours when
    # the pin is thin or missing alternate role tables.
    labor_text, labor_srcs = await _fetch_labor_role_rate_context(
        rfp, focus_hint=focus_hint or stage_two[:200]
    )
    if labor_text.strip():
        guide_text = (
            f"{guide_text.rstrip()}\n\n"
            "=== KB labor / role billable rates (search — cite source filenames) ===\n"
            f"{labor_text.strip()}"
        )
        for src in labor_srcs:
            if src not in sources:
                sources.append(src)

    return guide_text, sources


# --- Large NTE + media campaign under-utilization check ---------------------

async def generate_proposal_budget(rfp_id: str) -> tuple[ProposalBudget, ProposalResearchCache]:
    """Phase 3.5 budget: pricing plan v2 (asks -> LLM plan -> code checks -> render)."""
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
    """Return the cached budget unchanged (v2 plans are final; legacy budgets are frozen)."""
    research = await aget_research_cache(rfp_id)
    if not research or not research.budget:
        raise ProposalError(
            "No cached budget to reconcile. Run Phase 3.5 budget generation first.",
            status_code=400,
        )
    return research.budget, research
