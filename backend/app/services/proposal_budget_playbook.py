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

BUDGET_COMPLIANCE_ADVISORY_RULES = """=== BUDGET / COST RFP COMPLIANCE (Check RFP / meet the RFP / gaps) ===
Hard rules — never conflate separate RFP asks:

1. FEE METHOD ≠ RATE SCHEDULE. Language allowing retainer, hourly, hybrid, phased,
   or NTE fees answers HOW the buyer may be billed. It does NOT waive a separate
   ask for a complete hourly rate schedule by classification / labor category /
   role, option-year rates, or stated assumptions (travel, materials, software
   licenses, stock media, subconsultant markup). Treat those as independent
   mandatory deliverables when the RFP states them.

2. CITE EXACT WORDING. Quote the RFP clause. Never invent section titles (e.g. do
   not rename Term/Budget language as "Compensation" unless that word appears).
   Prefer the BUDGET / COST instrument excerpt and HARD FLAGS over memory.

3. PHASED / FIXED-FEE ONLY IS NOT ENOUGH when an hourly classification schedule
   is also required — flag that as a high-priority responsiveness gap, same tier
   as a missing cost table format.

4. RATES FROM KB ONLY. Do not invent $/hr. If the draft lacks a required rate
   schedule, search the packed KB pricing + labor/role billable excerpts (cite
   source filenames) — never fabricate classifications or dollars, and never use
   Internal Rate / Raw floor columns for the client schedule.

5. When HARD FLAGS list a mandatory hourly schedule / assumptions ask, open with
   that gap — do not lead with "fee method is flexible so rates are optional."
"""

BUDGET_PLAYBOOK_CANONICAL = """=== ZÖ PRICING PLAYBOOK (mandatory for budget/fee work) ===

1. Pricing model first — before line items
   - No fee method / innovation invited (e.g. SRIA) → service-menu from Pricing Guide, not default hourly.
   - RFP asks hourly / rate schedule by classification → billable $/hr from KB
     labor/role rate excerpts (and 00_Guide_Pricing labor rows when present).
     Never invent named ZO person $/hr; never use Internal/Raw floor columns.
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

BUDGET_FREEFORM_NARRATIVE_RULES = """=== BUDGET FREEFORM (Cost tab — surgical edit) ===
Obey the user's verbatim ask with the SMALLEST change. Prefer editing one table/section.

You MAY:
- add/rename/reorder columns or rows the user asked for
- clarify Scope cell wording / layout
- pull person/role names from KB roster / MasterTemplate / bios when the ask needs names
  (Name cells: verified person name only, otherwise "—" — NEVER put MANUAL FILL / VERIFY /
   "Needs your input" / "Confirm before submit" inside a rate-table cell)

You MUST NOT:
- delete or blank the Hourly Rate Schedule billable $ rows that already exist
- replace a filled Hourly Rate Schedule with MANUAL FILL / "confirm before submit" prose
- invent person names (no Jax / fake roster) — unknown → "—"
- invent new dollar amounts, rates, hourly figures, or line items
- change any Fee / Amount / Total cell away from the CANONICAL BUDGET OBJECT
- rewrite Fee Detail by Phase when the user only asked about the Hourly Rate Schedule
- add Investment Framing Component|Share|Amount mix tables when Fee Detail by Phase exists
- reverse-engineer fees to hit a target total
- paraphrase Investment Framing, Scope Protection, Reimbursable Expenses, or Revision Rounds
  — those four blocks are Pricing Guide USE VERBATIM (post-process restores them)

