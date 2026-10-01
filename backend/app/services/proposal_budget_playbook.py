"""Canonical pricing/budget playbook for Stage 3 and chat edits (option C enforcement)."""

from __future__ import annotations

import re

from app.models.proposal import ProposalBudget, ProposalResearchCache, ProposalSection
from app.models.rfp import RfpRecord
from app.services.proposal_budget_content import budget_section_score

BUDGET_EXPLAIN_ADVISORY_RULES = """=== BUDGET EXPLAIN MODE (mandatory when user asks totals / why / validity) ===
- zö agency is value-based. Explain totals from the CANONICAL BUDGET OBJECT and pricingFlags — never claim "clean" or "handled correctly" if flags or automated checks contradict you.
- Catalog items sell at their Pricing Book price. Custom work is priced by code from the work behind it. Say what the client gets; never state hours, roles, costs, margins or loaded rates — those stay internal.
- When a rate is asked for, there is one blended rate for every role. Never give rates by role.
- Separate valid reasoning (engagement type, media pass-through, approved wording) from invalid lines — list both honestly.
- If the pricing docs were not retrieved, say so — do not invent prices."""

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

4. RATES: zö agency gives one blended rate for every role, never rates by role. A
   required rate schedule is answered with that blended rate on every line (the
   negotiated rate where the client has one). Never invent classifications or
   dollars. A form that asks for hours per task is raised for Sonja, not filled.

5. When HARD FLAGS list a mandatory hourly schedule / assumptions ask, open with
   that gap — do not lead with "fee method is flexible so rates are optional."
"""

BUDGET_PLAYBOOK_CANONICAL = """=== ZÖ PRICING PLAYBOOK (mandatory for budget/fee work) ===

1. Value-based
   - Show a price for each item or phase, then the total. Descriptions say what the client gets.
   - Hours, roles, costs, margins and loaded rates never appear in client text.

2. Prices come from the pricing docs
   - Catalog items sell at their Pricing Book price. Custom work is built from the work (hours by
     role, POs, hard costs) and priced by code between the floor and the target.
   - Never invent a price or a rate.

3. Under the budget
   - When the RFP prints a budget, come in under it (about 90%), never below the floor.
   - If the floor is above the budget, the scope does not fit the money: report it and name the cuts.

4. Rates
   - When asked, one blended rate for every role; never rates by role.
   - A client with a negotiated rate (City of Bend) gets that rate.

5. Agency revenue vs client pass-through
   - Traditional media is client money: the outlet gets 85%, zö bills the 15% commission.
   - Digital media is a monthly management fee; ad spend runs on the client's card.
   - Keep pass-through separate so the agency fee is not inflated by media that was never zö's fee.

6. Management is in the price
   - Project management, account management and Agency Director oversight are never separate lines.

7. Approved wording
   - Billing and terms, outside the price, change orders, travel: use the approved blocks.
   - Two revision rounds plus a final review.

8. Flag, don't fill
   - [PRICING FLAG: description — below the floor or outside the pricing docs, Sonja review required]

9. Never reverse-engineer a line to hit a total
"""

OPTION_C_CHAT_POLICY = """=== OPTION C — CHAT / REVISE ENFORCEMENT ===
- REFUSE: invented dollar amounts with no pricing-doc source; reverse-engineered line prices to hit a user-requested total; $0 agency revenue when commission/fees apply; hours, costs or margins in client text; rates by role.
- FLAG ONLY: scope genuinely outside the pricing docs — use [PRICING FLAG: … — Sonja review required], do not guess.
- Otherwise apply safe playbook edits and explain tradeoffs in the assistant reply when you push back.
"""

BUDGET_FREEFORM_NARRATIVE_RULES = """=== BUDGET FREEFORM (Cost tab — surgical edit) ===
Obey the user's verbatim ask with the SMALLEST change. Prefer editing one table/section.

You MAY:
- add/rename/reorder columns or rows the user asked for
- clarify Scope cell wording / layout

You MUST NOT:
- delete or blank rate schedule $ rows that already exist
- replace a filled rate schedule with MANUAL FILL / "confirm before submit" prose
- invent person names — unknown → "—"
- invent new dollar amounts, rates, hourly figures, or line items
- change any Fee / Amount / Total cell away from the CANONICAL BUDGET OBJECT
- show hours, roles, costs or margins
- reverse-engineer fees to hit a target total
- paraphrase the approved wording blocks (billing and terms, outside the price, change orders)

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


