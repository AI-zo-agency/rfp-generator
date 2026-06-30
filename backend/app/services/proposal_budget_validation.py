"""Validate and reconcile Stage 3 budget math — no RFP-specific hardcoding."""

from __future__ import annotations

import re
from typing import Any

from app.models.proposal import BudgetLineItem, ProposalBudget, RfpSectionMap

_LUMP_SUM_RE = re.compile(
    r"\b(lump\s*sum|total\s*(?:contract|project)\s*(?:price|cost|amount)|not[\s-]*to[\s-]*exceed|nte)\b",
    re.I,
)
_HOURLY_RE = re.compile(r"\b(hourly\s*rate|rate\s*per\s*hour|loaded\s*rate)\b", re.I)
_ESCALATION_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*%\s*(?:annual|yearly|per\s*year)?\s*(?:escalat|increase)",
    re.I,
)
_ESCALATION_ALT_RE = re.compile(
    r"escalat(?:ion|e)[^.]{0,40}?(\d+(?:\.\d+)?)\s*%",
    re.I,
)
_BASE_TERM_YEARS_RE = re.compile(
    r"\b(\d+)[\s-]*(?:year|yr)\s*(?:base|initial|term|contract)",
    re.I,
)
_OPTION_YEAR_RE = re.compile(r"\boption\s+year\s+(\d+)\b", re.I)
_STALE_RECONCILIATION_FLAG_RE = re.compile(
    r"reconciled to match line items|lump sum set to line-item total",
    re.I,
)
_USD_IN_TEXT_RE = re.compile(r"\$[\d,]+(?:\.\d+)?")


def _usd(value: float) -> str:
    return f"${value:,.0f}"


def fix_line_item_extended_values(line_items: list[BudgetLineItem]) -> list[BudgetLineItem]:
    """Recompute extended = rate × quantity when both are present."""
    fixed: list[BudgetLineItem] = []
    for item in line_items:
        rate, qty, ext = item.rate, item.quantity, item.extended
        if rate is not None and qty is not None:
            computed = round(float(rate) * float(qty), 2)
            if ext is None or abs(float(ext) - computed) > 0.01:
                item = item.model_copy(update={"extended": computed})
        fixed.append(item)
    return fixed


def sum_line_items_extended(budget: ProposalBudget) -> float:
    total = 0.0
    for item in budget.line_items:
        if isinstance(item.extended, (int, float)):
            total += float(item.extended)
    return round(total, 2)


def rfp_requires_lump_sum_and_hourly(
    rfp_sections: list[RfpSectionMap] | None,
    rfp_context: str = "",
) -> bool:
    """True when RFP text asks for both lump sum and hourly pricing."""
    blobs: list[str] = [rfp_context[:40_000]]
    for section in rfp_sections or []:
        blobs.append(section.title or "")
        blobs.extend(section.requirements or [])
    text = "\n".join(blobs)
    return bool(_LUMP_SUM_RE.search(text) and _HOURLY_RE.search(text))


def _parse_escalation_rate(*texts: str) -> float | None:
    for text in texts:
        if not text:
            continue
        for pattern in (_ESCALATION_RE, _ESCALATION_ALT_RE):
            match = pattern.search(text)
            if match:
                return float(match.group(1)) / 100.0
    return None


def _parse_base_term_years(*texts: str) -> int | None:
    for text in texts:
        if not text:
            continue
        match = _BASE_TERM_YEARS_RE.search(text)
        if match:
            years = int(match.group(1))
            if 1 <= years <= 10:
                return years
    return None


def _count_option_years(*texts: str) -> int:
    years: set[int] = set()
    for text in texts:
        if not text:
            continue
        for match in _OPTION_YEAR_RE.finditer(text):
            years.add(int(match.group(1)))
    return max(years) if years else 0


def _append_fee_structure_note(existing: str, note: str) -> str:
    note = note.strip()
    if not note:
        return existing.strip()
    if note.lower() in existing.lower():
        return existing.strip()
    if existing.strip():
        return f"{existing.strip()}\n\n{note}"
    return note


def rebuild_option_term_notes(
    budget: ProposalBudget,
    *,
    rfp_context: str = "",
) -> str:
    """Rebuild option-year prose from verified base revenue (no guessed escalation)."""
    base = budget.agency_revenue_estimate
    if base is None or base <= 0:
        return budget.option_term_notes

    context_blob = "\n".join(
        part for part in (rfp_context[:20_000], budget.option_term_notes, budget.rfp_budget_notes) if part
    )
    escalation = _parse_escalation_rate(context_blob)
    base_years = _parse_base_term_years(context_blob) or 1
    option_years = _count_option_years(context_blob)

    lines: list[str] = []
    if base_years > 1:
        lines.append(
            f"Base {base_years}-year agency revenue estimate: {_usd(base * base_years)} "
            f"({base_years} × {_usd(base)})."
        )
    else:
        lines.append(f"Base-year agency revenue estimate: {_usd(base)}.")

    if escalation is not None and option_years > 0:
        pct = escalation * 100
        prior = base
        for year in range(1, option_years + 1):
            amount = round(prior * (1 + escalation), 2)
            if year == 1:
                lines.append(
                    f"Option Year {year}: {_usd(amount)} ({pct:g}% escalation on base year)."
                )
            else:
                lines.append(
                    f"Option Year {year}: {_usd(amount)} ({pct:g}% escalation on Option Year {year - 1})."
                )
            prior = amount
    elif budget.option_term_notes.strip() and not _USD_IN_TEXT_RE.findall(budget.option_term_notes):
        lines.append(budget.option_term_notes.strip())

    return "\n".join(lines).strip()