Prefer ONE fee breakdown: **Fee Detail by Phase** from the ledger.
Preserve Proposed Investment totals exactly as in the canonical object.
"""

_BUDGET_TOPIC_RE = re.compile(
    r"\b("
    r"budget|pricing|price proposal|fee schedule|cost proposal|"
    r"cost of (?:the )?base|cost section|fee table|"
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
    # Intentional "change/set … to $X" — NOT "sums to $210k" / "equals $X" math prose.
    r"(?:change|set|adjust|bring|bump|raise|drop)\s+"
    r"(?:(?:it|them|(?:the\s+)?(?:total|budget|sum|fees?|price))\s+)?"
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


def section_is_budget_related(section: ProposalSection) -> bool:
    """True for real fee / Cost Proposal tabs — not insurance 'compensation' forms."""
    from app.services.proposal_outline_dedup import is_pricing_outline_title

    title = section.title or ""
    score = budget_section_score(title)
    if score <= 0:
        return False
    # Weak incidental hits (score 1–3) without a pricing title are not fee tabs.
    if score < 4 and not is_pricing_outline_title(title):
        return False
    return True


def user_message_targets_budget(text: str) -> bool:
    """Legacy helper for summary/explain helpers — not the Improve playbook gate."""
    return bool(_BUDGET_TOPIC_RE.search(text or ""))


def should_apply_budget_playbook(section: ProposalSection, user_message: str = "") -> bool:
    """Fee ledger collapse / sync only when the OPEN tab is a real Cost/Pricing section.

    Do NOT keyword-scan the chat message. The section planner / Improve pin owns
    understanding — if the user wants fees while parked on Workers' Comp, remap to
    the fee tab instead of running Cost Proposal collapse on the wrong form.
    """
    del user_message
    return section_is_budget_related(section)


def user_asks_budget_summary_reconcile(text: str) -> bool:
    """True when the user wants narrative totals fixed from the existing fee table.

    This is surgical prose only — never Stage 3.5 / new line items.
    Prefer section-tab ledger sync over growing keyword lists — chat Improve on
    budget tabs runs reconcile from the canonical object without this gate.
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
            r"re-?run|rerun|redo|add|paint|write|seed|generate|create|put"
            r")\b.{0,60}\b("
            r"budget|pricing|cost(?:\s+of)?(?:\s+base)?(?:\s+proposal)?|fee\s+table|"
            r"line\s+items?|cost\s+proposal"
            r")\b"
            r"|"
            r"\b("
            r"budget|pricing|cost(?:\s+of)?(?:\s+base)?(?:\s+proposal)?|fee\s+table|"
            r"cost\s+proposal"
            r")\b.{0,60}\b("
            r"fill|complete|reconcile|rebuild|regenerate|finish|fix|update|"
            r"add|paint|write|seed|generate|create"
            r")\b"
            r"|"
            # Bare “add budget here” / “budget please” on the Cost tab.
            r"^\s*(?:please\s+)?(?:add|put|paint|write|seed)\s+"
            r"(?:the\s+)?(?:budget|cost(?:\s+proposal)?|pricing|fee\s+table)"
            r"(?:\s+here)?(?:\s+for\s+this\s+rfp)?\s*$",
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
    parts = [
        BUDGET_PLAYBOOK_CANONICAL.strip(),
        OPTION_C_CHAT_POLICY.strip(),
        BUDGET_FREEFORM_NARRATIVE_RULES.strip(),
    ]
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
    # Label sync (Professional fees vs travel, summary vs phase table) is not
    # reverse-engineering line items.
    if user_asks_budget_summary_reconcile(text):
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


def user_asks_hourly_rate_schedule_edit(text: str) -> bool:
    """True when the user wants a classification / role hourly rate table added."""
    raw = (text or "").casefold()
    if not raw.strip():
        return False
    return bool(
        re.search(
            r"(?is)\b("
            r"hourly\s+rate\s+(?:table|schedule|card)|"
            r"rate\s+schedule|"
            r"classification.{0,40}(?:rate|hourly)|"
            r"(?:add|include|insert|put).{0,40}hourly|"
            r"billable\s+rate|"
            r"labor\s+(?:categor(?:y|ies)|rate)|"
            r"role\s+rates?"
            r")\b",
            raw,
        )
    )


def user_asks_budget_fee_structure_mutation(text: str) -> bool:
    """True when the ask would change fees/rates/line items (keep canonical refresh)."""
    raw = (text or "").casefold()
    if not raw.strip():
        return False
    if user_asks_hourly_rate_schedule_edit(raw):
        return True
    needles = (
        "hourly",
        "/hr",
        "per hour",
        "rate schedule",
        "classification",
        "labor categor",
        "billable rate",
        "line item",
        "line-item",
        "new phase fee",
        "add a phase",
        "add phase",
        "change the total",
        "set the total",
        "total should be",
        "total to $",
        "reprice",
        "new fees",
        "new fee",
        "pricing guide",
        "00_guide",
        "00 guide",
        "stage 3.5",
        "stage 3.5",
        "labor rate",
        "burdened rate",
        "add a line",
        "add line item",
        "revise budget",
        "revise the budget",
        "revise pricing",
        "revise fees",
    )
    return any(n in raw for n in needles)


def user_asks_budget_narrative_freeform(text: str) -> bool:
    """True when Cost-tab chat should LLM-edit prose/layout without full re-render."""
    raw = (text or "").casefold()
    if not raw.strip():
        return False
    if user_asks_budget_fee_structure_mutation(raw):
        return False
    if user_asks_budget_rebuild(raw) or user_asks_global_cost_rebuild(raw):
        return False
    needles = (
        "investment framing",
        "component | share",
        "component|share",
        "share | amount",
        "fee detail only",
        "keep fee detail",
        "only fee detail",
        "remove investment",
        "delete investment",
        "drop investment",
        "remove the table",
        "delete the table",
        "duplicate table",
        "mix table",
        "broken table",
        "clean up terms",
        "clean terms",
        "shorten terms",
        "rewrite terms",
        "use verbatim",
        "restore verbatim",
        "verbatim terms",
        "pricing guide verbatim",
        "scope cell",
        "scope column",
        "detailed breakdown",
        "more detail",
        "more detailed",
        "fix the framing",
        "fix framing",
        "reconcile the tables",
        "tables don't agree",
        "tables do not agree",
        "don't agree",
        "do not agree",
    )
    return any(n in raw for n in needles)


