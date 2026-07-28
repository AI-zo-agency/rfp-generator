"""Templated budget money slots for narrative (T5.4).

Writers emit ``{{budget.agency_revenue}}`` etc.; renderer substitutes from
canonical ProposalBudget. Unresolved slots are left in place and reported.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable

from app.models.proposal import ProposalBudget, ProposalDraft

logger = logging.getLogger(__name__)

_SLOT_RE = re.compile(r"\{\{\s*budget\.([a-zA-Z0-9_]+)\s*\}\}")


def _fmt_usd(value: float | None) -> str | None:
    if value is None:
        return None
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return None
    if amount < 0:
        return None
    return f"${amount:,.2f}"


_SLOT_RESOLVERS: dict[str, Callable[[ProposalBudget], float | None]] = {
    "agency_revenue": lambda b: b.agency_revenue_estimate,
    "agency_revenue_estimate": lambda b: b.agency_revenue_estimate,
    "lump_sum_total": lambda b: b.lump_sum_total,
    "total_client_invoicing": lambda b: b.total_client_invoicing,
    "client_media_passthrough": lambda b: b.client_media_passthrough,
    "agency_fee_subtotal": lambda b: b.agency_fee_subtotal,
    "line_item_sum": lambda b: b.line_item_sum,
    "direct_expenses_total": lambda b: b.direct_expenses_total,
    "rfp_budget_cap": lambda b: b.rfp_budget_cap,
}


def list_budget_slot_keys() -> list[str]:
    return sorted(_SLOT_RESOLVERS.keys())


def render_budget_slots(
    text: str,
    budget: ProposalBudget | None,
) -> tuple[str, list[str]]:
    """Return (rendered_text, unresolved_slot_keys)."""
    if not text or "{{" not in text:
        return text or "", []
    unresolved: list[str] = []

    def _replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if budget is None:
            unresolved.append(key)
            return match.group(0)
        resolver = _SLOT_RESOLVERS.get(key)
        if resolver is None:
            unresolved.append(key)
            return match.group(0)
        formatted = _fmt_usd(resolver(budget))
        if formatted is None:
            unresolved.append(key)
            return match.group(0)
        return formatted

    rendered = _SLOT_RE.sub(_replace, text)
    # Dedupe preserving order
    seen: set[str] = set()
    uniq: list[str] = []
    for key in unresolved:
        if key not in seen:
            seen.add(key)
            uniq.append(key)
    if uniq:
        logger.info("budget_slots_unresolved keys=%s", uniq)
    return rendered, uniq


def find_unresolved_budget_slots(text: str) -> list[str]:
    return list(dict.fromkeys(_SLOT_RE.findall(text or "")))


def render_draft_budget_slots(
    draft: ProposalDraft,
    budget: ProposalBudget | None,
) -> tuple[ProposalDraft, list[str]]:
    """Render slots across all sections; return draft + all unresolved keys."""
    all_unresolved: list[str] = []
    sections = []
    for section in draft.sections:
        new_content, unresolved = render_budget_slots(section.content or "", budget)
        all_unresolved.extend(unresolved)
        if new_content != (section.content or ""):
            sections.append(section.model_copy(update={"content": new_content}))
        else:
            sections.append(section)
    # Also keep unresolved tokens that remain after failed render
    for section in sections:
        all_unresolved.extend(find_unresolved_budget_slots(section.content or ""))
    uniq = list(dict.fromkeys(all_unresolved))
    logger.info(
        "draft_budget_slots_rendered rfp_id=%s unresolved=%s",
        draft.rfp_id,
        len(uniq),
    )
    return draft.model_copy(update={"sections": sections}), uniq


def money_slots_prompt_hint() -> str:
    keys = ", ".join(f"{{{{budget.{k}}}}}" for k in (
        "agency_revenue",
        "total_client_invoicing",
        "client_media_passthrough",
        "lump_sum_total",
    ))
    return (
        "MONEY SLOTS: For canonical totals, emit placeholders exactly as "
        f"{keys} instead of typing dollar amounts. Never invent totals."
    )
