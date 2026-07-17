"""Validate and reconcile Stage 3 budget math — no RFP-specific hardcoding."""

from __future__ import annotations

import re
from typing import Any

from app.models.proposal import BudgetLineItem, BudgetLineItemType, ProposalBudget, RfpSectionMap

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
_VERIFY_BEFORE_SUBMIT_RE = re.compile(
    r"\bverify\b[^.\n]{0,60}\b(before\s+submission|before\s+submitting|submission)\b",
    re.I,
)
_USD_IN_TEXT_RE = re.compile(r"\$[\d,]+(?:\.\d+)?")
_COMMISSION_MODEL_RE = re.compile(
    r"\bcommission\b|85\s*/\s*15|media\s+placement|passthrough|pass[\s-]*through",
    re.I,
)
_PASSTHROUGH_LINE_RE = re.compile(
    r"pass[\s-]*through|client\s+media|media\s+spend|placement\s+at\s+net|"
    r"gross\s+media|net\s+media|advertising\s+spend|media\s+placement",
    re.I,
)
_AGENCY_FEE_LINE_RE = re.compile(
    r"\bcommission\b|\bagency\s+fee\b|\bproject\s+management\b|\bstrategy\b|"
    r"\bresearch\b|\breporting\b|\bcreative\b|\bdesign\b|\baccount\s+management\b",
    re.I,
)
_PM_LINE_RE = re.compile(
    r"\bproject\s+management\b|\baccount\s+management\b|\bprogram\s+management\b",
    re.I,
)
_PRICING_FLAG_ADVISORY_RE = re.compile(r"PRICING\s+FLAG", re.I)
_MISPLACED_VERIFY_FLAG_RE = re.compile(r"^\[VERIFY:", re.I)
_PM_RATIO_MIN = 0.05
_PM_RATIO_MAX = 0.08
_PM_RATIO_TARGET = 0.065


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


def infer_line_item_type(item: BudgetLineItem) -> BudgetLineItemType:
    """Classify line item by description — commission-model vs agency-fee vs passthrough."""
    if item.line_item_type:
        return item.line_item_type
    blob = " ".join(
        part
        for part in (item.category, item.description, item.notes or "", item.role_title or "")
        if part
    )
    if _PASSTHROUGH_LINE_RE.search(blob) and not re.search(
        r"\bagency\s+commission\b", blob, re.I
    ):
        return "client_passthrough"
    if _AGENCY_FEE_LINE_RE.search(blob):
        return "agency_fee"
    return "agency_fee"


def split_line_item_totals(
    line_items: list[BudgetLineItem],
) -> tuple[float, float, float]:
    """Return (line_item_sum, agency_fee_subtotal, client_passthrough_subtotal)."""
    agency = 0.0
    passthrough = 0.0
    for item in line_items:
        ext = float(item.extended or 0)
        if ext <= 0:
            continue
        if infer_line_item_type(item) == "client_passthrough":
            passthrough += ext
        else:
            agency += ext
    agency = round(agency, 2)
    passthrough = round(passthrough, 2)
    return round(agency + passthrough, 2), agency, passthrough


def collect_pm_ratio_violations(budget: ProposalBudget) -> list[str]:
    """Flag when PM line items fall outside the 5–8% agency-fee guide."""
    _, agency_fee, _ = split_line_item_totals(budget.line_items)
    base = budget.agency_fee_subtotal
    if base is None:
        base = agency_fee
    base = float(base or 0)
    if base <= 0:
        return []

    pm_total = 0.0
    for item in budget.line_items:
        if _is_pm_line_item(item):
            pm_total += float(item.extended or 0)

    if pm_total <= 0:
        return []

    ratio = pm_total / base
    if 0.05 <= ratio <= 0.08:
        return []

    return [
        (
            f"project management lines (${pm_total:,.0f}) are {ratio * 100:.1f}% of agency fees "
            f"— pricing guide targets 5–8%. Adjust rates or scope with Sonja before submission."
        )
    ]


def is_commission_style_budget(budget: ProposalBudget) -> bool:
    if budget.commission_model and _COMMISSION_MODEL_RE.search(budget.commission_model):
        return True
    if budget.commission_rate is not None and budget.commission_rate > 0:
        return True
    _, _, passthrough = split_line_item_totals(budget.line_items)
    return passthrough > 0