def user_asks_budget_improve_if_needed(text: str) -> bool:
    """True when user wants safe RFP-aligned fixes only (not a full fee rebuild)."""
    raw = (text or "").casefold()
    if not raw.strip():
        return False
    if user_asks_budget_rebuild(raw) or user_asks_global_cost_rebuild(raw):
        return False
    if user_asks_budget_fee_structure_mutation(raw):
        return False
    return bool(
        re.search(
            r"(?is)"
            r"improve.{0,48}if\s+needed|"
            r"if\s+needed.{0,48}(?:rfp|budget|cost)|"
            r"according\s+to\s+(?:the\s+)?rfp|"
            r"\bper\s+(?:the\s+)?rfp\b|"
            r"align.{0,80}(?:with\s+)?(?:the\s+)?rfp|"
            r"improve\s+(?:the\s+)?budget|"
            r"fix\s+(?:the\s+)?budget.{0,48}rfp|"
            r"needed.{0,24}(?:according|per|against).{0,16}rfp|"
            r"restore\s+verbatim|"
            r"use\s+verbatim|"
            r"verbatim\s+terms",
            raw,
        )
    )


def user_explicitly_asks_to_change_budget(text: str) -> bool:
    """True when Cost-tab chat should freeform/fee-edit — not coverage-only.

    Soft “improve / align with RFP” and voice-only asks stay on the RFP coverage
    path (demand extract + safe fixes). Freeform is for substantive Cost edits
    the user clearly wants rewritten (columns, prose, structure, etc.).
    """
    raw = (text or "").strip()
    if not raw:
        return False
    if user_asks_budget_rebuild(raw) or user_asks_global_cost_rebuild(raw):
        return True
    if user_asks_budget_fee_structure_mutation(raw):
        return True
    if user_asks_budget_summary_reconcile(raw):
        return True
    from app.services.proposal_manual_flags import (
        is_manual_fill_request,
        user_asks_submit_handoff_fill,
    )

    if is_manual_fill_request(raw) or user_asks_submit_handoff_fill(raw):
        return True
    # Coverage / soft improve — not freeform steal.
    if user_asks_budget_improve_if_needed(raw):
        return False
    if _budget_ask_is_soft_improve_only(raw):
        return False
    if _budget_ask_is_voice_only(raw):
        return False
    return budget_ask_allows_freeform_narrative(raw)


def _budget_ask_is_soft_improve_only(text: str) -> bool:
    raw = (text or "").strip()
    return bool(
        re.fullmatch(
            r"(?is)\s*(?:please\s+)?improve\s+(?:this\s+)?(?:section|budget|cost)"
            r"(?:\s+please)?\s*",
            raw,
        )
    )


def _budget_ask_is_voice_only(text: str) -> bool:
    """Voice/tone align without a fee or RFP-cost substance ask."""
    raw = (text or "").casefold()
    if not raw.strip():
        return False
    if user_asks_budget_improve_if_needed(raw):
        return False
    if user_asks_budget_fee_structure_mutation(raw):
        return False
    if user_asks_budget_rebuild(raw) or user_asks_global_cost_rebuild(raw):
        return False
    return bool(
        re.search(
            r"(?is)"
            r"\b("
            r"zo\s*voice|zö\s*voice|brand\s*voice|"
            r"in[- ]?voice|style\s*pass|"
            r"sound\s+like\s+zö|sound\s+like\s+zo"
            r")\b"
            r"|"
            r"\balign\b.{0,160}\bvoice\b"
            r"|"
            r"\bvoice\b.{0,60}\balign\b"
            r"|"
            r"^\s*(?:align\s+with\s+)?(?:zö|zo)\s+agency'?s?\s+(?:established\s+)?"
            r"voice\s*$",
            raw,
        )
    )


def budget_ask_allows_freeform_narrative(text: str) -> bool:
    """Cost-tab freeform for substantive non-fee asks — clients phrase freely.

    Do NOT grow an allowlist of layout keywords. Freeform + Supermemory is the
    default for real edits; fee rebuilds stay canonical; soft improve / align-RFP
    / voice stay on the coverage path.
    """
    raw = (text or "").strip()
    if not raw:
        return False
    if user_asks_budget_fee_structure_mutation(raw):
        return False
    if user_asks_budget_rebuild(raw) or user_asks_global_cost_rebuild(raw):
        return False
    if user_asks_budget_improve_if_needed(raw):
        return False
    if _budget_ask_is_soft_improve_only(raw):
        return False
    if _budget_ask_is_voice_only(raw):
        return False
    return True


def dollar_amount_tokens(text: str) -> set[str]:
    """Normalized $amount tokens from prose/tables (no regex)."""
    found: set[str] = set()
    raw = text or ""
    i = 0
    while i < len(raw):
        if raw[i] == "$":
            j = i + 1
            while j < len(raw) and (raw[j].isdigit() or raw[j] in ",."):
                j += 1
            token = raw[i + 1 : j].replace(",", "")
            if token and any(ch.isdigit() for ch in token):
                if "." in token:
                    try:
                        token = f"{float(token):.2f}".rstrip("0").rstrip(".")
                    except ValueError:
                        pass
                found.add(token)
            i = j
        else:
            i += 1
    return found


