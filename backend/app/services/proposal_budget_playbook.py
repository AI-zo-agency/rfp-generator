"""Canonical pricing/budget playbook for Stage 3 and chat edits (option C enforcement)."""

from __future__ import annotations

import re

from app.models.proposal import ProposalBudget, ProposalResearchCache, ProposalSection
from app.models.rfp import RfpRecord
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
   - RFP asks hourly → work/labor-category rates from 00_Guide_Pricing only. Never invent named ZO person $/hr (not in KB).
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
    r"budget|pricing|price proposal|fee schedule|cost proposal|"
    r"cost of (?:the )?base|cost section|fee table|"
    r"compensation|"
    r"commission|pass-?through|media spend|line item|tier|lump sum|hourly rate|"
    r"investment|investments|invested|invest|"
    r"agency revenue|project management|pm\b"
    r")\b",
    re.I,
)

_REVERSE_ENGINEER_ASK_RE = re.compile(
    r"(?is)"
    r"(?:"
    # Affirmative reverse-engineer ask with a nearby target — bare playbook/refusal
    # mentions ("Never reverse-engineer…", "would reverse-engineer…") are filtered
    # in user_asked_reverse_engineered_total.
    r"reverse[\s-]?engineer(?:ing)?\b.{0,80}"
    r"(?:\$\s*\d|\b(?:hit|reach|meet|fit|make|force|squeeze)\b)|"
    r"(?:hit|reach|make|get(?:\s+it)?\s+to|force|squeeze|pad|inflate)\s+"
    r"(?:(?:the|a)\s+)?(?:total|budget|ceiling|cap|sum)\b.{0,24}\$?\s*\d|"
    r"(?:total|budget|sum|ceiling|cap)\s*(?:of|to|at|=|:)?\s*\$\s*\d|"
    r"to\s+\$\s*\d{2,}|"
    r"(?:fit|reduce|lower|cut)\s+(?:(?:the|it|our)\s+)?(?:total|budget|sum)\s+to\s+\$?\s*\d|"
    r"so\s+the\s+total\s+(?:is|equals|hits|reaches|=)\s+\$?\s*\d"
    r")"
)

_LATEST_USER_MESSAGE_RE = re.compile(
    r"(?is)\nLatest user message:\s*\n(.*)\Z"
)

_REVERSE_ENGINEER_NEGATION_RE = re.compile(
    r"(?is)\b(?:do\s+not|don't|dont|never|refuse|would|not)\s+$"
)

