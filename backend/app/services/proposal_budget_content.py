"""Render Stage 3 budget into proposal section content and sync to draft."""

from __future__ import annotations

import re
from datetime import datetime, timezone

from app.models.proposal import BudgetLineItem, ProposalBudget, ProposalDraft, ProposalSection
from app.services.proposal_repository import aget_proposal_draft, asave_proposal_draft
from app.services.proposal_rfp_excerpt import rfp_forbids_quotation_form_changes

_BUDGET_TITLE_PATTERN = re.compile(
    r"\b(budget|pricing|price\s*proposal|fee\s*schedule|cost\s*proposal|compensation)\b",
    re.I,
)

_BLENDED_FORM_RE = re.compile(
    r"(?:pricing\s+proposal\s+form|cost\s+proposal\s+form|schedule\s+of\s+fees)"
    r"|hourly.{0,80}monthly.{0,80}annual"
    r"|annual\s*=\s*monthly",
    re.I | re.S,
)


def rfp_wants_blended_pricing_form(rfp_text: str) -> bool:
    """True when THIS RFP's pricing deliverable is a 3-field blended rate form."""
    return bool(_BLENDED_FORM_RE.search(rfp_text or ""))


def _usd(value: float | None) -> str:
    if value is None:
        return "—"
    if abs(value - round(value)) < 0.01:
        return f"${value:,.0f}"
    return f"${value:,.2f}"


def derive_blended_form_rates(
    budget: ProposalBudget,
) -> tuple[float | None, float | None, float | None, str]:
    """Return (hourly, monthly, annual, notes) for an RFP Pricing Proposal Form."""
    hourly = budget.form_hourly_rate
    monthly = budget.form_monthly_rate
    annual = budget.form_annual_rate
    notes = (budget.form_rate_notes or "").strip()

    if hourly is not None and monthly is not None and annual is not None:
        return (
            hourly,
            monthly,
            annual,
            notes or "Rates as submitted on the RFP Pricing Proposal Form.",
        )

    hour_rows = [
        item
        for item in budget.line_items
        if item.rate is not None
        and item.quantity
        and item.quantity > 0
        and (item.unit or "").lower() in {"hour", "hours", "hr", "hrs"}
    ]
    if hourly is None and hour_rows:
        total_hours = sum(float(i.quantity or 0) for i in hour_rows)
        total_fees = sum(float(i.extended or 0) for i in hour_rows)
        if total_hours > 0:
            hourly = total_fees / total_hours
            notes = notes or (
                "Blended hourly = agency-fee hours ÷ extended fees from the supporting rate build."
            )

    fee_base = (
        budget.agency_revenue_estimate
        or budget.agency_fee_subtotal
        or budget.lump_sum_total
    )
    if monthly is None and fee_base is not None and fee_base > 0:
        monthly = float(fee_base) / 12.0
        notes = notes or (
            "Monthly rate = annualized agency fee ÷ 12 (supporting build below)."
        )
    if annual is None and monthly is not None:
        annual = float(monthly) * 12.0
    elif annual is None and fee_base is not None:
        annual = float(fee_base)
        if monthly is None:
            monthly = annual / 12.0

    if hourly is None and monthly is not None:
        hourly = float(monthly) / 160.0
        notes = notes or (
            "Hourly rate approximated as monthly ÷ 160 billable hours for the RFP form; "
            "confirm with Sonja before submission."
        )

    return hourly, monthly, annual, notes


def render_verbatim_quotation_form_markdown(budget: ProposalBudget) -> str:
    """Worksheet matching typical NJ college quotation forms — no substitute A/B/C/D structure."""
    hourly, monthly, annual, notes = derive_blended_form_rates(budget)
    lines = [
        "## Quotation / Pricing Proposal Form (complete the RFP's official form — do not alter it)",
        "",
        "The buyer's RFP states that **changes to the Quotation/Pricing Proposal Form can "
        "disqualify the submission**. Fill in the **exact form the College issued** (PDF/Word). "
        "Use this table only as a draft worksheet; do not replace their layout in the export package.",
        "",
        "| Field | Response |",
        "| --- | --- |",
        "| Legal Business Name | zö agency |",
        "| Federal Tax ID (FEIN) | [MANUAL FILL: use verified FEIN from Section 1] |",
        "| Business Address | [MANUAL FILL: use verified address from Section 1] |",
        "| Authorized Representative (signature) | [MANUAL FILL: wet/digital signature] |",
        "| Printed Name | [MANUAL FILL: authorized signatory] |",
        "| Title | [MANUAL FILL] |",
        "| Telephone | [MANUAL FILL: business phone from Section 1] |",
        "| Fax | [MANUAL FILL or N/A] |",
        "| Email | [MANUAL FILL: business email from Section 1] |",
        f"| **Hourly Rate** | {_usd(hourly)} |",
        "| Hourly Rate (amount in words) | [MANUAL FILL: spell hourly amount in words per RFP] |",
        f"| **Monthly Rate** | {_usd(monthly)} |",
        "| Monthly Rate (amount in words) | [MANUAL FILL: spell monthly amount in words per RFP] |",
        f"| **Annual Rate** (monthly × 12 if required) | {_usd(annual)} |",
        "| Annual Rate (amount in words) | [MANUAL FILL: spell annual amount in words per RFP] |",
        "",
    ]
    if notes:
        lines.append(f"*Rate derivation (for internal use — do not paste onto the official form):* {notes}")
        lines.append("")
    if hourly is None or monthly is None or annual is None:
        lines.append(
            "[MANUAL FILL: Confirm hourly, monthly, and annual on the official Pricing Proposal "
            "Form before export.]"
        )
        lines.append("")
    return "\n".join(lines)


def render_pricing_proposal_form_markdown(
    budget: ProposalBudget,
    *,
    rfp_text: str = "",
) -> str:
    if rfp_forbids_quotation_form_changes(rfp_text):
        return render_verbatim_quotation_form_markdown(budget)
    if rfp_wants_blended_pricing_form(rfp_text):
        return render_verbatim_quotation_form_markdown(budget)
    hourly, monthly, annual, notes = derive_blended_form_rates(budget)
    lines = [
        "## Pricing Proposal Form",
        "",
        "This is the RFP-required rate block (complete and return). "
        "Supporting line-item rationale follows only if needed for evaluators.",
        "",
        "| Rate | Amount |",
        "| --- | ---: |",
        f"| **Hourly rate** | {_usd(hourly)} |",
        f"| **Monthly rate** | {_usd(monthly)} |",
        f"| **Annual rate** *(monthly × 12)* | {_usd(annual)} |",
        "",
    ]
    if notes:
        lines.append(notes)
        lines.append("")
    if hourly is None or monthly is None or annual is None:
        lines.append(
            "[MANUAL FILL: Confirm blended hourly / monthly / annual on the agency's "
            "Pricing Proposal Form before export.]"
        )
        lines.append("")
    return "\n".join(lines)


_DEDICATED_BUDGET_TITLE_RE = re.compile(
    r"\b("
    r"cost\s+of(?:\s+the)?\s+base(?:\s+bid)?|"
    r"cost\s+proposal|"
    r"fee\s+schedule|"
    r"price\s+proposal|"
    r"pricing\s+proposal|"
    r"compensation\s+schedule|"
    r"budget\s*(?:&|and)\s*pricing|"
    r"budget\s+and\s+fees|"
    r"fees?\s*(?:&|and)\s*budget|"
    r"proposed\s+(?:fees?|pricing|budget)"
    r")\b",
    re.I,
)

# "Budgets" listed among SOW/timeline/reporting topics — not the Cost Proposal tab.
_INCIDENTAL_BUDGET_LIST_RE = re.compile(
    r"\bbudgets?\b.{0,40}\b("
    r"timeline|timelines|schedule|schedules|reporting|report|methodology|"
    r"approach|deliverable|kpi|kpis"
    r")\b|"
    r"\b("
    r"timeline|timelines|schedule|schedules|reporting|report|methodology|"
    r"approach|deliverable|kpi|kpis"
    r")\b.{0,40}\bbudgets?\b",
    re.I,
)


def budget_section_score(title: str) -> int:
    t = title.lower()
    # Sections that merely list "budgets" among SOW/compliance topics are NOT
    # the Cost Proposal tab (e.g. "… Timelines, Budgets, Reporting …").
    if re.search(
        r"\b("
        r"compliance|general\s+requirements|records?\s+retention|"
        r"acknowledgements?|cover\s+letter|case\s*stud|references?"
        r")\b",
        t,
    ) and not _DEDICATED_BUDGET_TITLE_RE.search(t):
        return 0
    if _INCIDENTAL_BUDGET_LIST_RE.search(t) and not _DEDICATED_BUDGET_TITLE_RE.search(t):
        return 0

    score = 0
    if _DEDICATED_BUDGET_TITLE_RE.search(t):
        score += 8
    if "budget" in t:
        score += 4
    if "pricing" in t or "price proposal" in t:
        score += 3
    if "fee" in t:
        score += 2
    if "cost" in t:
        score += 1
    if "compensation" in t:
        score += 2
    if _BUDGET_TITLE_PATTERN.search(title):
        score = max(score, 2)
    return score