def ledger_dollar_tokens(budget: ProposalBudget | None) -> set[str]:
    tokens: set[str] = set()
    if budget is None:
        return tokens
    for item in budget.line_items or []:
        for val in (item.extended, item.rate, item.quantity):
            if isinstance(val, (int, float)) and float(val) > 0:
                tokens.add(f"{float(val):.2f}".rstrip("0").rstrip("."))
    for attr in (
        "lump_sum_total",
        "agency_revenue_estimate",
        "client_media_passthrough",
        "rfp_budget_cap",
    ):
        val = getattr(budget, attr, None)
        if isinstance(val, (int, float)) and float(val) > 0:
            tokens.add(f"{float(val):.2f}".rstrip("0").rstrip("."))
    return tokens


def _extract_h2_block(text: str, heading_needle: str) -> str:
    """Return an ## section whose heading contains needle (casefold), else ""."""
    needle = (heading_needle or "").casefold()
    if not needle or not (text or "").strip():
        return ""
    lines = (text or "").splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.startswith("## ") and needle in line.casefold():
            start = i
            break
    if start is None:
        return ""
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if lines[j].startswith("## "):
            end = j
            break
    return "\n".join(lines[start:end]).strip()


def _replace_h2_block(text: str, heading_needle: str, new_block: str) -> str:
    """Replace matching ## section with new_block, or append if missing."""
    needle = (heading_needle or "").casefold()
    block = (new_block or "").strip()
    if not block:
        return text or ""
    lines = (text or "").splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.startswith("## ") and needle in line.casefold():
            start = i
            break
    if start is None:
        base = (text or "").rstrip()
        return f"{base}\n\n{block}\n" if base else f"{block}\n"
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if lines[j].startswith("## "):
            end = j
            break
    out = lines[:start] + block.splitlines() + lines[end:]
    return "\n".join(out).rstrip() + "\n"


def _block_has_billable_rate_rows(block: str) -> bool:
    """True when a schedule/fee block still has markdown $ rate/fee cells."""
    if not (block or "").strip():
        return False
    if re.search(r"\|\s*\$\s*\d", block):
        return True
    # Role row with a standalone $amount cell.
    for line in block.splitlines():
        if not line.strip().startswith("|"):
            continue
        if re.search(r"\$\s*\d{2,}", line):
            return True
    return False


def restore_stripped_budget_tables(
    content: str,
    *,
    prior_text: str,
    budget: ProposalBudget | None = None,
) -> tuple[str, list[str]]:
    """Undo freeform wipes of Fee Detail / Hourly Rate Schedule data rows."""
    logs: list[str] = []
    text = content or ""
    prior = prior_text or ""
    if not prior.strip():
        return text, logs

    prior_hourly = _extract_h2_block(prior, "hourly rate schedule")
    new_hourly = _extract_h2_block(text, "hourly rate schedule")
    if prior_hourly and _block_has_billable_rate_rows(prior_hourly):
        if not _block_has_billable_rate_rows(new_hourly):
            # Prefer re-render from ledger rates when available (keeps columns clean).
            restored = prior_hourly
            if budget is not None and any(
                (vr.hourly_rate or 0) > 0 for vr in (budget.verified_rates or [])
            ):
                try:
                    from app.services.proposal_budget_content import (
                        render_kb_classification_rate_schedule_markdown,
                    )

                    painted = render_kb_classification_rate_schedule_markdown(
                        budget, rfp_text=""
                    ).strip()
                    if _block_has_billable_rate_rows(painted):
                        restored = painted
                except Exception:
                    pass
            text = _replace_h2_block(text, "hourly rate schedule", restored)
            logs.append(
                "Restored Hourly Rate Schedule billable rows stripped by freeform edit"
            )

    prior_fee = _extract_h2_block(prior, "fee detail")
    new_fee = _extract_h2_block(text, "fee detail")
    if prior_fee and _block_has_billable_rate_rows(prior_fee):
        prior_dollars = dollar_amount_tokens(prior_fee)
        new_dollars = dollar_amount_tokens(new_fee) if new_fee else set()
        lost = {a for a in prior_dollars if a not in new_dollars}
        # Only restore when material fee dollars disappeared (not a column add).
        material_lost = []
        for a in lost:
            try:
                if float(a) >= 100:
                    material_lost.append(a)
            except ValueError:
                continue
        if material_lost or not _block_has_billable_rate_rows(new_fee):
            text = _replace_h2_block(text, "fee detail", prior_fee)
            logs.append("Restored Fee Detail by Phase stripped/altered by freeform edit")

    return text, logs


def _md_row_cells(line: str) -> list[str]:
    raw = (line or "").strip()
    if not raw.startswith("|"):
        return []
    parts = raw.strip("|").split("|")
    return [p.strip() for p in parts]