def derive_commission_agency_revenue(budget: ProposalBudget) -> float | None:
    """Annual agency fee from commission rate × client media pass-through when line items are empty."""
    rate = budget.commission_rate
    passthrough = budget.client_media_passthrough
    if rate is None or not passthrough or float(passthrough) <= 0:
        return None
    r = float(rate)
    if r > 1:
        r = r / 100.0
    if r <= 0:
        return None
    return round(float(passthrough) * r, 2)


def render_budget_markdown_for_validation(budget: ProposalBudget) -> str:
    from app.services.proposal_budget_content import render_budget_markdown

    return render_budget_markdown(budget)


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
    """Rebuild option-year prose from verified agency fee base (never pass-through totals)."""
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
    if is_commission_style_budget(budget) and budget.client_media_passthrough:
        lines.append(
            f"Annual agency commission revenue (base year): {_usd(base)}. "
            f"Client media pass-through (at net, not agency revenue): "
            f"{_usd(budget.client_media_passthrough)}."
        )
        if budget.total_client_invoicing:
            lines.append(
                f"Total estimated annual client invoicing (media pass-through + agency fees): "
                f"{_usd(budget.total_client_invoicing)}."
            )

    if base_years > 1:
        lines.append(
            f"Base {base_years}-year agency revenue estimate: {_usd(base * base_years)} "
            f"({base_years} × {_usd(base)} agency fee base)."
        )
    elif not lines:
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


def _strip_misplaced_verify_flags(flags: list[str]) -> list[str]:
    """Drop compliance [VERIFY: …] tags from pricing_flags — they belong in manuscript sections."""
    return [flag for flag in flags if not _MISPLACED_VERIFY_FLAG_RE.match(flag.strip())]


def _is_pm_line_item(item: BudgetLineItem) -> bool:
    desc_blob = " ".join(
        part for part in (item.description, item.role_title or "") if part
    )
    if _PM_LINE_RE.search(desc_blob):
        return True
    cat = (item.category or "").strip()
    if not cat:
        return False
    if re.fullmatch(r"account\s*&\s*project\s*management", cat, re.I):
        return False
    return bool(_PM_LINE_RE.search(cat))