# Completing / reconciling Cost from the guide is NOT reverse-engineering.
_SAFE_BUDGET_COMPLETE_RE = re.compile(
    r"(?is)"
    r"\b("
    r"reconcile|complete|fill|finish|rebuild|regenerate|"
    r"from\s+(?:the\s+)?(?:guide|kb|pricing\s+guide|00_guide)|"
    r"match\s+(?:the\s+)?(?:guide|canonical|pricing\s+guide|fee\s+table|line\s+items?)|"
    r"align\s+(?:with|to)\s+(?:the\s+)?(?:guide|canonical|pricing)"
    r")\b"
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


def user_asks_budget_summary_reconcile(text: str) -> bool:
    """True when the user wants narrative totals fixed from the existing fee table.

    This is surgical prose only — never Stage 3.5 / new line items.
    """
    raw = text or ""
    # Explicit full rebuild / regenerate always wins against summary-only.
    if re.search(
        r"(?i)\b("
        r"stage\s*3\.?5|pricing\s+agent|"
        r"rebuild\s+(?:the\s+)?(?:budget|pricing|cost|fee\s+table)|"
        r"regenerate\s+(?:the\s+)?(?:budget|pricing|fee|line\s+items?)|"
        r"new\s+line\s+items?"
        r")\b",
        raw,
    ):
        return False
    # Completing / rebuilding Cost of Base Proposal is Stage 3.5, not summary prose.
    if re.search(
        r"(?is)\b(cost\s+of\s+(?:the\s+)?base|cost\s+proposal)\b.{0,50}\b"
        r"(fill|complete|rebuild|regenerate)\b"
        r"|"
        r"\b(fill|complete|rebuild|regenerate)\b.{0,50}\b"
        r"(cost\s+of\s+(?:the\s+)?base|cost\s+proposal)\b",
        raw,
    ):
        return False

    summary_signals = bool(
        re.search(
            r"(?is)\b("
            r"recalculate|"
            r"summary\s+(?:paragraph|blocks?|figures?)|"
            r"distinct\s+(?:figures?|numbers?)|"
            r"(?:three|3)\s+different\s+numbers|"
            r"duplicated?\s+(?:total|figure|amount)|"
            r"identical\s+figure|"
            r"agency\s+(?:fee|revenue).{0,100}pass-?through|"
            r"pass-?through.{0,100}(?:total\s+invoic|agency)|"
            r"match\s+(?:the\s+)?(?:line[-\s]?item|fee)\s+table|"
            r"line[-\s]?item\s+table.{0,60}(?:correct|already|sums?)|"
            r"fix\s+all\s+three\s+summary|"
            r"investment\s+summary|"
            r"garbled\s+trailing|"
            r"corrupted\s+or\s+truncated"
            r")\b",
            raw,
        )
    )
    if not summary_signals:
        return False
    return bool(
        user_message_targets_budget(raw)
        or re.search(
            r"(?i)\b("
            r"agency\s+(?:fee|revenue)|pass-?through|invoicing|fee\s+table|"
            r"line[-\s]?items?"
            r")\b",
            raw,
        )
    )


def user_asks_budget_rebuild(text: str) -> bool:
    """True when the user wants Cost/budget filled or rebuilt from the Pricing Guide."""
    raw = text or ""
    if not user_message_targets_budget(raw):
        return False
    # Summary-paragraph reconcile must never look like a Stage 3.5 rebuild ask.
    if user_asks_budget_summary_reconcile(raw):
        return False
    return bool(
        re.search(
            r"(?is)\b("
            r"fill|complete|reconcile|rebuild|regenerate|finish|fix|update|"
            r"re-?run|rerun|redo"
            r")\b.{0,60}\b("
            r"budget|pricing|cost(?:\s+of)?(?:\s+base)?(?:\s+proposal)?|fee\s+table|"
            r"line\s+items?|cost\s+proposal"
            r")\b"
            r"|"
            r"\b("
            r"budget|pricing|cost(?:\s+of)?(?:\s+base)?(?:\s+proposal)?|fee\s+table|"
            r"cost\s+proposal"
            r")\b.{0,60}\b("
            r"fill|complete|reconcile|rebuild|regenerate|finish|fix|update"
            r")\b",
            raw,
        )
    )


def user_points_at_open_section(text: str) -> bool:
    """True when the ask is scoped to the open tab ('here', 'this section', 'in this')."""
    return bool(
        re.search(
            r"(?i)\b("
            r"here|this\s+section|this\s+tab|this\s+part|open\s+(?:section|tab)|"
            r"in\s+this(?:\s+(?:section|tab|part))?|for\s+this\s+(?:section|tab)|"
            r"improve\s+this\s+section"
            r")\b",
            text or "",
        )
    )


def section_has_budget_verify_tags(content: str) -> bool:
    """True when the section body has [VERIFY: …] tags about budget/fees/investment."""
    from app.services.proposal_manual_flags import VERIFY_TAG_RE

    for match in VERIFY_TAG_RE.finditer(content or ""):
        field = (match.group(1) or "").casefold()
        if any(
            k in field
            for k in (
                "budget",
                "investment",
                "fee",
                "pricing",
                "cost",
                "total",
                "phase",
            )
        ):
            return True
    return False


def user_asks_insert_budget_table(text: str) -> bool:
    """Add/insert a fee table into the open section — never a Stage 3.5 Cost rebuild."""
    raw = text or ""
    if not user_message_targets_budget(raw) and not re.search(
        r"(?i)\b(?:\[?E\d|evidence\s+marker|citations?|pricing\s+flag|bold)\b",
        raw,
    ):
        # Allow scrub/fix asks that mention E-markers without the word budget.
        if not re.search(
            r"(?i)\b(?:don'?t|do\s+not)\s+(?:give|show|include|put)\b.{0,40}\bE\d",
            raw,
        ):
            return False
    if re.search(
        r"(?is)\b("
        r"(?:implement|add|insert|put|include|embed|drop)\b.{0,48}\b"
        r"(?:budget|fee|investment|pricing)\s+table\b|"
        r"\b(?:budget|fee|investment)\s+table\b.{0,24}\b"
        r"(?:here|this\s+section|this\s+tab|this\s+part)\b|"
        r"\b(?:add|implement|insert)\b.{0,24}\bbudget\b.{0,24}\b"
        r"(?:here|table|to\s+this)\b|"
        # Fix / clean / format the embedded budget (Compliance BUDGETS block).
        r"(?:proper|accurate|clean|fix|format|correct)\b.{0,48}\b"
        r"(?:budget|fee\s+table|investment|bold)|"
        r"(?:don'?t|do\s+not)\s+(?:give|show|include|put)\b.{0,40}\b"
        r"(?:\[?E\d|evidence|citations?|pricing\s+flag)|"
        r"\bremove\b.{0,40}\b(?:\[?E\d|evidence\s+marker|citations?|pricing\s+flag)"
        r")",
        raw,
    ):
        return True
    return False


def user_asks_section_budget_fill(text: str) -> bool:
    """Fill budget VERIFY/gaps in the open section — not a Cost Proposal Stage 3.5 rebuild."""
    raw = text or ""
    if not user_message_targets_budget(raw):
        return False
    # "implement budget table here" is section-local insert, not Cost Proposal rebuild.
    if user_asks_insert_budget_table(raw):
        return True
    if user_points_at_open_section(raw):
        return True
    return bool(
        re.search(
            r"(?is)\b(fill|complete|resolve|clear)\b.{0,40}\b"
            r"(budget|investment)\s+(?:part|figures?|tags?|verify)|"
            r"\bbudget\s+(?:part|figures?|verify\s+tags?)\b",
            raw,
        )
    )


def user_asks_global_cost_rebuild(text: str) -> bool:
    """Rebuild the Cost of Base Proposal / fee table (Stage 3.5) — proposal-wide."""
    raw = text or ""
    if not user_message_targets_budget(raw):
        return False
    # Narrative summary reconcile keeps the existing fee table — never Stage 3.5.
    if user_asks_budget_summary_reconcile(raw):
        return False
    # "implement/add budget table here" stays on the open tab — never Stage 3.5.
    if user_asks_insert_budget_table(raw) or (
        user_asks_section_budget_fill(raw) and user_points_at_open_section(raw)
    ):
        return False
    # Explicit Cost Proposal / Stage 3.5 language always wins.
    if re.search(
        r"(?i)\b("
        r"cost\s+of\s+(?:the\s+)?base|cost\s+proposal|"
        r"stage\s*3\.?5|pricing\s+agent|rebuild\s+(?:the\s+)?(?:budget|pricing|cost)|"
        r"regenerate\s+(?:the\s+)?(?:budget|pricing|fee)"
        r")\b",
        raw,
    ):
        return True
    # "fee table" alone is ambiguous — only global when not scoped to open tab.
    if re.search(r"(?i)\bfee\s+(?:table|schedule)\b", raw):
        if user_points_at_open_section(raw):
            return False
        return True
    # "fill budget" alone on another tab is section-local, not global rebuild.
    if user_asks_section_budget_fill(raw):
        return False
    return user_asks_budget_rebuild(raw)


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
    """True only for forcing line items to hit an explicit numeric total — not guide fills."""
    text = user_message or ""
    if not text.strip():
        return False
    # When improve() composes prior turns, only judge the latest user ask — prior
    # assistant refusals/playbook text often contain "reverse-engineer".
    latest = _LATEST_USER_MESSAGE_RE.search(text)
    if latest:
        text = (latest.group(1) or "").strip()
        if not text:
            return False
    # Completing Cost from the guide / canonical object is allowed even if wording
    # includes "match" or "fit" without an explicit dollar/target figure.
    if _SAFE_BUDGET_COMPLETE_RE.search(text) and not re.search(
        r"\$\s*\d{2,}|\b\d{1,3}(?:,\d{3})+\b|\b\d{5,}\b",
        text,
    ):
        return False
    for match in _REVERSE_ENGINEER_ASK_RE.finditer(text):
        # Skip policy / refusal phrasing that mentions reverse-engineering.
        if match.group(0).lower().startswith("reverse"):
            prefix = text[max(0, match.start() - 48) : match.start()]
            if _REVERSE_ENGINEER_NEGATION_RE.search(prefix):
                continue
        return True
    return False


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


BUDGET_TOOL_ROUTING = """=== BUDGET TOOL ROUTING (mandatory) ===
New RFP clients have NO fee/hours/rates in the company knowledge base.
1) Call search_rfp_requirements for budget ceiling, cost evaluation weight, quote/pricing form rules.
2) Call search_pricing_guide for 00_Guide_Pricing Low/Average/High tiers and approved menu rates.
3) Pick ONE tier from RFP pressure + cost scoring weight, then price from the guide only.
4) Never invent dollars; never put phone numbers in Fee columns; use [VERIFY: …] when unknown.
"""


async def build_budget_repair_context(
    *,
    rfp: RfpRecord,
    rfp_text: str,
    research: ProposalResearchCache | None,
    user_message: str = "",
) -> str:
    """RFP budget excerpt + 00_Guide_Pricing + playbook for repair/revise agents."""
    from app.services.proposal_pricing_service import fetch_pricing_guide_context
    from app.services.proposal_rfp_excerpt import budget_and_cost_excerpt

    cost_excerpt = budget_and_cost_excerpt(rfp_text, max_chars=16_000)
    stage_two = ""
    if research and research.rfp_sections:
        stage_two = "\n".join(
            f"{s.title}: {', '.join((s.requirements or [])[:5])}"
            for s in research.rfp_sections[:12]
        )
    guide_text, _ = await fetch_pricing_guide_context(
        rfp,
        stage_two=stage_two,
        focus_hint=user_message[:300] or "tier selection budget ceiling",
    )
    parts = [
        BUDGET_TOOL_ROUTING,
        budget_playbook_prompt_block(research=research, full_budget_detail=True),
    ]
    if cost_excerpt.strip():
        parts.append(f"=== RFP BUDGET / COST EXCERPT ===\n{cost_excerpt[:14_000]}")
    if guide_text.strip():
        parts.append(f"=== 00_Guide_Pricing ===\n{guide_text[:16_000]}")
    return "\n\n".join(parts)