def _looks_like_person_name(value: str) -> bool:
    s = (value or "").strip()
    if not s or len(s) > 48:
        return False
    if re.search(r"[\[\]|/]|manual fill|verify:|\$|\d{3,}", s, re.I):
        return False
    words = [w for w in re.split(r"\s+", s) if w]
    if not (1 <= len(words) <= 4):
        return False
    return all(re.match(r"^[A-Za-z][A-Za-z.'\-]*$", w) for w in words)


def _clean_schedule_name_cell(cell: str) -> str:
    """Name column: verified person only; junk → em dash (never MANUAL FILL in-cell)."""
    c = (cell or "").strip()
    if not c or c in {"—", "-", "–", "n/a", "N/A"}:
        return "—"
    low = c.casefold()
    if any(
        tok in low
        for tok in (
            "manual fill",
            "verify:",
            "fabricated",
            "needs your input",
            "confirm before",
            "confirm which",
            "not in kb",
            "org chart",
            "assign verified",
            "tbd",
        )
    ):
        # ZF already rejected a fabricated name — never keep the leading token.
        if "fabricated" in low or "manual fill" in low or "assign verified" in low:
            return "—"
        # Keep a leading real name if present: "Sonja Anderson [VERIFY: …]"
        lead = re.split(r"\s*/\s*|\s*\[", c, maxsplit=1)[0].strip(" /-")
        if _looks_like_person_name(lead):
            return lead
        return "—"
    if _looks_like_person_name(c):
        return c
    return "—"


def _looks_like_labor_role(value: str) -> bool:
    s = (value or "").strip()
    if not s or len(s) > 60:
        return False
    low = s.casefold()
    if any(
        tok in low
        for tok in (
            "team member on this",
            "confirm",
            "manual fill",
            "verify",
            "engagement",
            "needs your input",
        )
    ):
        # Allow short roles that happen to include none of the junk phrases above
        # except "engagement" alone can appear in real titles rarely — block the
        # known scrub artifact specifically.
        if "team member on this" in low or "needs your input" in low:
            return False
        if "confirm" in low or "manual fill" in low or "verify" in low:
            return False
    if "$" in s or s.startswith("|"):
        return False
    return True


def _rate_from_cell(cell: str) -> float | None:
    m = re.search(r"\$?\s*([\d,]+(?:\.\d+)?)", cell or "")
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def normalize_hourly_rate_schedule_table(
    content: str,
    *,
    budget: ProposalBudget | None = None,
) -> tuple[str, list[str]]:
    """Clean Cost Hourly Rate Schedule tables after freeform / ZF cell pollution."""
    block = _extract_h2_block(content, "hourly rate schedule")
    if not block.strip():
        return content, []

    lines = block.splitlines()
    table_idxs = [i for i, ln in enumerate(lines) if ln.strip().startswith("|")]
    if len(table_idxs) < 2:
        return content, []

    header_i = table_idxs[0]
    header_cells = _md_row_cells(lines[header_i])
    if not header_cells:
        return content, []
    header_cf = [h.casefold() for h in header_cells]

    def _col(*needles: str) -> int | None:
        for i, h in enumerate(header_cf):
            if any(n in h for n in needles):
                return i
        return None

    role_i = _col("role", "labor", "categor")
    name_i = _col("name", "team member", "person", "staff")
    rate_i = _col("hourly", "rate", "billable")
    y2_i = _col("year-2", "year 2", "y2")
    y3_i = _col("year-3", "year 3", "y3")
    if role_i is None or rate_i is None:
        return content, []

    # Map rate → roles from ledger for repairing scrubbed role labels.
    rate_to_roles: dict[float, list[str]] = {}
    if budget is not None:
        for vr in budget.verified_rates or []:
            role = (vr.role or "").strip()
            rate = float(vr.hourly_rate or 0)
            if role and rate > 0:
                rate_to_roles.setdefault(rate, [])
                if role not in rate_to_roles[rate]:
                    rate_to_roles[rate].append(role)

    out_rows: list[str] = [lines[header_i]]
    # Keep separator if present
    sep_i = header_i + 1
    if sep_i < len(lines) and re.match(r"^\s*\|[\s:|\-]+\|\s*$", lines[sep_i]):
        # Normalize separator to column count
        aligns = []
        for idx in range(len(header_cells)):
            if idx in {rate_i, y2_i, y3_i}:
                aligns.append("---:")
            else:
                aligns.append("---")
        out_rows.append("| " + " | ".join(aligns) + " |")
        data_start = sep_i + 1
    else:
        aligns = []
        for idx in range(len(header_cells)):
            if idx in {rate_i, y2_i, y3_i}:
                aligns.append("---:")
            else:
                aligns.append("---")
        out_rows.append("| " + " | ".join(aligns) + " |")
        data_start = header_i + 1

    changed = False
    seen_roles: set[str] = set()
    for i in range(data_start, len(lines)):
        line = lines[i]
        if not line.strip().startswith("|"):
            break
        if re.match(r"^\s*\|[\s:|\-]+\|\s*$", line):
            continue
        cells = _md_row_cells(line)
        if len(cells) < len(header_cells):
            cells = cells + [""] * (len(header_cells) - len(cells))
        elif len(cells) > len(header_cells):
            # Extra pipes from junk — keep first N by truncating overflow into last kept
            cells = cells[: len(header_cells)]

        role = cells[role_i] if role_i < len(cells) else ""
        rate_cell = cells[rate_i] if rate_i < len(cells) else ""
        rate_val = _rate_from_cell(rate_cell)
        if not _looks_like_labor_role(role):
            repaired = None
            if rate_val is not None:
                for candidate in rate_to_roles.get(rate_val, []):
                    if candidate.casefold() not in seen_roles:
                        repaired = candidate
                        break
            if repaired:
                role = repaired
                changed = True
            else:
                changed = True
                continue
        role_key = role.casefold()
        if role_key in seen_roles:
            changed = True
            continue
        seen_roles.add(role_key)

        new_cells = list(cells[: len(header_cells)])
        new_cells[role_i] = role
        if name_i is not None:
            cleaned = _clean_schedule_name_cell(new_cells[name_i])
            if cleaned != new_cells[name_i].strip():
                changed = True
            new_cells[name_i] = cleaned
        if rate_val is not None:
            pretty = f"${rate_val:,.0f}" if float(rate_val).is_integer() else f"${rate_val:,.2f}"
            if pretty != rate_cell.strip():
                # only normalize formatting when we have a parseable rate
                if re.search(r"\d", rate_cell):
                    new_cells[rate_i] = pretty
        for yi in (y2_i, y3_i):
            if yi is None:
                continue
            val = (new_cells[yi] or "").strip()
            if not val:
                new_cells[yi] = "—"
                changed = True
        # Escape pipes inside cells
        safe = [c.replace("|", "/") for c in new_cells]
        out_rows.append("| " + " | ".join(safe) + " |")

    # Rebuild block: prose before table + new table + prose after table
    first_table = table_idxs[0]
    last_table = first_table
    for i in range(first_table, len(lines)):
        if lines[i].strip().startswith("|"):
            last_table = i
        elif i > first_table:
            break
    new_block_lines = lines[:first_table] + out_rows + lines[last_table + 1 :]
    new_block = "\n".join(new_block_lines).strip()
    if new_block == block.strip() and not changed:
        return content, []
    text = _replace_h2_block(content, "hourly rate schedule", new_block)
    return text, ["Normalized Hourly Rate Schedule table (clean name/role cells)"]