_OFFICIAL_PRICING_FORM_TITLE_RE = re.compile(
    r"(?i)\b("
    r"pricing\s+proposal\s+form|quotation\s+(?:\/\s*)?pricing|"
    r"request\s+for\s+qualifications\s+pricing|rfq\s+pricing|"
    r"cost\s+proposal\s+form|schedule\s+of\s+fees"
    r")\b"
)
_OFFICIAL_PRICING_FORM_BODY_RE = re.compile(
    r"(?is)section\s+i[:\s].{0,40}contact|"
    r"grand\s+total\s*\(\s*in\s+words\s*\)|"
    r"contact\s+person\s*:|"
    r"rfq\s+number\s*:"
)


def section_looks_like_official_pricing_form(section: ProposalSection) -> bool:
    """Buyer RFQ / quotation form tab — must not be wiped by Budget & Pricing render."""
    title = section.title or ""
    content = section.content or ""
    if _OFFICIAL_PRICING_FORM_TITLE_RE.search(title):
        return True
    if _OFFICIAL_PRICING_FORM_BODY_RE.search(content):
        return True
    return False


def official_pricing_form_is_filled(content: str) -> bool:
    """True when the form already has dollars (do not LLM-redraft or re-render over it)."""
    text = (content or "").strip()
    if len(text) < 120:
        return False
    return bool(re.search(r"\$\s*[\d,]+", text))


def find_budget_section_index(sections: list[ProposalSection]) -> int | None:
    """Prefer narrative Budget & Pricing over a filled official RFQ pricing form.

    Complete & Clean used to treat "Request for Qualifications Pricing Form" as the
    budget tab and overwrite a finished buyer form with generic Proposed Investment
    markdown (or trigger restore/reshape). Prefer a dedicated Budget section when
    both exist; never score a filled official form as the sole write target when a
    narrative budget sibling is present.
    """
    best_idx: int | None = None
    best_score = 0
    filled_form_idx: int | None = None
    for i, section in enumerate(sections):
        score = budget_section_score(section.title)
        if score <= 0:
            continue
        if section_looks_like_official_pricing_form(section) and official_pricing_form_is_filled(
            section.content or ""
        ):
            # Keep as fallback only — prefer non-form budget tabs.
            if filled_form_idx is None or score > budget_section_score(
                sections[filled_form_idx].title
            ):
                filled_form_idx = i
            continue
        if score > best_score:
            best_score = score
            best_idx = i
    if best_idx is not None:
        from app.services.proposal_budget_slots import find_unresolved_budget_slots

        slotted: list[tuple[int, int]] = []
        for i, section in enumerate(sections):
            if budget_section_score(section.title) <= 0:
                continue
            if find_unresolved_budget_slots(section.content or ""):
                slotted.append((budget_section_score(section.title), i))
        if slotted:
            slotted.sort(reverse=True)
            return slotted[0][1]
        return best_idx
    return filled_form_idx