def _strip_stale_reconciliation_flags(flags: list[str]) -> list[str]:
    return [flag for flag in flags if not _STALE_RECONCILIATION_FLAG_RE.search(flag)]


def reconcile_proposal_budget(
    budget: ProposalBudget,
    *,
    rfp_sections: list[RfpSectionMap] | None = None,
    rfp_context: str = "",
) -> ProposalBudget:
    """
    Deterministic budget reconciliation:
    1. Fix line-item extended = rate × qty
    2. Ground truth = sum(line items) + direct expenses
    3. Propagate ground truth to agencyRevenueEstimate and lump sum (when RFP requires both)
    4. Rebuild option-term math from verified base
    5. Remove stale reconciliation flags — never leave open math-discrepancy flags
    """
    flags = _strip_stale_reconciliation_flags(list(budget.pricing_flags))

    line_items = fix_line_item_extended_values(budget.line_items)
    subtotal = sum_line_items_extended(
        budget.model_copy(update={"line_items": line_items})
    )
    direct = round(float(budget.direct_expenses_total or 0), 2)
    computed = round(subtotal + direct, 2)

    updates: dict[str, Any] = {"line_items": line_items}
    if computed > 0:
        updates["agency_revenue_estimate"] = computed

    requires_lump_hourly = rfp_requires_lump_sum_and_hourly(rfp_sections, rfp_context)
    lump = budget.lump_sum_total
    if requires_lump_hourly and computed > 0:
        if lump is None or abs(float(lump) - computed) > max(1.0, computed * 0.01):
            updates["lump_sum_total"] = computed
            updates["fee_structure"] = _append_fee_structure_note(
                budget.fee_structure,
                (
                    f"Lump sum ({_usd(computed)}) equals the sum of all budget line items"
                    f"{f' plus direct expenses ({_usd(direct)})' if direct > 0 else ''}; "
                    "the hourly/per-unit table above is the underlying cost build."
                ),
            )
    elif lump is not None and computed > 0 and abs(float(lump) - computed) > max(1.0, computed * 0.01):
        updates["lump_sum_total"] = computed

    tier = (budget.pricing_tier or "").strip()
    if tier and budget.qualifying_language:
        ql = budget.qualifying_language
        other_tiers = {"Low", "Average", "High"} - {tier}
        for other in other_tiers:
            if re.search(rf"\b{other}\s+tier\b", ql, re.I):
                flags.append(
                    f"[PRICING FLAG: qualifyingLanguage mentions {other} tier but pricingTier is "
                    f"{tier} — reconcile to one tier only]"
                )

    merged = budget.model_copy(update=updates)
    merged = merged.model_copy(
        update={
            "option_term_notes": rebuild_option_term_notes(
                merged,
                rfp_context=rfp_context,
            ),
            "pricing_flags": flags,
        }
    )
    return merged


def collect_budget_invariant_violations(budget: ProposalBudget) -> list[str]:
    """Return human-readable violations when budget math or flags are unreconciled."""
    violations: list[str] = []
    subtotal = sum_line_items_extended(budget)
    direct = round(float(budget.direct_expenses_total or 0), 2)
    expected = round(subtotal + direct, 2)

    revenue = budget.agency_revenue_estimate
    if expected > 0:
        if revenue is None:
            violations.append("agencyRevenueEstimate is missing")
        elif abs(float(revenue) - expected) > 0.01:
            violations.append(
                f"agencyRevenueEstimate ({revenue}) != line items ({subtotal}) + direct ({direct})"
            )

    lump = budget.lump_sum_total
    if lump is not None and expected > 0 and abs(float(lump) - expected) > max(1.0, expected * 0.01):
        violations.append(f"lumpSumTotal ({lump}) != verified total ({expected})")

    blob_parts = [
        budget.fee_structure,
        budget.qualifying_language,
        budget.option_term_notes,
        " ".join(budget.pricing_flags),
    ]
    blob = "\n".join(part for part in blob_parts if part)
    if re.search(
        r"\bverify\b[^.\n]{0,60}\b(before\s+submission|before\s+submitting|submission)\b",
        blob,
        re.I,
    ):
        violations.append("budget object still contains verify-before-submission language")

    for flag in budget.pricing_flags:
        if _STALE_RECONCILIATION_FLAG_RE.search(flag):
            violations.append(f"stale reconciliation flag remains: {flag[:100]}")

    return violations


def assert_budget_invariants(budget: ProposalBudget) -> None:
    """Raise ValueError when budget fails post-reconcile invariants."""
    violations = collect_budget_invariant_violations(budget)
    if violations:
        raise ValueError("; ".join(violations))


def parse_budget_extras(raw: dict[str, Any]) -> dict[str, Any]:
    """Extract optional lump-sum / direct-expense fields from LLM JSON."""
    extras: dict[str, Any] = {}
    for key, alias in (
        ("lumpSumTotal", "lump_sum_total"),
        ("directExpensesTotal", "direct_expenses_total"),
    ):
        val = raw.get(key) if key in raw else raw.get(alias)
        if isinstance(val, (int, float)) and float(val) >= 0:
            extras[alias] = float(val)
    return extras