def apply_budget_freeform_postprocess(
    content: str,
    *,
    budget: ProposalBudget | None = None,
    prior_text: str = "",
    rfp_text: str = "",
    approach_digest: str = "",
) -> tuple[str, list[str]]:
    """Scrub conflicting mix tables + sync summary labels after freeform Cost edits."""
    from app.services.proposal_budget_content import (
        ensure_pricing_guide_verbatim_in_budget_markdown,
        qualifying_language_has_pricing_guide_verbatim,
        reconcile_budget_summary_prose,
        scrub_duplicate_budget_breakdown_tables,
    )

    logs: list[str] = []
    text = content or ""
    text, mix_logs = scrub_duplicate_budget_breakdown_tables(text)
    logs.extend(mix_logs)
    text, restore_logs = restore_stripped_budget_tables(
        text, prior_text=prior_text, budget=budget
    )
    logs.extend(restore_logs)
    text, norm_logs = normalize_hourly_rate_schedule_table(text, budget=budget)
    logs.extend(norm_logs)
    if budget is not None and (budget.line_items or []):
        text, n = reconcile_budget_summary_prose(text, budget)
        if n:
            logs.append(f"Reconciled {n} budget summary figure(s) to the fee ledger")
    before_terms = text
    from app.services.proposal_budget_content import (
        manuscript_asserts_all_in_no_separate_expenses,
    )

    include_reimb = not manuscript_asserts_all_in_no_separate_expenses(text)
    text = ensure_pricing_guide_verbatim_in_budget_markdown(
        text, include_reimbursable=include_reimb
    )
    if text != before_terms or not qualifying_language_has_pricing_guide_verbatim(
        text, require_reimbursable=include_reimb
    ):
        if "investment framing" in text.casefold() or "## terms" in text.casefold():
            logs.append("Restored Pricing Guide USE VERBATIM Terms blocks")
    # RFP Cost demands (LLM) are applied async in chat/Build — not here.
    _ = (rfp_text, approach_digest)
    return text, logs


