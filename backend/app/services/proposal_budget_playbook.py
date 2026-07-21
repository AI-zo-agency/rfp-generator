"""Canonical pricing/budget playbook for Stage 3 and chat edits (option C enforcement)."""

from __future__ import annotations

import re

from app.models.proposal import ProposalBudget, ProposalResearchCache, ProposalSection
from app.services.proposal_budget_content import budget_section_score

BUDGET_EXPLAIN_ADVISORY_RULES = """=== BUDGET EXPLAIN MODE (mandatory when user asks totals / why / validity) ===
- Ground every rate and line-item claim in the 00_Guide_Pricing (KB) excerpts provided — cite menu ids (e.g. 4.4, 9.1) when discussing a line.
- Use the CANONICAL BUDGET OBJECT and pricingFlags as source of truth for totals — never claim "clean" or "handled correctly" if flags or automated checks contradict you.
- If pricingFlags mention auto-scaled PM, that IS reverse-engineering per playbook — say so plainly; do not claim "no reverse-engineering."
- Email Newsletter Design & Setup (guide 4.4) is a one-time deliverable unless KB shows an explicit monthly email-management line — never defend qty×12 on setup.
- PM for full engagements must meet 00_Guide_Pricing dollar floor (~$7,500–$12,000 Average) AND 5–8% band — do not claim PM "passes" when extended is below floor.
- Separate valid reasoning (model, tier, pass-through, qualifying language) from invalid lines — list both honestly.
- If KB excerpts are missing, say pricing guide was not retrieved — do not invent guide ranges."""

BUDGET_PLAYBOOK_CANONICAL = """=== ZÖ PRICING PLAYBOOK (mandatory for budget/fee work) ===

1. Pricing model first — before line items
   - No fee method / innovation invited (e.g. SRIA) → service-menu from Pricing Guide, not default hourly.
   - RFP asks hourly → approved hourly rate card only, never service-menu rates on personnel rows.
   - Media placement → pass-through immediately, not agency revenue.
   - Phased RFP → phase subtotals (discovery → strategy → execution), not a flat annual menu only.

2. Pick Low / Average / High deliberately (guide criteria)
   - Low: cost ≥25% of score, tight budget, commoditized scope, crowded field.
   - Average: default municipal RFP, moderate budget, good sector match.
   - High: creativity/expertise weighted, large/complex client, premium positioning.
   - State tier + one-sentence rationale before building the table.

3. One-time vs recurring — before quantity × rate
   - Design & Setup, Development, Package → usually one-time (qty 1).
   - Monthly Management / Monthly Content Package → recurring; use the guide's monthly line.
   - Never multiply a one-time guide line by 12 to fake recurring — flag missing guide coverage instead.

4. Agency revenue vs client pass-through
   - Client media/ad budget is client money at net; commission (e.g. 85/15) is agency revenue.
   - Keep pass-through separate so agency fee subtotal is not inflated by media that was never zö's fee.

5. Project management sanity check
   - PM target 5–8% of total project investment; floor ~$7,500–$12,000 for real engagements.
   - If PM is squeezed to hit a total, the total/scope/tier is wrong — do not quietly cut PM to fit.

6. Never reverse-engineer a line to hit a total
   - Every line traces independently to a guide range. If sum vs RFP ceiling is off, change tier or scope.

7. Qualifying language on every budget page
   - Investment framing, scope protection, reimbursables, revision rounds — use pre-approved guide wording.

8. Flag, don't fill, out-of-guide scope
   - [PRICING FLAG: description — outside approved parameters, Sonja review required]

9. Stress-test before submission
   - At/under RFP ceiling; 50% wages / 30% G&A / 20% profit; 15–20% room to scope up after award.
"""

OPTION_C_CHAT_POLICY = """=== OPTION C — CHAT / REVISE ENFORCEMENT ===
- REFUSE: invented dollar amounts with no guide/KB source; reverse-engineered line rates to hit a user-requested total; $0 agency revenue when commission/fees apply; one-time setup lines priced as ×12 months without a monthly guide line.
- FLAG ONLY: scope genuinely outside 00_Guide_Pricing — use [PRICING FLAG: … — Sonja review required], do not guess.
- Otherwise apply safe playbook edits and explain tradeoffs in the assistant reply when you push back.
"""

_BUDGET_TOPIC_RE = re.compile(
    r"\b("
    r"budget|pricing|price proposal|fee schedule|cost proposal|compensation|"
    r"commission|pass-?through|media spend|line item|tier|lump sum|hourly rate|"
    r"agency revenue|project management|pm\b"
    r")\b",
    re.I,
)

_REVERSE_ENGINEER_ASK_RE = re.compile(
    r"\b("
    r"hit|reach|match|make it|get (?:it )?to|fit|reduce to|lower to|total of|"
    r"back into|reverse.?engineer|so the total|to \$\d"
    r")\b",
    re.I,
)