def _cost_tab_quality_score(section: ProposalSection) -> int:
    """Higher = keep this Cost/Pricing tab when collapsing duplicates."""
    from app.services.proposal_budget_slots import find_unresolved_budget_slots

    body = section.content or ""
    score = 0
    if find_unresolved_budget_slots(body) or "{{budget." in body:
        score -= 250
    money = [
        float(tok.replace(",", ""))
        for tok in re.findall(r"\$\s*([\d,]+(?:\.\d{1,2})?)", body)
        if tok.replace(",", "").replace(".", "", 1).isdigit()
    ]
    if any(amt >= 100 for amt in money):
        score += 120
    if re.search(r"(?i)fee\s+detail|proposed\s+investment", body):
        score += 40
    score += min(len(body) // 100, 50)
    return score


def collapse_duplicate_cost_proposal_tabs(
    sections: list[ProposalSection],
) -> tuple[list[ProposalSection], list[str]]:
    """Keep one narrative Cost/Pricing tab — never a second {{budget.}} shell.

    Scan/senior-editor dedupe treats every Cost Proposal as protected, so Phase 3
    plus Structure Scan used to ship both 'Cost Proposal using Appendix A…' (real
    fees) and a later 'Cost Proposal' full of unresolved money slots.
    Official filled buyer pricing forms are left beside the narrative tab.
    """
    from app.services.proposal_outline_dedup import is_pricing_outline_title

    logs: list[str] = []
    narrative_idxs: list[int] = []
    for i, section in enumerate(sections):
        if budget_section_score(section.title) <= 0 and not is_pricing_outline_title(
            section.title or ""
        ):
            continue
        if section_looks_like_official_pricing_form(section) and official_pricing_form_is_filled(
            section.content or ""
        ):
            continue
        narrative_idxs.append(i)
    if len(narrative_idxs) <= 1:
        return sections, logs

    keep_idx = max(narrative_idxs, key=lambda i: _cost_tab_quality_score(sections[i]))
    drop_ids = {
        sections[i].id
        for i in narrative_idxs
        if i != keep_idx and sections[i].id
    }
    if not drop_ids:
        return sections, logs
    kept_title = sections[keep_idx].title or sections[keep_idx].id
    dropped_titles = [
        sections[i].title or sections[i].id
        for i in narrative_idxs
        if sections[i].id in drop_ids
    ]
    logs.append(
        "Budget: collapsed duplicate Cost/Pricing tab(s) into "
        f"“{kept_title}” — dropped {', '.join(dropped_titles[:6])}."
    )
    return [s for s in sections if s.id not in drop_ids], logs


_DOLLAR_RE = re.compile(r"\$[\d,]+(?:\.\d{1,2})?")
_OF2_MARKER_RE = re.compile(r"(?i)\boffer\s+form\s+of-?2\b|all-?inclusive\s+contract\s+cost")
_OF2_TABLE_RE = re.compile(
    r"(?is)\|[ \t]*Line Item[ \t]*\|[ \t]*Description[ \t]*\|[ \t]*Cost \(USD\)[ \t]*\|.*?"
    r"(?=(?:\n#{1,6}\s)|\Z)"
)


def _line_looks_like_travel(item: BudgetLineItem) -> bool:
    """One canonical travel test — infer_line_item_type owns the vocabulary.

    This used to carry its own _TRAVEL_RE, a strict subset of the classifier's
    (no "reimbursable", no "out-of-pocket"). dedupe_travel_vs_direct_expenses
    therefore failed to clear a duplicated directExpensesTotal for those rows,
    so the same dollars were counted in a line item AND in the direct bucket
    with every gate green — the same class of defect as the $3,500 travel row.
    """
    from app.services.proposal_budget_validation import infer_line_item_type

    return infer_line_item_type(item) == "direct_expense"


def dedupe_travel_vs_direct_expenses(budget: ProposalBudget) -> ProposalBudget:
    """If travel is already a line item, do not also add the same amount as direct expenses."""
    direct = round(float(budget.direct_expenses_total or 0), 2)
    if direct <= 0:
        return budget
    travel_ext = [
        round(float(i.extended or 0), 2)
        for i in budget.line_items
        if _line_looks_like_travel(i) and i.extended is not None
    ]
    if not travel_ext:
        return budget
    travel_sum = round(sum(travel_ext), 2)
    if abs(travel_sum - direct) <= 1.0 or any(abs(x - direct) <= 1.0 for x in travel_ext):
        flags = list(budget.pricing_flags or [])
        note = (
            "[PRICING FLAG: Cleared duplicate directExpensesTotal — travel already in line items]"
        )
        if note not in flags:
            flags.append(note)
        return budget.model_copy(
            update={"direct_expenses_total": 0.0, "pricing_flags": flags}
        )
    return budget


def _canonical_client_total(budget: ProposalBudget) -> float | None:
    line_sum = round(
        sum(float(i.extended or 0) for i in budget.line_items if i.extended is not None),
        2,
    )
    direct = round(float(budget.direct_expenses_total or 0), 2)
    computed = round(line_sum + direct, 2)
    if computed > 0:
        return computed
    if budget.lump_sum_total is not None:
        return round(float(budget.lump_sum_total), 2)
    if budget.agency_revenue_estimate is not None:
        return round(float(budget.agency_revenue_estimate), 2)
    return None


def _fmt_money(amount: float) -> str:
    """Format money as digits only (no leading '$').

    The table often already includes a literal '$' right before the [VERIFY: ...]
    token, so we add '$' only if it isn't present in the source text.
    """
    if abs(amount - round(amount)) < 0.005:
        return f"{int(round(amount)):,}"
    return f"{amount:,.2f}"


def fill_section_budget_verify_from_canonical(
    content: str,
    budget: ProposalBudget,
) -> tuple[str, int]:
    """Replace [VERIFY: budget/investment/…] tags using the canonical Stage 3.5 budget.

    Used when the user asks to fill the budget part of the *open* section (e.g. a
    case study fee table) — does not rebuild Cost of Base Proposal.
    """
    from app.services.proposal_manual_flags import VERIFY_TAG_RE
    from app.core.step_debug_logger import step_trace

    if not content or not VERIFY_TAG_RE.search(content):
        return content, 0
    budget = prepare_budget_for_client_display(budget)
    total = _canonical_client_total(budget)
    if total is None or total <= 0:
        step_trace(
            "budget_verify_fill_total_missing",
            rfp_id=getattr(budget, "rfp_id", None),
            total=total,
            agency_revenue_estimate=budget.agency_revenue_estimate,
            lump_sum_total=budget.lump_sum_total,
            direct_expenses_total=budget.direct_expenses_total,
            client_media_passthrough=budget.client_media_passthrough,
            line_items=len(budget.line_items or []),
            priced_line_items=sum(
                1
                for i in (budget.line_items or [])
                if (i.extended is not None and float(i.extended) > 0)
            ),
        )
        return content, 0

    # Phase label → sum of extended fees for matching line items.
    phase_sums: dict[str, float] = {}
    for item in budget.line_items:
        if item.extended is None:
            continue
        label = _phase_label_for_line(item)
        phase_sums[label] = phase_sums.get(label, 0.0) + float(item.extended)

    fills = 0

    def _is_budget_field(field: str) -> bool:
        f = field.casefold()
        return any(
            k in f
            for k in (
                "budget",
                "investment",
                "fee",
                "pricing",
                "cost",
                "total",
                "phase",
                "dollar",
                "amount",
                "figure",
            )
        )

    def _is_total_field(field: str) -> bool:
        f = field.casefold()
        return "total" in f or "grand" in f or "overall" in f

    def repl(match: re.Match[str]) -> str:
        nonlocal fills
        field = match.group(1) or ""
        if not _is_budget_field(field):
            return match.group(0)
        if _is_total_field(field):
            fills += 1
            has_dollar_prefix = (
                match.start() > 0 and content[match.start() - 1] == "$"
            )  # noqa: S608
            return ("" if has_dollar_prefix else "$") + _fmt_money(total)
        # Prefer phase match from surrounding line text (handled in line pass below).
        return match.group(0)

    # First pass: totals only (safe global replace).
    updated = VERIFY_TAG_RE.sub(repl, content)

    # Second pass: line-by-line for remaining budget VERIFY tags.
    out_lines: list[str] = []
    for line in updated.splitlines(keepends=True):
        if not VERIFY_TAG_RE.search(line):
            out_lines.append(line)
            continue

        def line_repl(match: re.Match[str]) -> str:
            nonlocal fills
            field = match.group(1) or ""
            if not _is_budget_field(field) or _is_total_field(field):
                return match.group(0)
            line_cf = line.casefold()
            has_dollar_prefix = (
                match.start() > 0 and line[match.start() - 1] == "$"
            )  # noqa: S608
            # Match phase / deliverable wording on this row to canonical phase buckets.
            for label, amount in phase_sums.items():
                tokens = [
                    t
                    for t in re.split(r"[^a-z0-9]+", label.casefold())
                    if len(t) >= 4 and t not in {"phase", "fees"}
                ]
                if tokens and all(t in line_cf for t in tokens[:2]):
                    fills += 1
                    return ("" if has_dollar_prefix else "$") + _fmt_money(amount)
            # Discovery / strategy / etc. keyword fallbacks from the row label.
            for needle, keys in (
                ("discovery", ("discovery", "audit")),
                ("strategy", ("strategy", "positioning")),
                ("tactical", ("tactical", "execution")),
                ("roadmap", ("roadmap", "handoff")),
                ("project management", ("project", "management")),
                ("travel", ("travel", "reimburs")),
            ):
                if any(k in line_cf for k in keys):
                    for label, amount in phase_sums.items():
                        if any(k in label.casefold() for k in keys):
                            fills += 1
                            return ("" if has_dollar_prefix else "$") + _fmt_money(amount)
            return match.group(0)

        out_lines.append(VERIFY_TAG_RE.sub(line_repl, line))

    return "".join(out_lines), fills


def _of2_get_rate(content: str) -> float:
    """Best-effort GET rate for OF-2.

    Prefer an explicit percentage in the section content; otherwise default to the
    conservative Oʻahu 4.5% used in the current attachment language.
    """
    match = re.search(r"(\d+(?:\.\d+)?)\s*%", content or "")
    if match:
        try:
            pct = float(match.group(1))
            if 0 < pct < 100:
                return pct / 100.0
        except ValueError:
            pass
    return 0.045


def render_offer_form_of2_from_canonical(
    content: str,
    budget: ProposalBudget,
) -> tuple[str, bool]:
    """Render Attachment 2 / OF-2 pricing table from the canonical budget.

    This is deterministic by design: budget sections must not be freeform-rewritten
    by the chat model, otherwise nearby manuscript text can leak into money cells.
    """
    if not content or not _OF2_MARKER_RE.search(content):
        return content, False

    budget = prepare_budget_for_client_display(budget)
    total = _canonical_client_total(budget)
    if total is None or total <= 0:
        return content, False

    fee_items = [
        item
        for item in (budget.line_items or [])
        if item.extended is not None and float(item.extended) > 0
    ]
    if not fee_items:
        return content, False

    get_rate = _of2_get_rate(content)
    subtotal_pre_get = round(total / (1.0 + get_rate), 2)
    get_amount = round(total - subtotal_pre_get, 2)
    rate_label = f"{get_rate * 100:.1f}%"
    if abs(get_rate * 100 - round(get_rate * 100)) < 0.001:
        rate_label = f"{int(round(get_rate * 100))}%"

    lines = [
        "| Line Item | Description | Cost (USD) |",
        "| --- | --- | ---: |",
    ]
    for idx, item in enumerate(fee_items, start=1):
        _phase, desc = _client_line_label(item)
        lines.append(f"| {idx} | {desc} | {_usd(float(item.extended or 0))} |")
    lines.extend(
        [
            f"| **Subtotal (pre-GET)** | | **{_usd(subtotal_pre_get)}** |",
            f"| **Hawaiʻi GET (Oʻahu, {rate_label})** | Baked into the total per RFP §3.4.1 | **{_usd(get_amount)}** |",
            f"| **TOTAL ALL-INCLUSIVE CONTRACT COST** | Fixed, not-to-exceed, inclusive of all costs | **{_usd(total)}** |",
        ]
    )
    rendered_table = "\n".join(lines)

    if _OF2_TABLE_RE.search(content):
        return _OF2_TABLE_RE.sub(rendered_table, content, count=1), True

    # Fallback: append under the heading if the table is malformed/missing.
    if _OF2_MARKER_RE.search(content):
        return content.rstrip() + "\n\n" + rendered_table + "\n", True
    return content, False


def _professional_fees_and_direct(budget: ProposalBudget) -> tuple[float, float]:
    """Split agency professional fees from travel/reimbursables.

    Delegates classification to infer_line_item_type so this cannot disagree with
    split_line_item_totals — that disagreement is what let one travel row count as
    fee, travel and total simultaneously.
    """
    from app.services.proposal_budget_validation import (
        direct_expense_subtotal,
        split_line_item_totals,
    )

    _line_sum, fee_sum, _passthrough = split_line_item_totals(budget.line_items)
    travel_in_lines = direct_expense_subtotal(budget.line_items)
    direct = round(float(budget.direct_expenses_total or 0), 2)
    # Travel lives in lines XOR directExpensesTotal after dedupe.
    return round(fee_sum, 2), round(travel_in_lines + direct, 2)


def _parse_dollar_amounts(text: str) -> list[float]:
    """Extract dollar amounts from text (structural parse only)."""
    out: list[float] = []
    for raw in _DOLLAR_RE.findall(text or ""):
        try:
            out.append(float(raw.replace("$", "").replace(",", "")))
        except ValueError:
            continue
    return out


def _strip_dollar_bearing_sentences(text: str) -> str:
    """Keep non-money prose; drop sentences that embed dollar figures.

    Phase/fee dollars are re-injected from structured line items instead.
    """
    if not text or "$" not in text:
        return (text or "").strip()
    kept: list[str] = []
    buf = ""
    flat = text.replace("\n", " ")
    i = 0
    while i < len(flat):
        ch = flat[i]
        buf += ch
        # Sentence end: .!? not a cents decimal inside a money token.
        if ch in ".!?":
            is_cents = (
                ch == "."
                and i + 1 < len(flat)
                and flat[i + 1].isdigit()
                and bool(buf.rstrip()[:-1])
                and buf.rstrip()[-2:-1].isdigit()
            )
            if not is_cents:
                if "$" not in buf:
                    piece = buf.strip()
                    if piece:
                        kept.append(piece if piece[-1] in ".!?" else piece + ".")
                buf = ""
        i += 1
    trailing = buf.strip()
    if trailing and "$" not in trailing:
        kept.append(trailing if trailing[-1] in ".!?" else trailing + ".")
    return " ".join(kept).strip()


def _sync_narrative_total(
    text: str,
    canonical: float,
    *,
    protect: list[float] | None = None,
) -> str:
    """Sync a single stale grand-total figure — never rewrite multi-amount phase lists.

    Structural rule: if the prose contains more than one unprotected dollar amount,
    leave it alone (phase subtotals / fee breakdowns). Only when there is exactly
    one stale figure do we replace it with the canonical engagement total.
    """
    if not text or canonical <= 0:
        return text
    protected = {round(float(p), 2) for p in (protect or []) if p and float(p) > 0}
    matches = list(_DOLLAR_RE.finditer(text))
    if not matches:
        return text

    stale: list[tuple[int, int]] = []
    for m in matches:
        try:
            val = float(m.group(0).replace("$", "").replace(",", ""))
        except ValueError:
            continue
        if any(abs(val - p) <= 1.0 for p in protected):
            continue
        if abs(val - canonical) <= 1.0:
            continue
        stale.append((m.start(), m.end()))

    # Multi-phase / multi-fee narratives: do not paste the grand total into each slot.
    if len(stale) != 1:
        return text

    start, end = stale[0]
    return text[:start] + _usd(canonical) + text[end:]


def _phase_breakdown_from_lines(budget: ProposalBudget) -> str:
    """Build phase dollars from line items — source of truth for client narrative."""
    phase_sums: dict[str, float] = {}
    order: list[str] = []
    for item in budget.line_items or []:
        if item.extended is None:
            continue
        phase, _desc = _client_line_label(item)
        if phase not in phase_sums:
            order.append(phase)
            phase_sums[phase] = 0.0
        phase_sums[phase] += float(item.extended)
    if len(order) < 2:
        return ""
    parts = [f"{label} ({_usd(phase_sums[label])})" for label in order]
    if len(parts) == 2:
        return f"Fee phases: {parts[0]} and {parts[1]}."
    return "Fee phases: " + ", ".join(parts[:-1]) + f", and {parts[-1]}."


# Sentence strip must allow cents inside money tokens. A naive [^.]*\. stops at
# $154,026. and leaves orphan remnants like "32 ($150,526.32 in professional fees…)".
_MONEY_TOKEN = r"\$[\d,]+(?:\.\d{2})?"
# Match through the real sentence-ending period (not a cents decimal).
_INVESTMENT_SENTENCE_RE = re.compile(
    r"(?i)\b(?:total\s+proposed\s+investment|total\s+estimated\s+investment|"
    r"total\s+professional\s+fees|estimated\s+reimbursable\s+travel|"
    r"professional\s+fees)\s*:"
    rf"(?:[^.\$]|{_MONEY_TOKEN})*\."
)
# Orphan cents left after a prior botched strip (one or many, any position).
_ORPHAN_CENTS_REMNANT_RE = re.compile(
    rf"(?i)\s*\d{{1,3}}\s*\({_MONEY_TOKEN}\s+in\s+"
    rf"(?:professional\s+fees|direct\s+travel|client\s+media)[^)]*\)\s*\.?"
)


def _rewrite_investment_sentence(
    scope: str,
    fees: float,
    direct: float,
    total: float,
    *,
    passthrough: float = 0.0,
) -> str:
    """Ensure scope states fees + travel correctly (not 'total including $total travel')."""
    text = (scope or "").strip()
    # Drop prior investment / fee total sentences — they drift and get truncated.
    text = _INVESTMENT_SENTENCE_RE.sub("", text)
    text = re.sub(
        rf"(?i)\bestimated\s+reimbursable\s+travel\s*:?(?:[^.\$]|{_MONEY_TOKEN})*\.",
        "",
        text,
    )
    # Also drop investment lines that never got a closing period (mid-rewrite garbage).
    text = re.sub(
        rf"(?i)\b(?:total\s+proposed\s+investment|total\s+estimated\s+investment|"
        rf"total\s+professional\s+fees|professional\s+fees)\s*:"
        rf"\s*{_MONEY_TOKEN}(?:\s*\([^)]*\))?",
        "",
        text,
    )
    # Strip orphan cents remnants from older botched rewrites
    # (e.g. "32 ($150,526.32 in professional fees plus $3,500 in direct travel expenses)").
    text = _ORPHAN_CENTS_REMNANT_RE.sub("", text)
    # Strip trailing garbage from incomplete dollar rewrites (e.g. "43 ($116,368.")
    text = re.sub(rf"\s+\d{{1,3}}\s*\({_MONEY_TOKEN}\.?\s*$", "", text)
    text = re.sub(r"\s{2,}", " ", text).strip(" .\n")
    parts: list[str] = []
    if fees > 0:
        parts.append(f"{_usd(fees)} in professional fees")
    if direct > 0:
        parts.append(f"{_usd(direct)} in direct travel expenses")
    if passthrough > 0:
        parts.append(f"{_usd(passthrough)} in client media pass-through at net")
    if parts and total > 0:
        if len(parts) == 1:
            clause = f"Total proposed investment: {_usd(total)} ({parts[0]})."
        elif len(parts) == 2:
            clause = (
                f"Total proposed investment: {_usd(total)} "
                f"({parts[0]} plus {parts[1]})."
            )
        else:
            clause = (
                f"Total proposed investment: {_usd(total)} "
                f"({', '.join(parts[:-1])}, and {parts[-1]})."
            )
    elif total > 0:
        clause = f"Total proposed investment: {_usd(total)}."
    else:
        return text
    if text:
        return f"{text}. {clause}" if not text.endswith(".") else f"{text} {clause}"
    return clause


def _sync_qualifying_fee_language(
    qualifying: str,
    *,
    fees: float,
    direct: float,
    total: float,
) -> str:
    """Keep Investment Framing dollars aligned with the fee table."""
    text = (qualifying or "").strip()
    if not text or fees <= 0:
        return text
    fee_label = (
        f"proposed investment of {_usd(fees)} in professional fees"
        + (f", plus {_usd(direct)} in reimbursable travel" if direct > 0 else "")
    )
    # Match professional / consultant / agency fee phrasings — LLM Terms often
    # invent "$240,000 in agency Consultant Fees" that must track the fee table.
    text = re.sub(
        r"(?i)(?:proposed\s+investment\s+of\s+)?\$[\d,]+(?:\.\d{2})?\s+in\s+"
        r"(?:professional\s+fees|agency\s+consultant\s+fees|consultant\s+fees|"
        r"agency\s+fees|professional\s+services(?:\s+fees)?)"
        r"(?:\s*,?\s*plus(?:\s+an\s+estimated)?\s+\$[\d,]+(?:\.\d{2})?\s+in\s+"
        r"reimbursable\s+travel)?",
        fee_label,
        text,
        count=1,
    )
    # Single stale grand-total left in Terms (e.g. only $240,000 appears) → sync.
    text = _sync_narrative_total(
        text,
        fees,
        protect=[direct] if direct > 0 else None,
    )
    return text


def _sync_labeled_fee_subtotal(text: str, fees: float) -> str:
    """Keep 'Agency Fee Subtotal' / 'Professional fees' figures on the table sum.

    Multi-amount phase lists are otherwise left alone (Complete-scan used to
    rewrite the table and leave the old subtotal in Terms).
    """
    if not text or fees <= 0:
        return text
    usd = _usd(fees)

    def _repl(match: re.Match[str]) -> str:
        return f"{match.group(1)}{match.group(2)}{usd}"

    return re.sub(
        r"(?i)(\*{0,2}(?:agency\s+fee\s+subtotal|professional\s+(?:services\s+)?fees?)"
        r"\*{0,2})(\s*[:—-]\s*)\$[\d,]+(?:\.\d{2})?",
        _repl,
        text,
    )


def prepare_budget_for_client_display(budget: ProposalBudget) -> ProposalBudget:
    """Dedupe travel, sync totals, scrub internal jargon before manuscript render.

    Preserves agency vs pass-through split: agency_revenue / lump_sum = agency fees
    (+ direct); total_client_invoicing = client grand total including pass-through.
    """
    from app.services.proposal_budget_validation import split_line_item_totals

    cleaned = dedupe_travel_vs_direct_expenses(budget)
    fees, reimbursables = _professional_fees_and_direct(cleaned)
    direct_bucket = round(float(cleaned.direct_expenses_total or 0), 2)
    line_sum, agency_fee, passthrough = split_line_item_totals(cleaned.line_items or [])
    if agency_fee <= 0:
        agency_fee = fees
    if passthrough <= 0 and cleaned.client_media_passthrough:
        passthrough = round(float(cleaned.client_media_passthrough), 2)
    # Professional fees + travel (whether travel lives in lines or direct bucket).
    agency_revenue = round(fees + reimbursables, 2)
    if agency_revenue <= 0 and agency_fee > 0:
        agency_revenue = round(agency_fee + direct_bucket, 2)
    # Client total = all line items + residual direct bucket (never add travel twice).
    client_total = (
        round(line_sum + direct_bucket, 2)
        if line_sum > 0
        else round(agency_revenue + passthrough, 2)
    )
    # Narrative "proposed investment" uses client total when media is billed through.
    display_total = client_total if client_total > 0 else agency_revenue
    updates: dict = {}
    if display_total is not None and display_total > 0:
        updates["line_item_sum"] = round(float(line_sum or 0), 2)
        updates["agency_fee_subtotal"] = round(float(agency_fee or fees), 2)
        updates["agency_revenue_estimate"] = agency_revenue
        # Lump sum tracks agency proposed fees (pass-through invoiced separately).
        updates["lump_sum_total"] = agency_revenue if agency_revenue > 0 else display_total
        if passthrough > 0:
            updates["client_media_passthrough"] = passthrough
            updates["total_client_invoicing"] = client_total
        protect = [
            v
            for v in (reimbursables, fees, passthrough, agency_revenue)
            if v and v > 0
        ]
        # Money prose must come from structured line items + fee totals.
        # Never rewrite freeform multi-$ phase lists in place (Complete-scan bug).
        raw_scope = cleaned.scope_summary or ""
        amounts = _parse_dollar_amounts(raw_scope)
        phase_bit = _phase_breakdown_from_lines(cleaned)
        if len(amounts) > 1 or phase_bit:
            scope = _strip_dollar_bearing_sentences(raw_scope)
            if phase_bit:
                scope = f"{scope} {phase_bit}".strip() if scope else phase_bit
        elif len(amounts) == 1:
            scope = _sync_narrative_total(raw_scope, display_total, protect=protect)
        else:
            scope = raw_scope
        scope = _rewrite_investment_sentence(
            scope,
            fees,
            reimbursables,
            display_total,
            passthrough=passthrough,
        )
        if scope != (cleaned.scope_summary or ""):
            updates["scope_summary"] = scope
        ql = _sync_qualifying_fee_language(
            cleaned.qualifying_language or "",
            fees=fees,
            direct=reimbursables,
            total=agency_revenue if agency_revenue > 0 else display_total,
        )
        ql = _scrub_unverified_benchmark_clients(ql)
        scope = _scrub_unverified_benchmark_clients(scope)
        if scope != (cleaned.scope_summary or ""):
            updates["scope_summary"] = scope
        if ql != (cleaned.qualifying_language or ""):
            updates["qualifying_language"] = ql
        synced_scope = _sync_labeled_fee_subtotal(
            updates.get("scope_summary", cleaned.scope_summary or ""), fees
        )
        synced_ql = _sync_labeled_fee_subtotal(
            updates.get("qualifying_language", cleaned.qualifying_language or ""),
            fees,
        )
        if synced_scope != (updates.get("scope_summary", cleaned.scope_summary or "")):
            updates["scope_summary"] = synced_scope
        if synced_ql != (
            updates.get("qualifying_language", cleaned.qualifying_language or "")
        ):
            updates["qualifying_language"] = synced_ql
    opt = (cleaned.option_term_notes or "").strip()
    if opt:
        # Internal jargon only — no topic/keyword rewrites of fee structure.
        opt2 = opt.replace("agency revenue estimate", "proposed fees")
        opt2 = opt2.replace("Agency revenue estimate", "Proposed fees")
        if agency_revenue > 0 and len(_parse_dollar_amounts(opt2)) == 1:
            opt2 = _sync_narrative_total(
                opt2,
                agency_revenue,
                protect=[reimbursables, passthrough]
                if reimbursables > 0 or passthrough > 0
                else None,
            )
        opt2 = _scrub_unverified_benchmark_clients(opt2)
        if opt2 != opt:
            updates["option_term_notes"] = opt2
    if not updates:
        return cleaned
    return cleaned.model_copy(update=updates)


def _client_line_label(item: BudgetLineItem) -> tuple[str, str]:
    """Return (delivery phase, deliverable label) for the client fee table.

    Phase comes from structured line data (category / line type) — never from
    keyword-guessing the description.
    """
    desc = (item.description or "").strip()
    # Strip internal source footnotes without keyword topic matching.
    if "*(Source:" in desc or "* (Source:" in desc:
        cut = desc.find("*(Source:")
        if cut < 0:
            cut = desc.find("* (Source:")
        if cut >= 0:
            desc = desc[:cut].strip().rstrip("*").strip()
    cat = (item.category or "").strip() or "Fees"

    phase = _phase_label_for_line(item)
    generic = not desc or desc.casefold() in {
        "budget line item",
        "labor",
        "fees",
        "fee",
    }
    if generic and item.named_person:
        role = (item.role_title or "Team").strip()
        desc = f"{role} — {item.named_person}"
    if not desc:
        desc = cat
    return phase, desc


def _phase_label_for_line(item: BudgetLineItem) -> str:
    """Phase column = structured category (or travel type). No keyword heuristics."""
    from app.services.proposal_budget_validation import infer_line_item_type

    if infer_line_item_type(item) == "direct_expense":
        return "Travel / Reimbursables"
    cat = (item.category or "").strip()
    if cat:
        return cat
    return "Fees"


def _scrub_unverified_benchmark_clients(text: str) -> str:
    """Drop unverified client name-drops from pricing framing (e.g. Lake Oswego)."""
    if not text:
        return text
    out = re.sub(r"(?i)\s*(?:and|,)\s*Lake\s+Oswego\b", "", text)
    out = re.sub(r"(?i)\bLake\s+Oswego\s+and\s+", "", out)
    out = re.sub(r"(?i)\bLake\s+Oswego\b", "verified public-sector engagements", out)
    out = re.sub(
        r"(?i)\bcomparable\s+Carbondale\s+and\s+verified public-sector engagements\s+projects\b",
        "comparable Carbondale public-sector marketing plan work",
        out,
    )
    out = re.sub(
        r"(?i)comparable\s+Carbondale\s+and\s+verified public-sector engagements\b",
        "comparable Carbondale public-sector marketing plan work",
        out,
    )
    return out


def render_budget_markdown(
    budget: ProposalBudget,
    *,
    rfp_text: str = "",
) -> str:
    """Client-facing budget: one total, phase/deliverable fee table, short terms."""
    budget = prepare_budget_for_client_display(budget)
    lines: list[str] = []
    fmt = (budget.budget_format or "").casefold()
    wants_form = fmt == "blended_rate_form" or rfp_wants_blended_pricing_form(rfp_text)
    strict_form = wants_form and rfp_forbids_quotation_form_changes(rfp_text)

    if wants_form:
        lines.append(
            render_pricing_proposal_form_markdown(budget, rfp_text=rfp_text).rstrip()
        )
        lines.append("")

    total = _canonical_client_total(budget)
    fees, direct = _professional_fees_and_direct(budget)
    passthrough = round(float(budget.client_media_passthrough or 0), 2)
    if total is not None:
        lines.append("## Proposed Investment")
        lines.append("")
        if fees > 0:
            lines.append(f"**Professional fees: {_usd(fees)}**")
        if direct > 0:
            lines.append(f"**Direct travel / reimbursables: {_usd(direct)}**")
        if passthrough > 0:
            lines.append(
                f"**Client media pass-through (net): {_usd(passthrough)}**"
            )
        lines.append(f"**Total proposed investment: {_usd(total)}**")
        if budget.pricing_tier:
            lines.append(
                f"Rates follow zö's Industry {budget.pricing_tier} pricing guide "
                f"for comparable municipal / education marketing engagements."
            )
        if passthrough > 0:
            lines.append(
                "Media placements billed as client pass-through at net — "
                "separate from professional fees."
            )
        lines.append("")

    scope = (budget.scope_summary or "").strip()
    if scope:
        # prepare_budget_for_client_display already rebuilt money prose from line items.
        if len(scope) > 700:
            cut = scope[:700]
            scope = cut.rsplit(".", 1)[0].strip() + "."
        lines.append(scope)
        lines.append("")

    ql = (budget.qualifying_language or "").strip()
    if ql:
        if len(ql) > 1600:
            ql = ql[:1600].rsplit(".", 1)[0].strip() + "."
        if strict_form:
            lines.append(
                "> Supporting terms only — not part of the official Pricing/Quotation form."
            )
            lines.append("")
        lines.append("## Terms")
        lines.append("")
        lines.append(ql)
        lines.append("")

    if budget.line_items:
        heading = (
            "## Fee Detail by Phase" if not wants_form else "## Supporting Fee Detail"
        )
        lines.append(heading)
        lines.append("")
        lines.append("| Phase | Deliverable | Amount |")
        lines.append("| --- | --- | ---: |")
        subtotal = 0.0
        for item in budget.line_items:
            phase, desc = _client_line_label(item)
            extended = _usd(item.extended) if item.extended is not None else "—"
            if isinstance(item.extended, (int, float)):
                subtotal += float(item.extended)
            lines.append(f"| {phase} | {desc} | {extended} |")
        direct = round(float(budget.direct_expenses_total or 0), 2)
        if direct > 0:
            lines.append(
                f"| Direct expenses | Travel / reimbursables | {_usd(direct)} |"
            )
        grand = round(subtotal + direct, 2)
        lines.append(f"| **Total** | | **{_usd(grand)}** |")
        lines.append("")

    opt = (budget.option_term_notes or "").strip()
    if opt:
        opt2 = opt.replace("agency revenue estimate", "proposed fees")
        opt2 = opt2.replace("Agency revenue estimate", "Proposed fees")
        # Incomplete fragments (one short line, one dollar) confuse reviewers — omit.
        amounts = _parse_dollar_amounts(opt2)
        if len(opt2) < 160 and len(amounts) <= 1:
            opt2 = ""
        elif len(opt2) > 500:
            opt2 = opt2[:500].rsplit(".", 1)[0].strip() + "."
        if opt2:
            lines.append("## Option Terms")
            lines.append(opt2)
            lines.append("")

    return "\n".join(lines).strip() + "\n"


def render_embedded_budget_table_markdown(budget: ProposalBudget) -> str:
    """Clean fee table for embedding in Compliance / narrative sections.

    Bold labels + one accurate table from the canonical budget. No [PRICING FLAG],
    no evidence markers, no internal Sonja notes.
    """
    budget = prepare_budget_for_client_display(budget)
    total = _canonical_client_total(budget)
    fees, direct = _professional_fees_and_direct(budget)
    passthrough = round(float(budget.client_media_passthrough or 0), 2)
    lines: list[str] = [
        "### Proposed Investment",
        "",
    ]
    if fees > 0:
        lines.append(f"**Professional fees:** {_usd(fees)}")
    if direct > 0:
        lines.append(f"**Direct travel / reimbursables:** {_usd(direct)}")
    if passthrough > 0:
        lines.append(f"**Client media pass-through (net):** {_usd(passthrough)}")
    if total is not None:
        lines.append(f"**Total proposed investment:** {_usd(total)}")
    if budget.pricing_tier:
        lines.append(
            f"Rates follow zö's Industry **{budget.pricing_tier}** pricing guide "
            "for comparable municipal marketing engagements."
        )
    lines.append("")
    if budget.line_items:
        lines.append("### Fee Detail by Phase")
        lines.append("")
        lines.append("| **Phase** | **Deliverable** | **Amount** |")
        lines.append("| --- | --- | ---: |")
        subtotal = 0.0
        for item in budget.line_items:
            phase, desc = _client_line_label(item)
            extended = _usd(item.extended) if item.extended is not None else "—"
            if isinstance(item.extended, (int, float)):
                subtotal += float(item.extended)
            lines.append(f"| {phase} | {desc} | {extended} |")
        if direct > 0:
            lines.append(
                f"| Direct expenses | Travel / reimbursables | {_usd(direct)} |"
            )
        grand = round(subtotal + direct, 2)
        lines.append(f"| **Total** | | **{_usd(grand)}** |")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


_EXISTING_BUDGET_BLOCK_RE = re.compile(
    r"(?is)(?:^|\n)("
    r"#{1,3}\s*(?:Proposed\s+Investment(?:\s*/\s*Fee\s+Table)?|"
    r"Fee\s+Detail(?:\s+by\s+Phase)?|Supporting\s+Fee\s+Detail)\b"
    r".*?"
    r")(?=(?:\n#{1,3}\s+)|\Z)"
)


def _strip_existing_investment_blocks(body: str) -> str:
    """Remove prior Proposed Investment / fee-detail / pricing-flag dumps."""
    text = re.sub(r"(?is)(?:\s*\[PRICING FLAG:[^\]]*\]\s*)+", "\n\n", body or "")
    text = _EXISTING_BUDGET_BLOCK_RE.sub("\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


def insert_budget_table_into_section(content: str, budget_markdown: str) -> tuple[str, str]:
    """Insert or replace ONLY the budget/fee table block — preserve all other prose.

    Also scrubs evidence markers and [PRICING FLAG] dumps from the section body.
    Returns (updated_content, action) where action is 'inserted' or 'replaced'.
    """
    from app.services.proposal_manuscript import scrub_client_facing_section_artifacts

    original = content or ""
    body = scrub_client_facing_section_artifacts(original)
    had_prior = bool(_EXISTING_BUDGET_BLOCK_RE.search(body)) or bool(
        re.search(r"\[PRICING FLAG:", original, re.I)
    )
    body = _strip_existing_investment_blocks(body)
    body = scrub_client_facing_section_artifacts(body)

    table_md = scrub_client_facing_section_artifacts(budget_markdown or "").strip()
    if not table_md:
        return body, "replaced" if had_prior else "inserted"

    fee_only = table_md
    fee_match = re.search(
        r"(?is)(#{1,3}\s*(?:Fee\s+Detail(?:\s+by\s+Phase)?|Supporting\s+Fee\s+Detail|"
        r"Proposed\s+Investment)\b.*)",
        table_md,
    )
    if fee_match:
        fee_only = fee_match.group(1).strip()

    block = "\n\n" + fee_only.strip() + "\n"
    action = "replaced" if had_prior else "inserted"

    budgets_heading = re.search(
        r"(?im)^(#{1,3}\s*BUDGETS?\b[^\n]*\n)",
        body,
    )
    if budgets_heading:
        start = budgets_heading.end(1)
        rest = body[start:]
        para_end = re.search(r"\n\n", rest)
        if para_end:
            insert_at = start + para_end.end()
            updated = body[:insert_at].rstrip() + block + body[insert_at:]
        else:
            updated = body[:start].rstrip() + block + rest
        updated = scrub_client_facing_section_artifacts(updated)
        return updated.strip() + ("\n" if original.endswith("\n") else ""), action

    updated = body.rstrip() + block
    updated = scrub_client_facing_section_artifacts(updated)
    return updated.strip() + ("\n" if original.endswith("\n") else ""), action


def canonical_budget_summary_figures(budget: ProposalBudget) -> dict[str, float]:
    """Distinct agency / pass-through / direct / total figures from the fee table."""
    from app.services.proposal_budget_validation import (
        direct_expense_subtotal,
        split_line_item_totals,
    )

    line_sum, agency_fee, passthrough = split_line_item_totals(budget.line_items or [])
    # Travel lives in lines XOR directExpensesTotal after dedupe (see
    # _professional_fees_and_direct). line_sum already includes travel_in_lines
    # (split_line_item_totals sums agency + passthrough + direct), so adding it
    # again to line_sum would double-count. agency_fee, by contrast, now
    # excludes travel entirely, so agency_revenue needs the full travel amount
    # (in-lines + explicit field) or it would silently drop travel that used to
    # be folded into the old (buggy) agency_fee.
    travel_in_lines = direct_expense_subtotal(budget.line_items or [])
    explicit_direct = round(float(budget.direct_expenses_total or 0), 2)
    direct = round(travel_in_lines + explicit_direct, 2)
    if agency_fee <= 0 and budget.agency_fee_subtotal is not None:
        agency_fee = round(float(budget.agency_fee_subtotal), 2)
    if passthrough <= 0 and budget.client_media_passthrough is not None:
        passthrough = round(float(budget.client_media_passthrough), 2)
    agency_fee = round(float(agency_fee or 0), 2)
    passthrough = round(float(passthrough or 0), 2)
    agency_revenue = budget.agency_revenue_estimate
    if agency_revenue is None or float(agency_revenue) <= 0:
        agency_revenue = round(agency_fee + direct, 2)
    else:
        agency_revenue = round(float(agency_revenue), 2)
    total = budget.total_client_invoicing
    if total is None or float(total) <= 0:
        if line_sum > 0:
            total = round(float(line_sum) + explicit_direct, 2)
        else:
            total = round(agency_fee + passthrough + direct, 2)
    else:
        total = round(float(total), 2)
    return {
        "agency_fee": agency_fee,
        "agency_revenue": agency_revenue,
        "passthrough": passthrough,
        "direct": direct,
        "total": total,
        "line_sum": round(float(line_sum or 0), 2),
    }


_YEAR1_INVESTMENT_BLOCK_RE = re.compile(
    r"(?is)"
    r"Total\s+Year\s*1\s+agency\s+fee\s*:[^\n]*?"
    r"(?:Total\s+Year\s*1\s+client\s+invoicing\s*:[^\n.]*)"
    r"(?:\.\s*)?"
    r"(?:\s*\d{1,3}\s*\(\$[\d,]+(?:\.\d+)?\.?\s*)?"
)

_GARBLED_DOLLAR_TAIL_RE = re.compile(
    r"\s+\d{1,3}\s*\(\$[\d,]+(?:\.\d+)?\.?\s*$",
    re.M,
)


def reconcile_budget_summary_prose(
    content: str,
    budget: ProposalBudget,
) -> tuple[str, int]:
    """Rewrite duplicated/garbled investment summary sentences from canonical figures.

    Does not touch fee-table rows — only narrative labels (Year 1 summary, Option
    Terms, pass-through statements).
    """
    text = content or ""
    if not text.strip():
        return text, 0
    figs = canonical_budget_summary_figures(budget)
    # agency_revenue is only a stand-in for a MISSING fee (no line items, no
    # stored subtotal). A zero fee next to real travel is a true zero — an
    # all-travel budget — and substituting agency_revenue there reprints the
    # travel dollars as "Total Year 1 agency fee", which is exactly the
    # fee == travel == total sentence this task exists to eliminate.
    agency = figs["agency_fee"]
    if agency <= 0 and figs["direct"] <= 0:
        agency = figs["agency_revenue"]
    passthrough = figs["passthrough"]
    direct = figs["direct"]
    total = figs["total"]
    if agency <= 0 and total <= 0:
        return text, 0

    changes = 0
    year1_block = (
        f"Total Year 1 agency fee: {_usd(agency)}. "
        f"Client media pass-through billed at net: {_usd(passthrough)}. "
        f"Direct travel/reimbursables: {_usd(direct)}. "
        f"Total Year 1 client invoicing: {_usd(total)}."
    )

    def _year1_sub(match: re.Match[str]) -> str:
        nonlocal changes
        prior = match.group(0)
        if prior.strip() == year1_block:
            return prior
        changes += 1
        return year1_block

    out = _YEAR1_INVESTMENT_BLOCK_RE.sub(_year1_sub, text)

    # Label-by-label fixes when the Year 1 block regex did not fire. Connector
    # accepts a colon OR natural sentence phrasing ("Agency fee is $X") — colon-only
    # let sentences like "Year 1 agency revenue is $325,242.66" (a mislabeled
    # figure copying the grand total) through untouched; see proposal_budget_sync
    # for the matching fix in the detector this auto-fixer complements.
    _connector = r"(?:\s*:\s*|\s+(?:is|are|was|equals?|totals?|comes?\s+to|amounts?\s+to)\s+)"
    label_specs: list[tuple[str, float]] = [
        (
            r"(Total\s+Year\s*1\s+agency\s+fee|Total\s+agency\s+(?:fee|revenue)|"
            r"Agency\s+(?:fee|revenue)(?:\s+estimate)?)"
            + _connector
            + r"\$[\d,]+(?:\.\d{2})?",
            agency,
        ),
        (
            r"(Client\s+media\s+pass-?through(?:\s*\([^)]*\))?)"
            + _connector
            + r"\$[\d,]+(?:\.\d{2})?",
            passthrough,
        ),
        (
            r"(Direct\s+travel\s*/\s*reimbursables|Direct\s+travel|"
            r"Estimated\s+reimbursable\s+travel)"
            + _connector
            + r"\$[\d,]+(?:\.\d{2})?",
            direct,
        ),
        (
            r"(Total\s+Year\s*1\s+client\s+invoicing|Total\s+client\s+invoicing|"
            r"Total\s+Year\s*1\s+investment|Total\s+proposed\s+investment|"
            r"Grand\s+total\s+client\s+invoicing)"
            + _connector
            + r"\$[\d,]+(?:\.\d{2})?",
            total,
        ),
        (
            r"(Base-year\s+proposed\s+fees)" + _connector + r"\$[\d,]+(?:\.\d{2})?",
            agency,
        ),
    ]
    for pattern, amount in label_specs:
        def _repl(match: re.Match[str], amt: float = amount) -> str:
            nonlocal changes
            label = match.group(1)
            new = f"{label}: {_usd(amt)}"
            if match.group(0) != new:
                changes += 1
            return new

        out2 = re.sub(pattern, _repl, out, flags=re.I)
        out = out2

    # Strip trailing generation garbage like "66 ($325,242."
    cleaned = _GARBLED_DOLLAR_TAIL_RE.sub("", out)
    if cleaned != out:
        changes += 1
        out = cleaned

    return out, changes


def reconcile_draft_budget_summaries(
    draft: ProposalDraft,
    budget: ProposalBudget,
) -> tuple[ProposalDraft, int]:
    """Apply summary-prose reconcile across every section that mentions investment totals."""
    sections: list[ProposalSection] = []
    total_changes = 0
    for section in draft.sections:
        body = section.content or ""
        # Touch budget tabs always; other tabs only when Year-1 / pass-through labels exist.
        looks_relevant = section_is_budgetish(section) or bool(
            re.search(
                r"(?i)Year\s*1\s+agency\s+fee|client\s+media\s+pass-?through|"
                r"total\s+client\s+invoicing|Base-year\s+proposed\s+fees",
                body,
            )
        )
        if not looks_relevant:
            sections.append(section)
            continue
        new_body, n = reconcile_budget_summary_prose(body, budget)
        if n > 0 and new_body != body:
            total_changes += n
            sections.append(
                section.model_copy(update={"content": new_body, "status": "generated"})
            )
        else:
            sections.append(section)
    if total_changes <= 0:
        return draft, 0
    now = datetime.now(timezone.utc).isoformat()
    return draft.model_copy(update={"sections": sections, "updated_at": now}), total_changes


def ensure_budget_section_present(
    sections: list[ProposalSection],
    budget: ProposalBudget | None,
    *,
    rfp_text: str = "",
) -> tuple[list[ProposalSection], bool]:
    """If the fee tab is missing but canon budget exists, append Budget & Pricing.

    Used after Senior Editor / Scan compact so dedupe or delete tickets cannot
    permanently erase a token-expensive regenerated fee table.
    """
    if find_budget_section_index(sections) is not None:
        return sections, False
    if budget is None:
        return sections, False
    try:
        from app.services.proposal_fulfill_rfp_budget_kpi import (
            pricing_model_lacks_professional_fees,
        )

        if pricing_model_lacks_professional_fees(budget):
            return sections, False
    except Exception:  # noqa: BLE001
        # If the check cannot run, still restore when a budget object exists.
        pass
    content = render_budget_markdown(budget, rfp_text=rfp_text)
    if not (content or "").strip():
        return sections, False
    restored = list(sections) + [
        ProposalSection(
            id="section-budget-pricing",
            title="Budget & Pricing",
            content=content,
            status="generated",
            source="generated",
            mode="write",
            word_target=900,
            required=True,
        )
    ]
    return restored, True


def section_is_budgetish(section: ProposalSection) -> bool:
    return budget_section_score(section.title or "") > 0


def reshape_budget_for_rfp_form(
    draft: ProposalDraft,
    budget: ProposalBudget | None,
    *,
    rfp_text: str,
) -> ProposalDraft | None:
    """If THIS RFP wants a 3-rate form, rewrite Budget to lead with that form.

    Never overwrite an already-filled official RFQ / Quotation pricing form tab —
    that is how Complete & Clean wiped DuPage contact fields into [Contact Name].
    """
    if not budget or not rfp_wants_blended_pricing_form(rfp_text):
        return None
    idx = find_budget_section_index(draft.sections)
    if idx is None:
        return None
    target = draft.sections[idx]
    if section_looks_like_official_pricing_form(target) and official_pricing_form_is_filled(
        target.content or ""
    ):
        return None
    updated_budget = budget.model_copy(update={"budget_format": "blended_rate_form"})
    content = render_budget_markdown(updated_budget, rfp_text=rfp_text)
    sections = list(draft.sections)
    sections[idx] = sections[idx].model_copy(
        update={"content": content, "status": "generated"}
    )
    form_md = render_pricing_proposal_form_markdown(updated_budget, rfp_text=rfp_text)
    for i, section in enumerate(sections):
        title = (section.title or "").casefold()
        if section.id == "rfp-closing-pricing-form" or "pricing proposal form" in title:
            if section_looks_like_official_pricing_form(section) and official_pricing_form_is_filled(
                section.content or ""
            ):
                continue
            sections[i] = section.model_copy(
                update={"content": form_md, "status": "generated"}
            )
            break
    now = datetime.now(timezone.utc).isoformat()
    return draft.model_copy(update={"sections": sections, "updated_at": now})


async def incorporate_budget_into_draft(
    rfp_id: str,
    budget: ProposalBudget,
    *,
    rfp_text: str = "",
) -> ProposalDraft | None:
    """Write generated budget into the best-matching proposal section (or append one).

    Never overwrite a filled official RFQ / Quotation Pricing Form — Phase 3.5 /
    Continue Proposal was wiping DuPage contact fields by treating that tab as
    the Budget & Pricing write target.
    """
    draft = await aget_proposal_draft(rfp_id)
    if not draft:
        return None

    content = render_budget_markdown(budget, rfp_text=rfp_text)
    now = datetime.now(timezone.utc).isoformat()
    sections = list(draft.sections)
    idx = find_budget_section_index(sections)

    if idx is not None:
        target = sections[idx]
        if section_looks_like_official_pricing_form(target) and official_pricing_form_is_filled(
            target.content or ""
        ):
            # Keep the buyer form; write narrative into Budget & Pricing sibling.
            narrative_idx = next(
                (
                    i
                    for i, s in enumerate(sections)
                    if not section_looks_like_official_pricing_form(s)
                    and budget_section_score(s.title or "") >= 4
                ),
                None,
            )
            if narrative_idx is not None:
                sections[narrative_idx] = sections[narrative_idx].model_copy(
                    update={"content": content, "status": "generated"}
                )
            else:
                sections.append(
                    ProposalSection(
                        id="section-budget-pricing",
                        title="Budget & Pricing",
                        content=content,
                        status="generated",
                        source="generated",
                        mode="write",
                        word_target=900,
                        required=True,
                    )
                )
        else:
            sections[idx] = sections[idx].model_copy(
                update={"content": content, "status": "generated"}
            )
    else:
        sections.append(
            ProposalSection(
                id="section-budget-pricing",
                title="Budget & Pricing",
                content=content,
                status="generated",
                source="generated",
                mode="write",
                word_target=900,
                required=True,
            )
        )

    updated = draft.model_copy(update={"sections": sections, "updated_at": now})
    await asave_proposal_draft(updated)
    return updated


_PHASE_FEE_TABLE_HEADER_RE = re.compile(
    r"(?im)^\|[^\n]*(?:\bphase\b|\bmilestone\b|\bdeliverable\b)[^\n]*\|"
    r"[^\n]*(?:amount|fee|cost|\$)[^\n]*\|"
)

_INVENTED_PHASE_TABLE_BLOCK_RE = re.compile(
    r"(?is)(?:^|\n)("
    r"(?:#{1,4}\s*(?:"
    r"Fee\s+Detail(?:\s+by\s+Phase)?|"
    r"Disbursement(?:\s+Schedule)?|"
    r"Budget\s+Allocation|"
    r"Milestone\s+(?:Payment|Disbursement)|"
    r"Payment\s+Schedule"
    r")\b[^\n]*\n)?"
    r"(?:[^\n|]*\n)?"
    r"(?:\|[^\n]+\|\n)+"
    r")"
)

_BUDGET_PHASE_CROSS_REF = (
    "\n\n> **Fee detail by phase:** See the **Budget & Pricing** section for the "
    "canonical milestone and fee breakdown.\n"
)


def _body_has_invented_phase_fee_table(content: str) -> bool:
    """True when section body contains a phase/milestone dollar table not from canon."""
    text = content or ""
    if not text.strip():
        return False
    if _PHASE_FEE_TABLE_HEADER_RE.search(text):
        return True
    if re.search(r"(?i)fee detail by phase", text) and re.search(r"\$\s*[\d,]+", text):
        return True
    if (
        re.search(r"(?i)(?:disbursement|budget allocation|milestone payment)", text)
        and text.count("|") >= 4
        and re.search(r"\$\s*[\d,]+", text)
    ):
        return True
    return False


def _strip_invented_phase_fee_tables(content: str) -> str:
    """Remove LLM-invented phase/disbursement markdown tables from narrative sections."""
    text = content or ""
    if not text.strip():
        return text
    cleaned = _INVENTED_PHASE_TABLE_BLOCK_RE.sub("\n", text)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def render_disbursement_schedule_markdown(budget: ProposalBudget) -> str:
    """Milestone disbursement table from canonical budget line items — one source of truth."""
    budget = prepare_budget_for_client_display(budget)
    total = _canonical_client_total(budget)
    lines: list[str] = [
        "### Disbursement Schedule",
        "",
        "| **Phase / Milestone** | **Amount** |",
        "| --- | ---: |",
    ]
    subtotal = 0.0
    for item in budget.line_items or []:
        phase, _desc = _client_line_label(item)
        amount = item.extended
        if isinstance(amount, (int, float)):
            subtotal += float(amount)
        lines.append(f"| {phase} | {_usd(amount)} |")
    fees, direct = _professional_fees_and_direct(budget)
    if direct > 0 and not any(_line_looks_like_travel(i) for i in (budget.line_items or [])):
        lines.append(f"| Direct expenses | {_usd(direct)} |")
        subtotal += direct
    grand = round(subtotal, 2)
    if total is not None and abs(grand - float(total)) > 1.0:
        grand = round(float(total), 2)
    lines.append(f"| **Total** | **{_usd(grand)}** |")
    lines.append("")
    return "\n".join(lines).strip() + "\n"


def _section_wants_canonical_phase_table(section: ProposalSection) -> bool:
    """Budget-adjacent tabs that must show the same phase dollars as the canon budget."""
    if section_is_budgetish(section):
        return True
    title = (section.title or "").casefold()
    sid = (section.id or "").casefold()
    keys = (
        "disbursement",
        "allocation",
        "payment schedule",
        "milestone",
        "fee detail",
        "budget",
    )
    return any(k in title or k in sid for k in keys)


def _canonical_table_for_section(section: ProposalSection, budget: ProposalBudget) -> str:
    title = (section.title or "").casefold()
    if "disbursement" in title or "payment schedule" in title or "milestone" in title:
        return render_disbursement_schedule_markdown(budget)
    return render_embedded_budget_table_markdown(budget)


def sync_phase_budget_tables_across_draft(
    draft: ProposalDraft,
    budget: ProposalBudget,
) -> tuple[ProposalDraft, list[str]]:
    """Replace invented phase-$ tables in sibling sections with the canonical budget.

    Phase 3 drafts each section independently, so Disbursement / Fee Detail / Budget
    Allocation often invent different phase splits that still sum to the same total.
    After Phase 3.5 freezes ProposalBudget, this overwrites those tables everywhere.
    """
    if not draft.sections or not budget.line_items:
        return draft, []

    canon_idx = find_budget_section_index(draft.sections)
    logs: list[str] = []
    updated_sections: list[ProposalSection] = []

    for i, section in enumerate(draft.sections):
        body = section.content or ""
        if i == canon_idx or not _body_has_invented_phase_fee_table(body):
            updated_sections.append(section)
            continue

        if _section_wants_canonical_phase_table(section):
            stripped = _strip_invented_phase_fee_tables(body)
            table_md = _canonical_table_for_section(section, budget)
            title_cf = (section.title or "").casefold()
            if "disbursement" in title_cf or "payment schedule" in title_cf:
                new_body = stripped.rstrip() + "\n\n" + table_md.strip() + "\n"
                action = "replaced" if stripped != body else "inserted"
            else:
                new_body, action = insert_budget_table_into_section(stripped, table_md)
            if new_body != body:
                logs.append(
                    f"{section.title or section.id}: synced canonical phase table ({action})"
                )
                section = section.model_copy(update={"content": new_body})
        else:
            stripped = _strip_invented_phase_fee_tables(body)
            if stripped != body:
                new_body = stripped.rstrip() + _BUDGET_PHASE_CROSS_REF
                logs.append(
                    f"{section.title or section.id}: removed invented phase $ table → cross-ref"
                )
                section = section.model_copy(update={"content": new_body})

        updated_sections.append(section)

    if not logs:
        return draft, logs
    return draft.model_copy(update={"sections": updated_sections}), logs