def refuse_noncompliant_budget_edit(
    user_message: str,
    new_text: str,
    *,
    prior_text: str = "",
    budget: ProposalBudget | None = None,
    section: ProposalSection | None = None,
) -> str | None:
    """Return a user-facing refusal when option C blocks the edit.

    Fee-ledger invent checks apply only on Cost/Pricing tabs. Narrative tabs
    (Executive Summary sponsorship tiers, etc.) must not be blocked by Stage 3.5.
    """
    if user_asked_reverse_engineered_total(user_message):
        return (
            "That request would reverse-engineer line items to hit a target total. "
            "Per the pricing playbook, each line must trace to the Pricing Guide — "
            "adjust tier or scope instead, or ask Sonja to review a flagged out-of-guide item."
        )
    if section is not None and not section_is_budget_related(section):
        return None
    if not (new_text or "").strip():
        return None
    prior_amts = dollar_amount_tokens(prior_text)
    new_amts = dollar_amount_tokens(new_text)
    allowed = prior_amts | ledger_dollar_tokens(budget)
    invented: set[str] = set()
    for a in new_amts:
        if a in allowed:
            continue
        try:
            af = float(a)
        except ValueError:
            continue
        if af < 100:
            continue
        if any(
            abs(af - float(b)) < 0.02
            for b in allowed
            if _is_numeric_token(b)
        ):
            continue
        invented.add(a)
    if invented and prior_text.strip():
        sample = ", ".join(f"${a}" for a in sorted(invented, key=float)[:4])
        return (
            "That edit would introduce dollar amounts that are not in the current "
            f"Cost section or the Stage 3.5 fee ledger ({sample}). "
            "Ask to rebuild Fee Detail from the ledger, or rebuild Cost from the "
            "pricing guide — chat will not invent fees."
        )
    return None


def _is_numeric_token(token: str) -> bool:
    try:
        float(token)
        return True
    except ValueError:
        return False


BUDGET_TOOL_ROUTING = """=== BUDGET TOOL ROUTING (mandatory) ===
New RFP clients have NO fee/hours/rates in the company knowledge base.
1) Call search_rfp_requirements for budget ceiling, cost evaluation weight, quote/pricing form rules.
2) Call search_pricing_guide for 00_Guide_Pricing Low/Average/High tiers and approved menu rates.
3) Pick ONE tier from RFP pressure + cost scoring weight, then price from the guide only.
4) Never invent dollars; never put phone numbers in Fee columns; use [VERIFY: …] when unknown.
"""


def user_asks_rfp_compliance(text: str) -> bool:
    """True for Check RFP / meet the RFP / gaps / compliance audits."""
    raw = text or ""
    return bool(
        re.search(
            r"(?i)\b("
            r"meet(?:s)?\s+(?:the\s+)?rfp|"
            r"check\s+(?:rfp\s+)?compliance|"
            r"rfp\s+compliance|"
            r"complian(?:ce|t)\s+(?:with\s+)?(?:the\s+)?rfp|"
            r"(?:what(?:'s| is)?|any)\s+(?:still\s+)?missing|"
            r"gap(?:s)?\s+(?:vs|against|versus)\s+(?:the\s+)?rfp|"
            r"according\s+to\s+(?:the\s+)?rfp|"
            r"responsive(?:ness)?|"
            r"non[\s-]?responsive"
            r")\b",
            raw,
        )
    )


_HOURLY_SCHEDULE_MANDATE_RE = re.compile(
    r"(?is)"
    r"("
    r"hourly\s+rate\s+schedule"
    r"|rate\s+schedule\s+by\s+classification"
    r"|complete\s+hourly\s+rate"
    r"|hourly\s+rates?\s+by\s+(?:classification|labor\s+categor(?:y|ies)|role|position)"
    r"|provide.{0,60}hourly\s+rate.{0,80}"
    r"(?:classification|labor\s+categor|option\s+years?|initial\s+term)"
    r"|fully[\s-]?burdened\s+hourly"
    r"|personnel[\s-]?loading"
    r")",
)

_COST_ASSUMPTIONS_MANDATE_RE = re.compile(
    r"(?is)"
    r"("
    r"assumptions?\s+regarding\s+travel"
    r"|travel,\s*materials?,\s*software"
    r"|software\s+licenses?,\s*stock\s+media"
    r"|subconsultant\s+markup"
    r"|stock\s+media,\s*and\s*subconsultant"
    r")",
)

_FEE_METHOD_FLEX_RE = re.compile(
    r"(?is)"
    r"("
    r"retainer,\s*hourly,\s*or\s*hybrid"
    r"|hourly,\s*or\s*hybrid"
    r"|may\s+propose\s+(?:retainer|hourly|hybrid)"
    r"|retainer\s+or\s+hourly"
    r")",
)


def rfp_mandates_hourly_rate_schedule(rfp_text: str) -> bool:
    """True when RFP requires a classification/role hourly rate schedule."""
    return bool(_HOURLY_SCHEDULE_MANDATE_RE.search(rfp_text or ""))


def rfp_mandates_cost_assumptions_disclosure(rfp_text: str) -> bool:
    """True when RFP requires stated assumptions (travel/materials/licenses/markup)."""
    return bool(_COST_ASSUMPTIONS_MANDATE_RE.search(rfp_text or ""))


def rfp_allows_flexible_fee_method(rfp_text: str) -> bool:
    """True when RFP allows retainer / hourly / hybrid billing methods."""
    return bool(_FEE_METHOD_FLEX_RE.search(rfp_text or ""))


