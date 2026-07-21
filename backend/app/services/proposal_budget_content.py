"""Render Stage 3 budget into proposal section content and sync to draft."""

from __future__ import annotations

import re
from datetime import datetime, timezone

from app.models.proposal import ProposalBudget, ProposalDraft, ProposalSection
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


def budget_section_score(title: str) -> int:
    t = title.lower()
    score = 0
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


def render_budget_markdown(
    budget: ProposalBudget,
    *,
    rfp_text: str = "",
) -> str:
    lines: list[str] = []
    fmt = (budget.budget_format or "").casefold()
    wants_form = fmt == "blended_rate_form" or rfp_wants_blended_pricing_form(rfp_text)
    strict_form = wants_form and rfp_forbids_quotation_form_changes(rfp_text)

    if wants_form:
        lines.append(
            render_pricing_proposal_form_markdown(budget, rfp_text=rfp_text).rstrip()
        )
        lines.append("")

    if budget.qualifying_language.strip() and not strict_form:
        lines.append(budget.qualifying_language.strip())
        lines.append("")
    elif budget.qualifying_language.strip() and strict_form:
        lines.append(
            "> **Supporting narrative only** — keep qualifying language off the official "
            "Pricing/Quotation form per RFP disqualification language."
        )
        lines.append("")

    supporting_title = (
        "## Supporting Budget Rationale (separate from the official Pricing Proposal Form)"
        if strict_form
        else ("## Supporting Budget Build" if wants_form else "## Budget Summary")
    )
    if not wants_form:
        lines.append(supporting_title)
    elif strict_form:
        lines.append(supporting_title)
        if budget.qualifying_language.strip():
            lines.append(budget.qualifying_language.strip())
            lines.append("")
    elif wants_form:
        pass  # summary block uses Supporting Budget Build below

    if wants_form and not strict_form:
        summary_heading = "## Supporting Budget Build"
    elif not wants_form:
        summary_heading = "## Budget Summary"
    else:
        summary_heading = None

    if summary_heading:
        lines.append(summary_heading)
    summary_rows: list[str] = []
    if budget.rfp_budget_cap is not None:
        summary_rows.append(f"- **RFP budget cap:** {_usd(budget.rfp_budget_cap)}")
    if budget.agency_revenue_estimate is not None:
        summary_rows.append(
            f"- **Agency revenue estimate (zö fee income only):** "
            f"{_usd(budget.agency_revenue_estimate)}"
        )
    if budget.agency_fee_subtotal is not None and budget.client_media_passthrough:
        summary_rows.append(
            f"- **Agency fee subtotal (excl. pass-through):** {_usd(budget.agency_fee_subtotal)}"
        )
    if budget.client_media_passthrough is not None and budget.client_media_passthrough > 0:
        summary_rows.append(
            f"- **Client media pass-through (at net, not agency revenue):** "
            f"{_usd(budget.client_media_passthrough)}"
        )
    if budget.commission_rate is not None and budget.commission_rate > 0:
        pct = budget.commission_rate * 100 if budget.commission_rate <= 1 else budget.commission_rate
        summary_rows.append(f"- **Commission rate:** {pct:g}%")
    if budget.total_client_invoicing is not None and budget.client_media_passthrough:
        summary_rows.append(
            f"- **Total estimated client invoicing (media + agency fees):** "
            f"{_usd(budget.total_client_invoicing)}"
        )
    if budget.line_item_sum is not None:
        summary_rows.append(f"- **Line item table total:** {_usd(budget.line_item_sum)}")
    if budget.lump_sum_total is not None:
        summary_rows.append(f"- **Lump sum (base term):** {_usd(budget.lump_sum_total)}")
    if budget.direct_expenses_total is not None:
        summary_rows.append(
            f"- **Direct expenses:** {_usd(budget.direct_expenses_total)}"
        )
    if budget.pricing_tier:
        summary_rows.append(f"- **Pricing tier:** {budget.pricing_tier}")
    if budget.fee_structure:
        summary_rows.append(f"- **Fee structure:** {budget.fee_structure}")
    if budget.budget_format:
        summary_rows.append(
            f"- **Budget format:** {budget.budget_format.replace('_', ' ')}"
        )
    if budget.commission_model:
        summary_rows.append(f"- **Commission model:** {budget.commission_model}")
    lines.extend(summary_rows or ["- *(See line items below.)*"])
    lines.append("")

    if budget.rfp_budget_notes.strip():
        lines.append(budget.rfp_budget_notes.strip())
        lines.append("")

    if budget.scope_summary.strip():
        lines.append("## Scope Summary")
        lines.append(budget.scope_summary.strip())
        lines.append("")

    if budget.tiers:
        lines.append("## Pricing Tiers")
        for tier in budget.tiers:
            marker = " *(recommended)*" if tier.id == budget.recommended_tier_id else ""
            total = _usd(tier.total) if tier.total is not None else "—"
            lines.append(f"### {tier.name}{marker} — {total}")
            if tier.rationale.strip():
                lines.append(tier.rationale.strip())
            lines.append("")

    if budget.line_items:
        heading = (
            "## Supporting Line Items (not a substitute for the Pricing Proposal Form)"
            if wants_form
            else "## Budget Line Items"
        )
        lines.append(heading)
        lines.append("")
        lines.append("| Category | Description | Qty | Unit | Rate | Extended |")
        lines.append("| --- | --- | ---: | --- | ---: | ---: |")
        subtotal = 0.0
        for item in budget.line_items:
            desc = item.description
            if item.named_person:
                role = item.role_title or item.description
                desc = f"{role} — {item.named_person}"
            if item.rate_source:
                desc = f"{desc} *(Source: {item.rate_source})*"
            qty = f"{item.quantity:g}" if item.quantity is not None else "—"
            rate = _usd(item.rate) if item.rate is not None else "—"
            extended = _usd(item.extended) if item.extended is not None else "—"
            if isinstance(item.extended, (int, float)):
                subtotal += float(item.extended)
            lines.append(
                f"| {item.category} | {desc} | {qty} | {item.unit} | {rate} | {extended} |"
            )
        direct = float(budget.direct_expenses_total or 0)
        lines.append(f"| **Subtotal** | *Sum of line items* | | | | **{_usd(subtotal)}** |")
        if direct > 0:
            lines.append(f"| **Direct expenses** | | | | | **{_usd(direct)}** |")
        if budget.client_media_passthrough and budget.client_media_passthrough > 0:
            agency_only = float(
                budget.agency_fee_subtotal or (subtotal - budget.client_media_passthrough)
            )
            lines.append(
                f"| **Agency fee subtotal** | *Excludes client pass-through* | | | | **{_usd(agency_only)}** |"
            )
            lines.append(
                f"| **Total agency revenue** | *Agency fees + direct expenses* | | | | "
                f"**{_usd(budget.agency_revenue_estimate or agency_only + direct)}** |"
            )
        else:
            lines.append(
                f"| **Total (agency revenue)** | *Line items + direct expenses* | | | | "
                f"**{_usd(subtotal + direct)}** |"
            )
        lines.append("")

    if budget.verified_rates:
        lines.append("## Verified Rates")
        lines.append("")
        lines.append("| Person | Role | Rate/hr | Source |")
        lines.append("| --- | --- | ---: | --- |")
        for row in budget.verified_rates:
            rate = _usd(row.hourly_rate) if row.hourly_rate is not None else "—"
            lines.append(f"| {row.person_name} | {row.role} | {rate} | {row.source} |")
        lines.append("")

    if budget.media_spend_notes.strip():
        lines.append("## Media Spend Notes")
        lines.append(budget.media_spend_notes.strip())
        lines.append("")

    if budget.option_term_notes.strip():
        lines.append("## Option Terms")
        lines.append(budget.option_term_notes.strip())
        lines.append("")

    if budget.scope_adjustments:
        lines.append("## Scope Adjustments")
        for note in budget.scope_adjustments:
            lines.append(f"- {note}")
        lines.append("")

    if budget.pricing_flags:
        lines.append("## Pricing Flags")
        for flag in budget.pricing_flags:
            lines.append(f"- {flag}")
        lines.append("")

    if budget.design_brief.strip():
        lines.append(f"[DESIGNER NOTE: {budget.design_brief.strip()}]")
        lines.append("")

    return "\n".join(lines).strip() + "\n"


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