def adjust_pm_line_items_to_guide(
    line_items: list[BudgetLineItem],
    agency_base: float,
) -> tuple[list[BudgetLineItem], str | None]:
    """Scale PM / account-management lines into the 5–8% agency-fee guide when out of range."""
    base = float(agency_base or 0)
    if base <= 0:
        return line_items, None

    pm_indices: list[int] = []
    pm_total = 0.0
    for index, item in enumerate(line_items):
        if _is_pm_line_item(item):
            pm_indices.append(index)
            pm_total += float(item.extended or 0)

    if pm_total <= 0 or not pm_indices:
        return line_items, None

    ratio = pm_total / base
    if _PM_RATIO_MIN <= ratio <= _PM_RATIO_MAX:
        return line_items, None

    target_ratio = _PM_RATIO_TARGET if ratio > _PM_RATIO_MAX else _PM_RATIO_MIN
    target_pm = round(base * target_ratio, 2)
    scale = target_pm / pm_total

    adjusted: list[BudgetLineItem] = []
    for index, item in enumerate(line_items):
        if index not in pm_indices:
            adjusted.append(item)
            continue
        new_ext = round(float(item.extended or 0) * scale, 2)
        rate, qty = item.rate, item.quantity
        if rate is not None and qty is not None and float(qty) > 0:
            adjusted.append(
                item.model_copy(
                    update={"extended": new_ext, "rate": round(new_ext / float(qty), 2)}
                )
            )
        elif rate is not None:
            adjusted.append(item.model_copy(update={"extended": new_ext, "rate": new_ext}))
        else:
            adjusted.append(item.model_copy(update={"extended": new_ext}))

    return adjusted, (
        f"[PRICING FLAG: Project management lines auto-scaled from {ratio * 100:.1f}% "
        f"to {target_ratio * 100:.1f}% of agency fees ({_usd(pm_total)} → {_usd(target_pm)}) "
        f"per 00_Guide_Pricing — Sonja confirm before submission]"
    )


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
    flags = _strip_misplaced_verify_flags(
        _strip_stale_reconciliation_flags(list(budget.pricing_flags))
    )

    line_items = fix_line_item_extended_values(budget.line_items)
    _, agency_fee_seed, _ = split_line_item_totals(line_items)
    agency_base = budget.agency_fee_subtotal
    if agency_base is None:
        agency_base = agency_fee_seed
    line_items, pm_note = adjust_pm_line_items_to_guide(line_items, float(agency_base or 0))
    if pm_note:
        flags.append(pm_note)

    line_sum, agency_fee, passthrough = split_line_item_totals(line_items)
    direct = round(float(budget.direct_expenses_total or 0), 2)
    commission_style = is_commission_style_budget(budget) or passthrough > 0

    if commission_style and passthrough > 0:
        agency_revenue = round(agency_fee + direct, 2)
        total_invoicing = round(line_sum + direct, 2)
    else:
        agency_revenue = round(line_sum + direct, 2)
        total_invoicing = agency_revenue
        agency_fee = round(line_sum, 2)
        passthrough = 0.0

    if commission_style and agency_revenue <= 0:
        derived = derive_commission_agency_revenue(budget)
        if derived is None and budget.lump_sum_total and float(budget.lump_sum_total) > 0:
            derived = round(float(budget.lump_sum_total), 2)
        if derived and derived > 0:
            agency_revenue = round(derived + direct, 2)
            agency_fee = derived
            if passthrough <= 0 and budget.client_media_passthrough:
                passthrough = round(float(budget.client_media_passthrough), 2)
            if passthrough > 0:
                total_invoicing = round(passthrough + agency_revenue, 2)

    updates: dict[str, Any] = {
        "line_items": line_items,
        "line_item_sum": line_sum,
        "agency_fee_subtotal": agency_fee,
        "client_media_passthrough": passthrough if passthrough > 0 else None,
        "total_client_invoicing": total_invoicing if commission_style and passthrough > 0 else None,
        "agency_revenue_estimate": agency_revenue,
    }

    computed = agency_revenue
    requires_lump_hourly = rfp_requires_lump_sum_and_hourly(rfp_sections, rfp_context)
    lump = budget.lump_sum_total
    if requires_lump_hourly and computed > 0:
        if lump is None or abs(float(lump) - computed) > max(1.0, computed * 0.01):
            updates["lump_sum_total"] = computed
            updates["fee_structure"] = _append_fee_structure_note(
                budget.fee_structure,
                (
                    f"Lump sum ({_usd(computed)}) equals agency fee line items"
                    f"{f' plus direct expenses ({_usd(direct)})' if direct > 0 else ''}; "
                    "client pass-through media (if any) is invoiced separately at net."
                ),
            )
    elif lump is not None and computed > 0 and abs(float(lump) - computed) > max(1.0, computed * 0.01):
        updates["lump_sum_total"] = computed

    if commission_style and passthrough > 0 and agency_fee > 0:
        implied_rate = round(agency_fee / passthrough, 4) if passthrough else None
        if implied_rate and budget.commission_rate is None:
            updates["commission_rate"] = implied_rate

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
    line_sum = sum_line_items_extended(budget)
    direct = round(float(budget.direct_expenses_total or 0), 2)
    _, agency_fee, passthrough = split_line_item_totals(budget.line_items)
    expected_agency = round(agency_fee + direct, 2)

    revenue = budget.agency_revenue_estimate
    if line_sum > 0:
        if revenue is None:
            violations.append("agencyRevenueEstimate is missing")
        elif abs(float(revenue) - expected_agency) > 0.01:
            violations.append(
                f"agencyRevenueEstimate ({revenue}) != agency fee subtotal ({agency_fee}) + direct ({direct})"
            )

    if budget.line_item_sum is not None and abs(float(budget.line_item_sum) - line_sum) > 0.01:
        violations.append(f"lineItemSum ({budget.line_item_sum}) != sum of line items ({line_sum})")

    if passthrough > 0 and revenue is not None and abs(float(revenue) - line_sum - direct) < 1.0:
        violations.append(
            "agencyRevenueEstimate includes client pass-through — must be agency fee only"
        )

    lump = budget.lump_sum_total
    if lump is not None and expected_agency > 0 and abs(float(lump) - expected_agency) > max(
        1.0, expected_agency * 0.01
    ):
        violations.append(f"lumpSumTotal ({lump}) != verified agency revenue ({expected_agency})")

    blob_parts = [
        budget.fee_structure,
        budget.qualifying_language,
        budget.option_term_notes,
        " ".join(budget.pricing_flags),
    ]
    blob = "\n".join(part for part in blob_parts if part)
    if _VERIFY_BEFORE_SUBMIT_RE.search(blob):
        violations.append("budget object still contains verify-before-submission language")

    for flag in budget.pricing_flags:
        if _STALE_RECONCILIATION_FLAG_RE.search(flag):
            violations.append(f"stale reconciliation flag remains: {flag[:100]}")
        elif _PRICING_FLAG_ADVISORY_RE.search(flag):
            # Advisory — surfaced in budget panel / manual flags; Sonja resolves pre-submit.
            continue
        elif flag.strip():
            violations.append(f"unresolved budget flag: {flag[:120]}")

    violations.extend(collect_pm_ratio_violations(budget))

    return violations