def _quote_match_window(text: str, match: re.Match[str], *, radius: int = 180) -> str:
    start = max(0, match.start() - radius)
    end = min(len(text), match.end() + radius)
    snippet = " ".join(text[start:end].split())
    if start > 0:
        snippet = "…" + snippet
    if end < len(text):
        snippet = snippet + "…"
    return snippet[:420]


def budget_compliance_hard_flags(rfp_text: str) -> str:
    """Deterministic compliance flags for advisory chat — independent RFP asks.

    Prevents the model from using fee-method flexibility to waive a mandatory
    hourly classification schedule or assumptions disclosure.
    """
    body = rfp_text or ""
    if not body.strip():
        return ""
    lines: list[str] = [
        "=== BUDGET COMPLIANCE HARD FLAGS (mechanical — do not ignore) ==="
    ]
    hourly = _HOURLY_SCHEDULE_MANDATE_RE.search(body)
    assumptions = _COST_ASSUMPTIONS_MANDATE_RE.search(body)
    flex = _FEE_METHOD_FLEX_RE.search(body)
    if hourly:
        lines.append(
            "- MANDATORY: hourly rate schedule / rates by classification "
            "(independent deliverable)."
        )
        lines.append(f"  RFP quote: \"{_quote_match_window(body, hourly)}\"")
    if assumptions:
        lines.append(
            "- MANDATORY: state assumptions on travel / materials / software "
            "licenses / stock media / subconsultant markup when asked."
        )
        lines.append(f"  RFP quote: \"{_quote_match_window(body, assumptions)}\"")
    if flex:
        lines.append(
            "- FEE METHOD FLEXIBILITY (retainer / hourly / hybrid allowed) does "
            "NOT waive the mandatory items above — separate section, separate ask."
        )
        lines.append(f"  RFP quote: \"{_quote_match_window(body, flex)}\"")
    if len(lines) == 1:
        return ""
    return "\n".join(lines)


def draft_lacks_hourly_rate_schedule(content: str) -> bool:
    """Heuristic: open Cost tab has fees but no classification hourly table."""
    body = (content or "").casefold()
    if not body.strip():
        return True
    has_hourly_table = bool(
        re.search(
            r"(?i)(\$\s*/\s*hr|per\s+hour|hourly\s+rate|/hr\b|"
            r"labor\s+categor|classification.+\$.*hr|"
            r"\|\s*[^\n]*rate[^\n]*\|[^\n]*\$)",
            body,
        )
    )
    return not has_hourly_table


def augment_cost_section_requirements(
    requirements: list[str],
    rfp_text: str,
) -> list[str]:
    """Prepend RFP-mandated cost instruments missing from the Phase-2 map.

    Intelligence caps key_messages; chat Improve must still see hourly schedule
    and assumptions asks when the full RFP states them.
    """
    out = [r for r in requirements if str(r).strip()]
    folded = "\n".join(out).casefold()
    if rfp_mandates_hourly_rate_schedule(rfp_text) and "hourly rate schedule" not in folded:
        if "by classification" not in folded and "labor categor" not in folded:
            out.insert(
                0,
                "Provide a complete hourly rate schedule by classification for the "
                "initial term and any option years (mandatory Cost Proposal ask — "
                "not waived by retainer/hourly/hybrid fee-method flexibility).",
            )
    if rfp_mandates_cost_assumptions_disclosure(rfp_text):
        if "subconsultant markup" not in folded and "stock media" not in folded:
            out.insert(
                1 if out else 0,
                "State assumptions regarding travel, materials, software licenses, "
                "stock media, and subconsultant markup.",
            )
    return out


def pack_budget_compliance_advisory_block(
    *,
    rfp_text: str,
    draft_content: str = "",
    max_excerpt_chars: int = 12_000,
) -> str:
    """Cost-instrument excerpt + hard flags for Ask Ralph compliance answers."""
    from app.services.proposal_rfp_excerpt import budget_and_cost_excerpt

    parts: list[str] = []
    excerpt = budget_and_cost_excerpt(rfp_text or "", max_chars=max_excerpt_chars)
    if excerpt.strip():
        parts.append(
            "=== RFP BUDGET / COST EXCERPT (authoritative for Cost / fee asks) ===\n"
            f"{excerpt.strip()}"
        )
    flags = budget_compliance_hard_flags(rfp_text or "")
    if flags:
        parts.append(flags)
    if rfp_mandates_hourly_rate_schedule(rfp_text or "") and draft_lacks_hourly_rate_schedule(
        draft_content
    ):
        parts.append(
            "=== DRAFT GAP (mechanical) ===\n"
            "- Open Cost/fee draft appears to LACK a classification hourly rate "
            "schedule. Flag as high-priority responsiveness gap — phased / fixed-fee "
            "/ NTE tables alone do not satisfy a mandatory rate schedule."
        )
    return "\n\n".join(parts)


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
