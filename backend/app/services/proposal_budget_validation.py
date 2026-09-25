"""Validate and reconcile Stage 3 budget math — no RFP-specific hardcoding."""

from __future__ import annotations

import logging
import re

from app.models.proposal import BudgetLineItem, BudgetLineItemType, ProposalBudget

logger = logging.getLogger(__name__)

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
# Travel and reimbursables are billed at cost — they are neither agency fee nor
# client media. Without this branch infer_line_item_type fell through to
# "agency_fee", while proposal_budget_content routed the same row to
# reimbursables, so one row was counted in both buckets.
_DIRECT_EXPENSE_LINE_RE = re.compile(
    r"\b(travel|airfare|lodging|per\s*diem|mileage|ground\s+transport|hotel|"
    r"reimbursable|out[\s-]of[\s-]pocket)\b",
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
_PRICING_FLAG_ADVISORY_RE = re.compile(
    r"PRICING\s+FLAG|"
    r"Sonja\s+review|"
    r"^\s*L\d+\s*[—–\-]|"
    r"capability\s+gap|"
    r"internal\s+benchmark|"
    r"outside\s+standard|"
    r"exceeds\s+.*ceiling|"
    r"Attachment\s+\d+",
    re.I | re.M,
)
_PM_GUIDE_FLOOR = 7500.0
# Only true campaign-specific / pilot PM may sit under the $7,500 engagement floor.
# Do NOT match "short project" alone — guide 9.1 ("short projects 3–6 months") is $7,500–$12,000.
_PM_CAMPAIGN_SPECIFIC_RE = re.compile(
    r"campaign-specific|\b9\.2\b|\bpilot\b",
    re.I,
)

_ONE_TIME_LINE_RE = re.compile(
    r"\b(design\s*&\s*setup|setup|development|one-?time|initial\s+setup|"
    r"newsletter\s+design\s*&\s*setup|landing\s+page\s+design)\b",
    re.I,
)
_RECURRING_LINE_RE = re.compile(
    r"\bmonthly\b|\bper\s+month\b|\brecurring\b|\bongoing\b",
    re.I,
)
_MONTH_UNIT_RE = re.compile(r"\b(month|months|mo)\b", re.I)

def _usd(value: float) -> str:
    return f"${value:,.0f}"


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
    if _DIRECT_EXPENSE_LINE_RE.search(blob):
        return "direct_expense"
    if _AGENCY_FEE_LINE_RE.search(blob):
        return "agency_fee"
    return "agency_fee"


def split_line_item_totals(
    line_items: list[BudgetLineItem],
) -> tuple[float, float, float]:
    """Return (line_item_sum, agency_fee_subtotal, client_passthrough_subtotal).

    Negative-extended lines (discounts, credits, waived-fee adjustments) are
    legitimate and must be included — dropping them here silently disagreed
    with sum_line_items_extended/_canonical_client_total/the rendered fee
    table's own subtotal (none of which filter by sign), which desynced
    agencyRevenueEstimate/lineItemSum from the client-facing total and could
    make collect_budget_invariant_violations fail against its own reconciled
    value. Zero-valued/unset lines still contribute nothing either way.
    """
    agency = 0.0
    passthrough = 0.0
    direct = 0.0
    for item in line_items:
        ext = float(item.extended or 0)
        kind = infer_line_item_type(item)
        if kind == "client_passthrough":
            passthrough += ext
        elif kind == "direct_expense":
            direct += ext
        else:
            agency += ext
    agency = round(agency, 2)
    passthrough = round(passthrough, 2)
    direct = round(direct, 2)
    return round(agency + passthrough + direct, 2), agency, passthrough


def direct_expense_subtotal(line_items: list[BudgetLineItem]) -> float:
    """Travel / reimbursables carried as line items, billed at cost."""
    total = 0.0
    for item in line_items:
        if infer_line_item_type(item) == "direct_expense":
            total += float(item.extended or 0)
    return round(total, 2)


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


def collect_line_item_math_violations(budget: ProposalBudget) -> list[str]:
    """Flag when extended does not equal rate × quantity (after rounding tolerance)."""
    violations: list[str] = []
    for item in budget.line_items:
        rate, qty, ext = item.rate, item.quantity, item.extended
        if rate is None or qty is None or ext is None:
            continue
        if float(qty) <= 0:
            continue
        expected = round(float(rate) * float(qty), 2)
        if abs(float(ext) - expected) > 0.02:
            violations.append(
                f"{item.id}: extended ({ext}) != rate×qty ({expected}) for {item.description[:80]}"
            )
    return violations


def collect_one_time_recurring_violations(budget: ProposalBudget) -> list[str]:
    """Flag one-time guide lines priced as ×12 months (SRIA-style error)."""
    violations: list[str] = []
    for item in budget.line_items:
        desc = item.description or ""
        if _RECURRING_LINE_RE.search(desc):
            continue
        if not _ONE_TIME_LINE_RE.search(desc):
            continue
        qty = item.quantity
        unit = (item.unit or "").strip()
        if qty is not None and float(qty) >= 12:
            violations.append(
                f"{item.id}: one-time/setup line multiplied by {qty} — use a monthly guide line or flag scope"
            )
        elif _MONTH_UNIT_RE.search(unit) and qty is not None and float(qty) > 1:
            violations.append(
                f"{item.id}: one-time/setup line billed across {qty} {unit} — likely misapplied recurring math"
            )
    return violations


_COMMISSION_LINE_RE = re.compile(r"\bcommission\b", re.I)
_COMMISSION_PCT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_MONEY_RE = re.compile(r"\$\s*([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{2})?|[0-9]+(?:\.[0-9]{2})?)")


def collect_orphan_commission_violations(budget: ProposalBudget) -> list[str]:
    """Derived commission fee cannot exist without a media/pass-through base (T5.3)."""
    violations: list[str] = []
    _, _, passthrough = split_line_item_totals(budget.line_items)
    stored_pt = float(budget.client_media_passthrough or 0)
    base = max(passthrough, stored_pt)
    for item in budget.line_items:
        desc = item.description or ""
        if not _COMMISSION_LINE_RE.search(desc):
            continue
        if infer_line_item_type(item) == "client_passthrough":
            continue
        ext = float(item.extended or 0)
        if ext <= 0:
            continue
        if base <= 0.01:
            violations.append(
                f"{item.id}: orphan commission ${ext:,.2f} with no client media "
                f"pass-through / media base line — add the buy base or drop the derived fee"
            )
    return violations


def collect_commission_fee_math_violations(budget: ProposalBudget) -> list[str]:
    """A drafted commission-fee line's dollar amount must equal rate x media base.

    reconcile_proposal_budget trusts the line item's own `extended` as ground
    truth and only falls back to rate x pass-through when the line is missing
    or zero (derive_commission_agency_revenue) — so a populated commission
    line with the wrong arithmetic (wrong base, stale rate, plain error) is
    never caught. This checks only lines whose own description says
    "commission" (matching collect_orphan_commission_violations's
    identification), never the whole agency_fee_subtotal, so a mixed fee
    structure (flat retainer + commission line) does not false-positive.
    """
    violations: list[str] = []
    rate = budget.commission_rate
    if rate is None:
        return violations
    r = float(rate)
    if r > 1:
        r = r / 100.0
    if r <= 0:
        return violations

    _, _, passthrough = split_line_item_totals(budget.line_items)
    stored_pt = float(budget.client_media_passthrough or 0)
    base = max(passthrough, stored_pt)
    if base <= 0.01:
        return violations

    expected = round(base * r, 2)
    tolerance = max(5.0, expected * 0.03)
    for item in budget.line_items:
        desc = item.description or ""
        if not _COMMISSION_LINE_RE.search(desc):
            continue
        if infer_line_item_type(item) == "client_passthrough":
            continue
        ext = float(item.extended or 0)
        if ext <= 0:
            continue
        if abs(ext - expected) > tolerance:
            violations.append(
                f"{item.id}: commission fee ${ext:,.2f} != commissionRate ({rate}) x "
                f"media base (${base:,.2f}) = ${expected:,.2f} — check rate/base, not invented"
            )
    return violations


def find_orphan_commission_in_manuscript(text: str) -> list[str]:
    """Narrative commission $ with % but missing implied base amount in the manuscript."""
    blob = text or ""
    if not _COMMISSION_LINE_RE.search(blob):
        return []
    findings: list[str] = []
    # Sentence-ish windows containing "commission" and a dollar amount + optional %.
    for match in re.finditer(
        r"(?i)[^.!\n]{0,120}commission[^.!\n]{0,160}",
        blob,
    ):
        window = match.group(0)
        money = _MONEY_RE.findall(window)
        if not money:
            continue
        pct_m = _COMMISSION_PCT_RE.search(window)
        if not pct_m:
            continue
        pct = float(pct_m.group(1))
        if pct <= 0 or pct > 100:
            continue
        # Prefer the commission dollar (usually smaller than a media base).
        amounts = [float(m.replace(",", "")) for m in money]
        commission = min(amounts)
        rate = pct / 100.0
        expected_base = round(commission / rate, 2)
        # Accept formatting variants of the base in the full manuscript.
        base_needles = {
            f"{expected_base:,.2f}",
            f"{expected_base:.2f}",
            f"{expected_base:,.0f}",
            f"{expected_base:.0f}",
        }
        if any(n in blob.replace(" ", "") or n in blob for n in base_needles):
            # Also require a $-prefixed or plain occurrence
            if any(
                f"${n}" in blob or n in blob
                for n in base_needles
            ):
                continue
        # Stronger check: normalized digits only
        digits_blob = re.sub(r"[^\d]", "", blob)
        digits_base = re.sub(r"[^\d]", "", f"{expected_base:.2f}")
        digits_base_int = re.sub(r"[^\d]", "", f"{expected_base:.0f}")
        if digits_base in digits_blob or digits_base_int in digits_blob:
            continue
        findings.append(
            f"Orphan commission ${commission:,.2f} at {pct:g}% implies media base "
            f"${expected_base:,.2f}, which does not appear in the manuscript"
        )
    return findings


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


def _pm_needs_engagement_floor(item: BudgetLineItem) -> bool:
    """Full-engagement PM lines must meet guide dollar floor; campaign-specific rows may be smaller."""
    blob = " ".join(
        part
        for part in (
            item.description,
            item.role_title or "",
            item.rate_source or "",
            item.notes or "",
            item.category or "",
        )
        if part
    )
    if _PM_CAMPAIGN_SPECIFIC_RE.search(blob):
        return False
    # Guide 9.2 is campaign-specific PM ($5k–$8.5k Average) — below full-engagement floor is OK.
    if re.search(r"\b9\.2\b", blob):
        return False
    return True


def collect_pm_floor_violations(budget: ProposalBudget) -> list[str]:
    violations: list[str] = []
    for item in budget.line_items:
        if not _is_pm_line_item(item) or not _pm_needs_engagement_floor(item):
            continue
        ext = float(item.extended or 0)
        if ext <= 0:
            continue
        if ext < _PM_GUIDE_FLOOR:
            violations.append(
                f"{item.id}: project management at {_usd(ext)} is below 00_Guide_Pricing "
                f"engagement floor (~{_usd(_PM_GUIDE_FLOOR)}–$12,000 Average tier)"
            )
    return violations


def collect_budget_invariant_violations(budget: ProposalBudget) -> list[str]:
    """Return human-readable violations when budget math or flags are unreconciled."""
    violations: list[str] = []
    line_sum = sum_line_items_extended(budget)
    # agency_fee excludes direct_expense (travel) line items — reconcile_proposal_budget's
    # agency_revenue_estimate is agency_fee + ALL direct spend (in-line travel plus the
    # explicit directExpensesTotal residual), so `direct` here must match that or a
    # legitimately-reconciled travel line would trip a false invariant violation.
    direct = round(
        direct_expense_subtotal(budget.line_items) + float(budget.direct_expenses_total or 0), 2
    )
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
        # All other pricing_flags are human-review notes (Sonja, line L01–Lnn, capability gaps).
        # They surface in the budget panel and pre-submit — they must not halt the pipeline.
        elif flag.strip() and not _PRICING_FLAG_ADVISORY_RE.search(flag):
            # Unknown flag format: still advisory unless it looks like a broken reconcile artifact.
            if re.search(r"reconcil|must equal|!=\s*sum", flag, re.I):
                violations.append(f"unresolved budget flag: {flag[:120]}")

    # PM ratio vs. guide is intentionally NOT a hard invariant — see the
    # advisory [PRICING FLAG: ...] this same check appends inside
    # reconcile_proposal_budget. It is a policy/guide tension (absolute-dollar
    # PM floor vs. percentage-of-fee ceiling), not an arithmetic fact, and can
    # be legitimately unresolvable on a small-fee RFP no retry fixes.
    #
    # Same for one-time×months and residual PM-floor dollars: surface as
    # pricing flags for Sonja, do not halt Phase 3.5.
    violations.extend(collect_line_item_math_violations(budget))
    violations.extend(collect_orphan_commission_violations(budget))
    violations.extend(collect_commission_fee_math_violations(budget))

    return violations


def validate_budget_canonical(budget: ProposalBudget) -> list[str]:
    """Post-reconcile validation — returns errors; pipeline must halt if non-empty."""
    errors: list[str] = []
    errors.extend(collect_budget_invariant_violations(budget))

    line_sum = sum_line_items_extended(budget)
    stored_sum = budget.line_item_sum
    if stored_sum is not None and abs(float(stored_sum) - line_sum) > 0.01:
        errors.append(f"lineItemSum ({stored_sum}) != actual line-item sum ({line_sum})")

    # explicit_direct pairs with line_sum (sum_line_items_extended / split_line_item_totals'
    # line_sum both already include in-line travel — adding it again would double-count).
    # `direct` (explicit + in-line travel) pairs with agency_fee, which now excludes travel.
    explicit_direct = round(float(budget.direct_expenses_total or 0), 2)
    _, agency_fee, passthrough = split_line_item_totals(budget.line_items)
    direct = round(direct_expense_subtotal(budget.line_items) + explicit_direct, 2)
    expected_agency = round(agency_fee + direct, 2)

    revenue = budget.agency_revenue_estimate
    if revenue is not None and passthrough > 0:
        if abs(float(revenue) - line_sum - explicit_direct) < 1.0 and abs(float(revenue) - expected_agency) > 1.0:
            errors.append(
                f"agencyRevenueEstimate ({revenue}) conflates pass-through media with agency fee — "
                f"must be agency fee subtotal ({agency_fee}) + direct ({direct}) = {expected_agency}, "
                f"not total line items ({line_sum}) + direct"
            )

    if passthrough > 0 and budget.total_client_invoicing is not None:
        expected_invoicing = round(line_sum + explicit_direct, 2)
        if abs(float(budget.total_client_invoicing) - expected_invoicing) > 1.0:
            errors.append(
                f"totalClientInvoicing ({budget.total_client_invoicing}) != "
                f"line items ({line_sum}) + direct ({explicit_direct})"
            )

    rendered = render_budget_markdown_for_validation(budget)
    if _VERIFY_BEFORE_SUBMIT_RE.search(rendered):
        errors.append("rendered budget markdown still contains verify-before-submission language")

    return errors


