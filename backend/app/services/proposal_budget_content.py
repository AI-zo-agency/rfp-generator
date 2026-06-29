"""Render Stage 3 budget into proposal section content and sync to draft."""

from __future__ import annotations

import re
from datetime import datetime, timezone

from app.models.proposal import ProposalBudget, ProposalDraft, ProposalSection
from app.services.proposal_repository import get_proposal_draft, save_proposal_draft

_BUDGET_TITLE_PATTERN = re.compile(
    r"\b(budget|pricing|price\s*proposal|fee\s*schedule|cost\s*proposal|compensation)\b",
    re.I,
)


def _usd(value: float | None) -> str:
    if value is None:
        return "—"
    return f"${value:,.0f}"


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


def render_budget_markdown(budget: ProposalBudget) -> str:
    lines: list[str] = []

    if budget.qualifying_language.strip():
        lines.append(budget.qualifying_language.strip())
        lines.append("")

    lines.append("## Budget Summary")
    summary_rows: list[str] = []
    if budget.rfp_budget_cap is not None:
        summary_rows.append(f"- **RFP budget cap:** {_usd(budget.rfp_budget_cap)}")
    if budget.agency_revenue_estimate is not None:
        summary_rows.append(
            f"- **Agency revenue estimate:** {_usd(budget.agency_revenue_estimate)}"
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
        lines.append("## Budget Line Items")
        lines.append("")
        lines.append("| Category | Description | Qty | Unit | Rate | Extended |")
        lines.append("| --- | --- | ---: | --- | ---: | ---: |")
        for item in budget.line_items:
            desc = item.description
            if item.named_person:
                role = item.role_title or item.description
                desc = f"{role} — {item.named_person}"
            if item.rate_source:
                desc = f"{desc} *(Source: {item.rate_source})*"
            qty = (
                f"{item.quantity:g}"
                if item.quantity is not None
                else "—"
            )
            rate = _usd(item.rate) if item.rate is not None else "—"
            extended = _usd(item.extended) if item.extended is not None else "—"
            lines.append(
                f"| {item.category} | {desc} | {qty} | {item.unit} | {rate} | {extended} |"
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
        lines.append("## Media Spend")
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
        lines.append(
            f"[DESIGNER NOTE: {budget.design_brief.strip()}]"
        )
        lines.append("")

    return "\n".join(lines).strip()


def incorporate_budget_into_draft(
    rfp_id: str,
    budget: ProposalBudget,
) -> ProposalDraft | None:
    """Write generated budget into the best-matching proposal section (or append one)."""
    draft = get_proposal_draft(rfp_id)
    if not draft:
        return None

    content = render_budget_markdown(budget)
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
                id=f"section-budget-pricing",
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
    save_proposal_draft(updated)
    return updated