def budget_chat_should_collapse_duplicate_cost_tabs(
    research: ProposalResearchCache | None,
) -> bool:
    """Gate for the chat-path Cost tab collapse (proposal_section_editor.improve_proposal_section).

    Frozen legacy budgets (no pricing_plan) are ordinary manuscript text — never
    collapse/merge their Cost tabs. Only v2 (pricing-plan) budgets get the collapse.
    """
    return bool(research and research.budget and research.budget.pricing_plan)


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


def _normalize_budget_ask_typos(text: str) -> str:
    """Fix common misspellings so Stage 3.5 rebuild routing still fires."""
    raw = text or ""
    # generate / regenerate (geenrate, genereate, regenerat, …)
    raw = re.sub(
        r"(?i)\b(?:re[\s-]?)?g+e+n+e*r+a*t+e?\b",
        lambda m: "regenerate" if m.group(0).casefold().startswith("re") else "generate",
        raw,
    )
    raw = re.sub(r"(?i)\brebui+ld\b", "rebuild", raw)
    return raw


def user_asks_budget_rebuild(text: str) -> bool:
    """True when the user wants Cost/budget filled or rebuilt from the Pricing Guide."""
    raw = _normalize_budget_ask_typos(text or "")
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
            r"line\s+items?|cost\s+proposal|compensation"
            r")\b"
            r"|"
            r"\b("
            r"budget|pricing|cost(?:\s+of)?(?:\s+base)?(?:\s+proposal)?|fee\s+table|"
            r"cost\s+proposal|compensation"
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
    raw = _normalize_budget_ask_typos(text or "")
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
        r"regenerate\s+(?:the\s+)?(?:budget|pricing|fee)|"
        r"generate\s+(?:the\s+)?(?:budget|pricing|fee|cost)"
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
    from app.services.proposal_budget_validation import collect_line_item_math_violations

    plan = budget.pricing_plan or {}
    lines: list[str] = [
        f"pricingVersion: {plan.get('pricing_version') or '(none)'}",
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
    checks = collect_line_item_math_violations(budget)
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
            "Say **generate budget** or **rebuild budget from the pricing guide** "
            "— chat will not invent fees outside Stage 3.5."
        )
    return None


def _is_numeric_token(token: str) -> bool:
    try:
        float(token)
        return True
    except ValueError:
        return False


BUDGET_TOOL_ROUTING = """=== BUDGET TOOL ROUTING (mandatory) ===
The Cost section's fees come from the pricing plan — never pick a tier or price
from the guide, and never re-derive fee amounts, hours, or rates.
1) Call search_rfp_requirements for budget ceiling, cost evaluation weight, quote/pricing form rules.
2) Edit layout / columns / names / prose only. Never invent dollars; never put phone
   numbers in Fee columns; use [VERIFY: …] when unknown.
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
    """RFP budget excerpt + playbook for repair/revise agents.

    ``rfp`` and ``user_message`` are kept in the signature for the existing
    call sites (proposal_self_edit_loop.py, proposal_section_editor.py) even
    though this function no longer uses them — fees come from the pricing
    plan, not from a fetched Pricing Guide, so nothing here needs to build a
    guide-search focus hint any more.
    """
    del rfp, user_message
    from app.services.proposal_rfp_excerpt import budget_and_cost_excerpt

    cost_excerpt = budget_and_cost_excerpt(rfp_text, max_chars=16_000)
    parts = [
        BUDGET_TOOL_ROUTING,
        budget_playbook_prompt_block(research=research, full_budget_detail=True),
    ]
    try:
        from app.services.pricing_delivery_context import (
            format_pricing_delivery_constraints_block,
        )

        delivery_block = format_pricing_delivery_constraints_block(
            research, focus="budget"
        )
        if delivery_block.strip():
            parts.insert(0, delivery_block)
    except Exception:  # noqa: BLE001
        pass
    if cost_excerpt.strip():
        parts.append(f"=== RFP BUDGET / COST EXCERPT ===\n{cost_excerpt[:14_000]}")
    return "\n\n".join(parts)