def validate_budget_canonical(budget: ProposalBudget) -> list[str]:
    """Post-reconcile validation — returns errors; pipeline must halt if non-empty."""
    errors: list[str] = []
    errors.extend(collect_budget_invariant_violations(budget))

    line_sum = sum_line_items_extended(budget)
    stored_sum = budget.line_item_sum
    if stored_sum is not None and abs(float(stored_sum) - line_sum) > 0.01:
        errors.append(f"lineItemSum ({stored_sum}) != actual line-item sum ({line_sum})")

    direct = round(float(budget.direct_expenses_total or 0), 2)
    _, agency_fee, passthrough = split_line_item_totals(budget.line_items)
    expected_agency = round(agency_fee + direct, 2)

    revenue = budget.agency_revenue_estimate
    if revenue is not None and passthrough > 0:
        if abs(float(revenue) - line_sum - direct) < 1.0 and abs(float(revenue) - expected_agency) > 1.0:
            errors.append(
                f"agencyRevenueEstimate ({revenue}) conflates pass-through media with agency fee — "
                f"must be agency fee subtotal ({agency_fee}) + direct ({direct}) = {expected_agency}, "
                f"not total line items ({line_sum}) + direct"
            )

    if passthrough > 0 and budget.total_client_invoicing is not None:
        expected_invoicing = round(line_sum + direct, 2)
        if abs(float(budget.total_client_invoicing) - expected_invoicing) > 1.0:
            errors.append(
                f"totalClientInvoicing ({budget.total_client_invoicing}) != "
                f"line items ({line_sum}) + direct ({direct})"
            )

    rendered = render_budget_markdown_for_validation(budget)
    if _VERIFY_BEFORE_SUBMIT_RE.search(rendered):
        errors.append("rendered budget markdown still contains verify-before-submission language")

    return errors


def assert_budget_canonical(budget: ProposalBudget) -> None:
    """Raise ValueError when canonical budget validation fails."""
    errors = validate_budget_canonical(budget)
    if errors:
        raise ValueError(
            f"BUDGET VALIDATION FAILED ({len(errors)} error(s)): " + "; ".join(errors)
        )


def assert_budget_invariants(budget: ProposalBudget) -> None:
    """Raise ValueError when budget fails post-reconcile invariants."""
    assert_budget_canonical(budget)


def parse_budget_extras(raw: dict[str, Any]) -> dict[str, Any]:
    """Extract optional lump-sum / direct-expense / canonical fields from LLM JSON."""
    extras: dict[str, Any] = {}
    for key, alias in (
        ("lumpSumTotal", "lump_sum_total"),
        ("directExpensesTotal", "direct_expenses_total"),
        ("lineItemSum", "line_item_sum"),
        ("agencyFeeSubtotal", "agency_fee_subtotal"),
        ("clientMediaPassthrough", "client_media_passthrough"),
        ("totalClientInvoicing", "total_client_invoicing"),
        ("commissionRate", "commission_rate"),
    ):
        val = raw.get(key) if key in raw else raw.get(alias)
        if isinstance(val, (int, float)) and float(val) >= 0:
            extras[alias] = float(val)
    return extras