_ZERO_AGENCY_PROSE_RE = re.compile(
    r"agency\s+(?:revenue|fee|commission).{0,60}\$0(?:\.00)?\b",
    re.I,
)


def section_is_budget_related(section: ProposalSection) -> bool:
    return budget_section_score(section.title or "") > 0


def user_message_targets_budget(text: str) -> bool:
    return bool(_BUDGET_TOPIC_RE.search(text or ""))


def should_apply_budget_playbook(section: ProposalSection, user_message: str) -> bool:
    return section_is_budget_related(section) or user_message_targets_budget(user_message)


_BUDGET_EXPLAIN_RE = re.compile(
    r"\b(explain|why|reason|valid|justify|walk me through|total|how much|is this right)\b",
    re.I,
)


def user_asks_budget_explanation(text: str) -> bool:
    return bool(_BUDGET_EXPLAIN_RE.search(text or "")) and user_message_targets_budget(text)


def format_canonical_budget_for_chat(budget: ProposalBudget) -> str:
    """Structured budget summary for chat — full line list + flags + checks."""
    from app.services.proposal_budget_validation import (
        collect_one_time_recurring_violations,
        collect_pm_floor_violations,
        collect_pm_ratio_violations,
    )

    lines: list[str] = [
        f"pricingTier: {budget.pricing_tier or '(unset)'}",
        f"budgetFormat: {budget.budget_format or '(unset)'}",
        f"agencyRevenueEstimate: {budget.agency_revenue_estimate}",
        f"agencyFeeSubtotal: {budget.agency_fee_subtotal}",
        f"clientMediaPassthrough: {budget.client_media_passthrough}",
        f"directExpensesTotal: {budget.direct_expenses_total}",
        f"totalClientInvoicing: {budget.total_client_invoicing}",
        f"lineItemSum: {budget.line_item_sum}",
        f"commissionRate: {budget.commission_rate}",
        "",
        "lineItems:",
    ]
    for item in budget.line_items:
        lines.append(
            f"  - {item.id}: {item.description[:100]} | qty={item.quantity} unit={item.unit} "
            f"rate={item.rate} extended={item.extended} type={item.line_item_type}"
        )
    flags = [f for f in (budget.pricing_flags or []) if str(f).strip()]
    if flags:
        lines.append("\npricingFlags (must acknowledge in reply):")
        for flag in flags:
            lines.append(f"  - {flag}")
    checks: list[str] = []
    checks.extend(collect_one_time_recurring_violations(budget))
    checks.extend(collect_pm_floor_violations(budget))
    checks.extend(collect_pm_ratio_violations(budget))
    if checks:
        lines.append("\nautomatedPlaybookChecks (must NOT contradict):")
        for check in checks:
            lines.append(f"  - {check}")
    return "\n".join(lines)


def budget_playbook_prompt_block(
    *,
    research: ProposalResearchCache | None = None,
    max_canonical_chars: int = 4000,
    full_budget_detail: bool = False,
) -> str:
    parts = [BUDGET_PLAYBOOK_CANONICAL.strip(), OPTION_C_CHAT_POLICY.strip()]
    if research and research.budget:
        if full_budget_detail:
            parts.append(
                "=== CANONICAL BUDGET OBJECT (source of truth) ===\n"
                + format_canonical_budget_for_chat(research.budget)
            )
        else:
            from app.services.proposal_budget_validation import render_budget_markdown_for_validation

            canonical = render_budget_markdown_for_validation(research.budget)
            if canonical.strip():
                snippet = canonical[:max_canonical_chars]
                if len(canonical) > max_canonical_chars:
                    snippet += "\n…(canonical budget truncated)"
                parts.append(
                    "=== CANONICAL BUDGET OBJECT (numbers in narrative must match) ===\n"
                    + snippet
                )
    return "\n\n".join(parts)


def user_asked_reverse_engineered_total(user_message: str) -> bool:
    text = user_message or ""
    if not _REVERSE_ENGINEER_ASK_RE.search(text):
        return False
    return bool(re.search(r"\b(total|budget|ceiling|cap|\$|\d{2,})\b", text, re.I))


def refuse_noncompliant_budget_edit(user_message: str, new_text: str) -> str | None:
    """Return a user-facing refusal when option C blocks the edit."""
    if user_asked_reverse_engineered_total(user_message):
        return (
            "That request would reverse-engineer line items to hit a target total. "
            "Per the pricing playbook, each line must trace to the Pricing Guide — "
            "adjust tier or scope instead, or ask Sonja to review a flagged out-of-guide item."
        )
    if _ZERO_AGENCY_PROSE_RE.search(new_text or ""):
        return (
            "Agency revenue / commission cannot be shown as $0 when the RFP uses fees or commission. "
            "Use commission rate × pass-through or the canonical budget figures, or "
            "[VERIFY: Sonja confirm commission rate and annual media estimate]."
        )
    return None
