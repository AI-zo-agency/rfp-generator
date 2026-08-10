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
        r"acknowledgements?|cover\s+letter|case\s+stud|references?"
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


def find_budget_section_index(sections: list[ProposalSection]) -> int | None:
    best_idx: int | None = None
    best_score = 0
    for i, section in enumerate(sections):
        score = budget_section_score(section.title)
        if score > best_score:
            best_score = score
            best_idx = i
    return best_idx if best_score > 0 else None



_DOLLAR_RE = re.compile(r"\$[\d,]+(?:\.\d{1,2})?")
_INTERNAL_OPT_RE = re.compile(
    r"(?i)\bagency\s+revenue\s+estimate\b|\bmargin\b|\bsonja\b|\b00_guide_pricing\b"
)
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


def _sync_narrative_total(
    text: str,
    canonical: float,
    *,
    protect: list[float] | None = None,
) -> str:
    """Replace stale investment totals — never rewrite protected reimbursable amounts."""
    if not text or canonical <= 0:
        return text
    amounts = _DOLLAR_RE.findall(text)
    if not amounts:
        return text
    protected = {round(float(p), 2) for p in (protect or []) if p and float(p) > 0}
    canon_s = _usd(canonical)
    out = text
    for amt in amounts:
        raw = amt.replace("$", "").replace(",", "")
        try:
            val = float(raw)
        except ValueError:
            continue
        if any(abs(val - p) <= 1.0 for p in protected):
            continue
        if abs(val - canonical) > 1.0:
            out = out.replace(amt, canon_s, 1)
    return out


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
    # Replace common stale framing like "$73,500 in professional fees, plus … $7,500"
    text = re.sub(
        r"(?i)(?:proposed\s+investment\s+of\s+)?\$[\d,]+(?:\.\d{2})?\s+in\s+professional\s+fees"
        r"(?:\s*,?\s*plus(?:\s+an\s+estimated)?\s+\$[\d,]+(?:\.\d{2})?\s+in\s+reimbursable\s+travel)?",
        (
            f"proposed investment of {_usd(fees)} in professional fees"
            + (f", plus {_usd(direct)} in reimbursable travel" if direct > 0 else "")
        ),
        text,
        count=1,
    )
    return text


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
        scope = _sync_narrative_total(
            cleaned.scope_summary or "",
            display_total,
            protect=protect,
        )
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
    opt = (cleaned.option_term_notes or "").strip()
    if opt:
        opt2 = re.sub(
            r"(?i)base-year\s+agency\s+revenue\s+estimate\s*:",
            "Base-year proposed fees:",
            opt,
        )
        opt2 = _INTERNAL_OPT_RE.sub("proposed fees", opt2)
        if agency_revenue > 0:
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

    Prefer Phase 1–4 labels from the description so the table matches the
    narrative / Alignment Matrix — not raw guide category names like
    "Implementation & Launch" or "Brand Identity & Creative".
    """
    desc = (item.description or "").strip()
    desc = re.sub(r"\s*\*\(?Source:[^*]+\)?\*", "", desc, flags=re.I).strip()
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


_PHASE_NUM_RE = re.compile(r"\bphase\s*([1-4])\b", re.I)


def _phase_label_for_line(item: BudgetLineItem) -> str:
    """Map a line item to KVCC-style Phase 1–4 (or PM / Travel) for the fee table."""
    blob = f"{item.description or ''} {item.category or ''} {item.notes or ''}"
    if re.search(r"\b(travel|airfare|lodging|reimbursable|per\s*diem)\b", blob, re.I):
        return "Travel / Reimbursables"
    if re.search(r"\bproject\s+management\b|\baccount\s+management\b", blob, re.I):
        return "Project Management"

    m = _PHASE_NUM_RE.search(blob)
    if m:
        return {
            "1": "Phase 1 — Discovery",
            "2": "Phase 2 — Strategy",
            "3": "Phase 3 — Tactical Plan",
            "4": "Phase 4 — Roadmap & Handoff",
        }.get(m.group(1), f"Phase {m.group(1)}")

    if re.search(r"\b(roadmap|handoff|strategic\s+plan\s+document)\b", blob, re.I):
        return "Phase 4 — Roadmap & Handoff"
    if re.search(
        r"\b(tactical|digital\s+campaign|social\s+media|content\s+strategy|"
        r"earned\s+media|brand\s+messaging\s+toolkit|pr\s*&?\s*earned)\b",
        blob,
        re.I,
    ):
        return "Phase 3 — Tactical Plan"
    if re.search(
        r"\b(messaging\s+framework|competitive\s+positioning|kpi\s+development|"
        r"brand\s+narrative)\b",
        blob,
        re.I,
    ):
        return "Phase 2 — Strategy"
    if re.search(
        r"\b(discovery|stakeholder|listening\s+session|brand\s+audit|"
        r"market\s+research|audience\s+segmentation|persona)\b",
        blob,
        re.I,
    ):
        return "Phase 1 — Discovery"
    return (item.category or "").strip() or "Fees"


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
        if total is not None:
            scope = _sync_narrative_total(
                scope,
                total,
                protect=[v for v in (direct, fees, passthrough) if v > 0],
            )
            scope = _rewrite_investment_sentence(
                scope,
                fees,
                direct,
                total,
                passthrough=passthrough,
            )
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
        opt2 = re.sub(
            r"(?i)base-year\s+agency\s+revenue\s+estimate\s*:",
            "Base-year proposed fees:",
            opt,
        )
        opt2 = _INTERNAL_OPT_RE.sub("proposed fees", opt2)
        if total is not None:
            opt2 = _sync_narrative_total(
                opt2,
                total,
                protect=[direct] if direct > 0 else None,
            )
        opt2 = opt2.strip()
        if opt2 and len(opt2) > 500:
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


def section_is_budgetish(section: ProposalSection) -> bool:
    return budget_section_score(section.title or "") > 0


def reshape_budget_for_rfp_form(
    draft: ProposalDraft,
    budget: ProposalBudget | None,
    *,
    rfp_text: str,
) -> ProposalDraft | None:
    """If THIS RFP wants a 3-rate form, rewrite Budget to lead with that form."""
    if not budget or not rfp_wants_blended_pricing_form(rfp_text):
        return None
    idx = find_budget_section_index(draft.sections)
    if idx is None:
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
    """Write generated budget into the best-matching proposal section (or append one)."""
    draft = await aget_proposal_draft(rfp_id)
    if not draft:
        return None

    content = render_budget_markdown(budget, rfp_text=rfp_text)
    now = datetime.now(timezone.utc).isoformat()
    sections = list(draft.sections)
    idx = find_budget_section_index(sections)

    if idx is not None:
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
