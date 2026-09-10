"""Render Stage 3 budget into proposal section content and sync to draft."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from app.models.proposal import BudgetLineItem, ProposalBudget, ProposalDraft, ProposalSection
from app.services.proposal_repository import aget_proposal_draft, asave_proposal_draft
from app.services.proposal_rfp_excerpt import rfp_forbids_quotation_form_changes

logger = logging.getLogger(__name__)

_BUDGET_TITLE_PATTERN = re.compile(
    r"\b(budget|pricing|price\s*proposal|fee\s*schedule|cost\s*proposal|compensation)\b",
    re.I,
)

# Assistive windowing for role-label scrape when budgetFormat is already
# personnel_loading — NOT Cost-format authority (pricing agent owns that).
_PERSONNEL_LOADING_WINDOW_RE = re.compile(
    r"(?:"
    r"hourly\s+rate(?:s)?\s+(?:for|by|across|table|schedule|form)"
    r"|rate(?:s)?\s+(?:for|by)\s+(?:each\s+)?(?:labor\s+)?(?:categor(?:y|ies)|role(?:s)?|position(?:s)?)"
    r"|fully[\s-]?burdened\s+hourly"
    r"|personnel[\s-]?loading"
    r"|labor[\s-]?categor(?:y|ies).{0,60}hourly"
    r"|year[\s-]*(?:2|3|two|three).{0,80}(?:percent|%|increase|escalat)"
    r"|(?:percent|%)\s+(?:increase|escalat).{0,80}year[\s-]*(?:2|3|two|three)"
    r"|proposed\s+hourly\s+rates?\s+for\s+the\s+following\s+roles?"
    r")",
    re.I | re.S,
)

_ROLE_TABLE_LINE_RE = re.compile(
    r"(?m)^\s*(?:\d+[.)]\s*|[A-Z][.)]\s*|[-•*]\s*)?"
    r"([A-Z][A-Za-z0-9/ &\-]{2,60})"
    r"(?:\s*(?:hourly\s+rate|/hr|/hour))?\s*$"
)


def extract_rfp_labor_role_labels(rfp_text: str, *, max_roles: int = 12) -> list[str]:
    """Best-effort role names from an hourly-rate table / role list in the RFP.

    Only used when rendering an already-chosen ``personnel_loading`` budget —
    never to decide budgetFormat.
    """
    text = rfp_text or ""
    if not text.strip():
        return []
    # Prefer a window around hourly-rate / labor-category language.
    windows: list[str] = []
    for m in _PERSONNEL_LOADING_WINDOW_RE.finditer(text):
        start = max(0, m.start() - 200)
        end = min(len(text), m.end() + 2500)
        windows.append(text[start:end])
    blob = "\n".join(windows) if windows else text[:12_000]
    skip = {
        "hourly rate",
        "hourly rates",
        "labor category",
        "labor categories",
        "cost proposal",
        "pricing proposal",
        "year 1",
        "year 2",
        "year 3",
        "total",
        "description",
        "position",
        "role",
        "title",
    }
    out: list[str] = []
    seen: set[str] = set()
    for m in _ROLE_TABLE_LINE_RE.finditer(blob):
        label = re.sub(r"\s+", " ", m.group(1)).strip(" -:|")
        key = label.casefold()
        if len(label) < 3 or key in skip or key in seen:
            continue
        if label.casefold().startswith(("provide", "submit", "include", "the ")):
            continue
        seen.add(key)
        out.append(label)
        if len(out) >= max_roles:
            break
    return out


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
    if (budget.budget_format or "").casefold() == "blended_rate_form":
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


def _hourly_rate_from_line(item: BudgetLineItem) -> float | None:
    unit = (item.unit or "").casefold()
    if item.rate is not None and unit in {"hour", "hours", "hr", "hrs", ""}:
        return float(item.rate)
    if item.rate is not None and unit in {"flat", "fixed"} and item.quantity == 1:
        # Flat one-off is not an hourly rate.
        return None
    if item.rate is not None and "hour" in unit:
        return float(item.rate)
    return None


def render_personnel_loading_form_markdown(
    budget: ProposalBudget,
    *,
    rfp_text: str = "",
) -> str:
    """RFP-required Role | Hourly Rate | Year-2 % | Year-3 % table.

    Never invent missing role rates — MANUAL FILL when the guide/build has no hourly.
    Returns empty string when there are no bindable rates and no RFP role labels
    (caller must cut the hollow table rather than ship placeholder columns).
    """
    roles = extract_rfp_labor_role_labels(rfp_text)
    # Fall back to line-item role titles / descriptions when RFP parse is thin.
    if len(roles) < 3:
        for item in budget.line_items:
            label = (item.role_title or item.description or "").strip()
            if not label:
                continue
            key = label.casefold()
            if key not in {r.casefold() for r in roles}:
                roles.append(label.split("—")[0].split("-")[0].strip()[:60])
            if len(roles) >= 12:
                break

    yoy = (budget.option_term_notes or "").strip()
    y2 = y3 = None
    m2 = re.search(r"year[\s-]*2[^%]{0,40}?(\d+(?:\.\d+)?)\s*%", yoy, re.I)
    m3 = re.search(r"year[\s-]*3[^%]{0,40}?(\d+(?:\.\d+)?)\s*%", yoy, re.I)
    if m2:
        y2 = m2.group(1)
    if m3:
        y3 = m3.group(1)
    show_yoy = bool(y2 or y3) or bool(
        re.search(
            r"(?i)year[\s-]*[23]|option\s+years?|%\s*increase",
            rfp_text or "",
        )
    )

    rate_rows: list[tuple[str, str, str, str]] = []
    used_ids: set[str] = set()
    for role in roles:
        rate_val: float | None = None
        role_cf = role.casefold()
        for item in budget.line_items:
            if item.id in used_ids:
                continue
            blob = f"{item.role_title or ''} {item.description or ''}".casefold()
            if role_cf and (
                role_cf in blob
                or any(t in blob for t in role_cf.split() if len(t) > 3)
            ):
                rate_val = _hourly_rate_from_line(item)
                if rate_val is None and item.rate is not None and (
                    item.unit or ""
                ).casefold() in {
                    "hour",
                    "hours",
                    "hr",
                    "hrs",
                }:
                    rate_val = float(item.rate)
                used_ids.add(item.id)
                break
        if rate_val is None:
            for vr in budget.verified_rates or []:
                vr_role = (vr.role or vr.person_name or "").strip()
                if not vr_role or not (vr.hourly_rate or 0):
                    continue
                if role_cf in vr_role.casefold() or vr_role.casefold() in role_cf:
                    rate_val = float(vr.hourly_rate)
                    break
        rate_cell = _usd(rate_val) if rate_val is not None else "—"
        y2_cell = f"{y2}%" if y2 else "—"
        y3_cell = f"{y3}%" if y3 else "—"
        rate_rows.append((role, rate_cell, y2_cell, y3_cell))

    # No RFP roles and no priced hourly rows → cut entirely (no MANUAL FILL shell).
    has_any_rate = any(cell != "—" for _, cell, _, _ in rate_rows)
    if not rate_rows and not has_any_rate:
        return ""
    if not rate_rows:
        return ""
    if not has_any_rate and not roles:
        return ""

    if show_yoy:
        lines = [
            "## Cost Proposal — Hourly Rate Schedule",
            "",
            "This table answers the RFP's scored Cost / hourly-rate instrument "
            "(labor categories with Year-2 / Year-3 percentage increases). "
            "Do not substitute a fixed-fee retainer for this form.",
            "",
            "| Role / Labor Category | Year-1 Hourly Rate | Year-2 % Increase | Year-3 % Increase |",
            "| --- | ---: | ---: | ---: |",
        ]
        for role, rate_cell, y2_cell, y3_cell in rate_rows:
            lines.append(f"| {role} | {rate_cell} | {y2_cell} | {y3_cell} |")
    else:
        lines = [
            "## Cost Proposal — Hourly Rate Schedule",
            "",
            "This table answers the RFP's scored Cost / hourly-rate instrument "
            "(labor categories). Do not substitute a fixed-fee retainer for this form.",
            "",
            "| Role / Labor Category | Year-1 Hourly Rate |",
            "| --- | ---: |",
        ]
        for role, rate_cell, _, _ in rate_rows:
            lines.append(f"| {role} | {rate_cell} |")

    lines.append("")
    return "\n".join(lines)


def _md_table_cell(text: str) -> str:
    """Strip characters that break GitHub/markdown pipe tables."""
    cleaned = " ".join((text or "").replace("|", " ").split()).strip()
    return cleaned or "—"


def _client_rate_source_label(source: str) -> str:
    """Human label for schedule footnote — drop path junk / pipes."""
    cleaned = _md_table_cell(source)
    if cleaned in {"—", "KB", "kb"}:
        return ""
    # Prefer short agency wording over raw Drive filenames in client prose.
    low = cleaned.casefold()
    if "role rates" in low or "fee schedule" in low or "labor" in low:
        return "agency role billable rate card"
    if "00_guide_pricing" in low or "guide_pricing" in low:
        return "00_Guide_Pricing"
    if len(cleaned) > 60:
        return cleaned[:57] + "…"
    return cleaned


def render_kb_classification_rate_schedule_markdown(
    budget: ProposalBudget,
    *,
    rfp_text: str = "",
) -> str:
    """Client hourly schedule from verifiedRates / unit=hour lines (KB-grounded).

    Used when the RFP mandates a classification rate schedule alongside phased fees.
    """
    rows: list[tuple[str, float, str]] = []
    seen: set[str] = set()
    for vr in budget.verified_rates or []:
        role = (vr.role or vr.person_name or "").strip()
        rate = vr.hourly_rate
        if not role or rate is None or float(rate) <= 0:
            continue
        key = role.casefold()
        if key in seen:
            continue
        seen.add(key)
        rows.append((role, float(rate), (vr.source or "").strip()))
    for item in budget.line_items or []:
        unit = (item.unit or "").casefold()
        if unit not in {"hour", "hours", "hr", "hrs"}:
            continue
        rate_val = _hourly_rate_from_line(item)
        if rate_val is None and item.rate is not None:
            rate_val = float(item.rate)
        if rate_val is None or rate_val <= 0:
            continue
        role = (item.role_title or item.description or "").strip()
        if not role:
            continue
        key = role.casefold()
        if key in seen:
            continue
        seen.add(key)
        rows.append((role, float(rate_val), (item.rate_source or "").strip()))
    if not rows:
        return ""

    yoy = (budget.option_term_notes or "").strip()
    y2 = y3 = None
    m2 = re.search(r"year[\s-]*2[^%]{0,40}?(\d+(?:\.\d+)?)\s*%", yoy, re.I)
    m3 = re.search(r"year[\s-]*3[^%]{0,40}?(\d+(?:\.\d+)?)\s*%", yoy, re.I)
    if m2:
        y2 = m2.group(1)
    if m3:
        y3 = m3.group(1)

    labels = []
    for _, _, src in rows:
        label = _client_rate_source_label(src)
        if label and label not in labels:
            labels.append(label)
    source_note = ", ".join(labels[:3]) if labels else "agency rate card"

    show_yoy = bool(y2 or y3) or bool(
        re.search(
            r"(?i)year[\s-]*[23]|option\s+years?|%\s*increase",
            rfp_text or "",
        )
    )

    lines = [
        "## Hourly Rate Schedule by Classification",
        "",
        f"Billable classification rates from the {source_note}. "
        "This schedule answers the RFP’s classification-rate disclosure. "
        "Proposed investment remains the phase / project fees above "
        "(hours × rate is used only when the RFP scores a staff-loading form).",
        "",
    ]
    if show_yoy:
        lines.extend(
            [
                "| Role / Labor Category | Hourly Rate (billable) | Year-2 % Increase | Year-3 % Increase |",
                "| --- | ---: | ---: | ---: |",
            ]
        )
        for role, rate_val, _source in rows:
            y2_cell = f"{y2}%" if y2 else "—"
            y3_cell = f"{y3}%" if y3 else "—"
            lines.append(
                f"| {_md_table_cell(role)} | {_usd(rate_val)} | "
                f"{_md_table_cell(y2_cell)} | {_md_table_cell(y3_cell)} |"
            )
    else:
        lines.extend(
            [
                "| Role / Labor Category | Hourly Rate (billable) |",
                "| --- | ---: |",
            ]
        )
        for role, rate_val, _source in rows:
            lines.append(f"| {_md_table_cell(role)} | {_usd(rate_val)} |")
    lines.append("")

    from app.services.proposal_budget_playbook import (
        rfp_mandates_cost_assumptions_disclosure,
    )

    if rfp_mandates_cost_assumptions_disclosure(rfp_text):
        lines.extend(
            [
                "### Cost assumptions",
                "",
                "Travel, materials, software licenses, stock media, and subconsultant "
                "markup are billed as reimbursable / pass-through unless included in "
                "a phase fee above — confirm final assumptions with Sonja before "
                "submission when not stated in the Fee Detail.",
                "",
            ]
        )
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
    # Workers' Comp / WC insurance certificates are compliance forms — NOT fee tabs.
    # "compensation" alone used to score them as budget and chat Improve collapsed
    # PROPOSAL RATE/FEE SCHEDULE into WORKER'S COMPENSATION CERTIFICATE.
    if re.search(
        r"\bworkers?'?\s*compensation\b|\bworker's\s*compensation\b|"
        r"\bwc\b.{0,24}\b(certificate|insurance|affidavit|cert)\b|"
        r"\b(certificate|insurance|affidavit)\b.{0,24}\bworkers?'?\s*comp",
        t,
    ):
        return 0
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
    # "compensation schedule" / fee compensation — not workers' comp (handled above).
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

    Hollow Strict-RFP pricing stubs (e.g. PROPOSAL RATE/FEE SCHEDULE still showing
    "Draft this RFP-required section") beat Zo "Budget & Pricing" so Phase 3.5
    writes the fee table into the buyer's demanded tab — not a sibling that leaves
    the RFP fee schedule empty after Budget goes green.
    """
    from app.services.proposal_draft_structure_stubs import section_is_rfp_draft_stub
    from app.services.proposal_outline_dedup import is_pricing_outline_title

    hollow_rfp_pricing: list[tuple[int, int]] = []
    for i, section in enumerate(sections):
        title = section.title or ""
        score = budget_section_score(title)
        # Only true pricing / fee tabs — never insurance "compensation" certificates.
        pricingish = bool(
            _DEDICATED_BUDGET_TITLE_RE.search(title)
            or is_pricing_outline_title(title)
            or score >= 8
        )
        if not pricingish or score <= 0:
            continue
        if section_looks_like_official_pricing_form(section) and official_pricing_form_is_filled(
            section.content or ""
        ):
            continue
        if section_is_rfp_draft_stub(section):
            # Boost above Zo Budget & Pricing (score 15) so Budget phase fills
            # the buyer's Rate/Fee Schedule stub instead of a parallel Zo tab.
            hollow_rfp_pricing.append((max(score, 8) + 100, i))
    if hollow_rfp_pricing:
        hollow_rfp_pricing.sort(reverse=True)
        return hollow_rfp_pricing[0][1]

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
    """Build phase dollars from line items — same rollup as Fee Detail by Phase."""
    rows = _rollup_phase_fee_rows(budget)
    parts = [
        f"{label} ({_usd(amount)})"
        for label, _scope, amount in rows
        if amount is not None and float(amount) > 0
    ]
    if len(parts) < 2:
        return ""
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


def _sync_year1_investment_phrase(text: str, total: float) -> str:
    """Keep 'Year 1 investment of $X' on the canonical client total."""
    if not text or total <= 0:
        return text
    return _YEAR1_INVESTMENT_RE.sub(lambda m: f"{m.group(1)}{_usd(total)}", text)


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
    text = _sync_year1_investment_phrase(text, total)
    # Single stale grand-total left in Terms (e.g. only $240,000 appears) → sync.
    text = _sync_narrative_total(
        text,
        fees,
        protect=[direct] if direct > 0 else None,
    )
    return text


_QL_TITLE_RE = re.compile(
    r"(?im)^\s*(?:#{1,4}\s+|\*\*)?"
    r"(Investment Framing|Scope Protection|Reimbursable Expenses|"
    r"Reimbursables|Revision Rounds)"
    r"(?:\*\*)?\s*:?\s*$"
)
_QL_ALLOCATION_RE = re.compile(
    r"(?i)"
    r"(?:\*\*)?"
    r"(?P<label>[A-Z][A-Za-z0-9 &'/,\-]{2,72}?)"
    r"(?:\*\*)?"
    r"\s*(?::|—+|-+|account(?:s)?\s+for|is|,)?\s*"
    r"(?P<pct>\d{1,3}(?:\.\d+)?)\s*%"
    r"\s*\(\s*(?P<amt>\$[\d,]+(?:\.\d{2})?)\s*\)"
    r"(?P<note>[^.]{0,160})?"
)
_QL_ALLOCATION_PCT_FIRST_RE = re.compile(
    r"(?i)"
    r"(?P<pct>\d{1,3}(?:\.\d+)?)\s*%"
    r"\s+(?:allocated\s+)?to\s+"
    r"(?P<label>[A-Za-z][A-Za-z0-9 &'/,\-]{2,72}?)"
    r"\s*\(\s*(?P<amt>\$[\d,]+(?:\.\d{2})?)"
    r"(?P<note>[^)]{0,160})?\)"
)
_QL_SKIP_LABEL_RE = re.compile(
    r"(?i)^(the|our|a|an|this|year|total|combined total|proposed)\b"
)
_QL_STACKED_BULLET_RE = re.compile(r"(?m)^(?:\s*[-*]\s+){2,}")
_YEAR1_INVESTMENT_RE = re.compile(
    r"(?i)((?:the\s+)?year\s*1\s+investment\s+of\s+)\$[\d,]+(?:\.\d{2})?"
)


def format_qualifying_language_for_client(
    text: str,
    *,
    line_items: list | None = None,
    total: float | None = None,
    suppress_mix_tables: bool = False,
) -> str:
    """Turn Terms walls of dollars/percentages into headings, tables, and bullets.

    When ``suppress_mix_tables`` is True (Fee Detail by Phase is the canonical
    breakdown), Investment Framing keeps short prose/bullets only — never a
    Component|Share|Amount table that can disagree with the phase fee rollup.
    """
    raw = _unstick_stacked_bullets(text or "").strip()
    if total and float(total) > 0:
        raw = _sync_year1_investment_phrase(raw, float(total))
    if not raw:
        return raw
    ledger_table = (
        ""
        if suppress_mix_tables
        else _mix_table_from_line_items(line_items, total)
    )
    if (
        _qualifying_language_already_scannable(raw)
        and not ledger_table
        and not suppress_mix_tables
    ):
        return raw
    if (
        suppress_mix_tables
        and _qualifying_language_already_scannable(raw)
        and not _text_has_component_share_table(raw)
    ):
        return raw
    parts: list[str] = []
    for title, body in _split_qualifying_blocks(raw):
        use_ledger = bool(
            ledger_table
            and not suppress_mix_tables
            and (not title or title.casefold().startswith("investment"))
        )
        parts.append(
            _format_qualifying_block(
                title,
                body,
                ledger_table=ledger_table if use_ledger else None,
                suppress_mix_tables=suppress_mix_tables,
            )
        )
    return "\n\n".join(p for p in parts if p.strip()) or raw


def reformat_budget_terms_in_markdown(content: str) -> str:
    """Rewrite a persisted ## Terms wall into tables + bullets without changing numbers."""
    text = content or ""
    suppress = _text_has_fee_detail_heading(text)
    match = re.search(r"(?im)^##\s+Terms\s*$", text)
    if not match:
        if "investment framing" in text.casefold() and "%" in text and "$" in text:
            return format_qualifying_language_for_client(
                text, suppress_mix_tables=suppress
            )
        return text
    start = match.end()
    next_h = re.search(r"(?im)^##\s+\S", text[start:])
    end = start + next_h.start() if next_h else len(text)
    body = text[start:end].strip()
    formatted = format_qualifying_language_for_client(
        body, suppress_mix_tables=suppress
    )
    if formatted.strip() == body:
        return text
    suffix = text[end:]
    joiner = "\n\n" if suffix.strip() else "\n"
    return text[:start] + "\n\n" + formatted + joiner + suffix


def _unstick_stacked_bullets(text: str) -> str:
    """`- - - sentence` from repeated format passes → `- sentence`."""
    return _QL_STACKED_BULLET_RE.sub("- ", text or "")


def _has_inline_mix_sentence(text: str) -> bool:
    blob = text or ""
    pct_first = len(_QL_ALLOCATION_PCT_FIRST_RE.findall(blob))
    label_first = len(_QL_ALLOCATION_RE.findall(blob))
    return (pct_first + label_first) >= 2


def _qualifying_language_already_scannable(text: str) -> bool:
    if _QL_STACKED_BULLET_RE.search(text or ""):
        return False
    if _has_inline_mix_sentence(text) and not re.search(
        r"(?im)^\s*\|\s*Component\s*\|", text or ""
    ):
        return False
    has_table = bool(re.search(r"(?m)^\s*\|.+\|\s*$", text))
    has_bullets = bool(re.search(r"(?m)^\s*[-*]\s+\S", text))
    paragraphs = []
    for block in re.split(r"\n\s*\n", text or ""):
        cleaned = "\n".join(
            ln for ln in block.splitlines() if not ln.strip().startswith("|")
        ).strip()
        if cleaned:
            paragraphs.append(cleaned)
    longest = max((len(p) for p in paragraphs), default=0)
    return (has_table or has_bullets) and longest <= 360


def _split_qualifying_blocks(text: str) -> list[tuple[str, str]]:
    matches = list(_QL_TITLE_RE.finditer(text))
    if not matches:
        return [("", text.strip())]
    blocks: list[tuple[str, str]] = []
    preamble = text[: matches[0].start()].strip()
    if preamble:
        blocks.append(("", preamble))
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        blocks.append((match.group(1).strip(), text[start:end].strip()))
    return blocks


def _format_qualifying_block(
    title: str,
    body: str,
    *,
    ledger_table: str | None = None,
    suppress_mix_tables: bool = False,
) -> str:
    heading = f"### {title}" if title else ""
    if suppress_mix_tables:
        # Fee Detail by Phase is authoritative — drop any Component|Share mix.
        leftover = _strip_component_share_markdown_tables(body)
        leftover = _drop_percent_money_sentences(leftover)
        leftover = _drop_extraction_debris(leftover)
        table_md = None
        lead = _prose_to_qualifying_bullets(leftover)
        if (
            title
            and title.casefold().startswith("investment")
            and not lead.strip()
        ):
            lead = (
                "- Fees are organized by delivery phase in **Fee Detail by Phase** "
                "below — that table is the fee breakdown."
            )
    else:
        extracted, leftover = _extract_allocation_table(body)
        if extracted or ledger_table:
            leftover = _drop_percent_money_sentences(leftover)
            leftover = _drop_extraction_debris(leftover)
            if ledger_table:
                leftover = _drop_dollar_sentences(leftover)
                year1 = _YEAR1_INVESTMENT_RE.search(body)
                if year1 and "$" not in leftover:
                    leftover = (
                        f"- {year1.group(0)} reflects the scope as understood at "
                        f"proposal stage.\n{leftover}"
                    ).strip()
            table_md = ledger_table or extracted
        else:
            table_md = None
        lead = _prose_to_qualifying_bullets(leftover)
    chunks = [heading, lead, table_md]
    return "\n\n".join(c for c in chunks if c and c.strip())


def _allocation_rows_from_text(text: str) -> list[tuple[str, str, str, str, str]]:
    """(full_match, label, pct, amt, note) from either mix syntax."""
    rows: list[tuple[str, str, str, str, str]] = []
    for match in _QL_ALLOCATION_PCT_FIRST_RE.finditer(text or ""):
        label = re.sub(r"\s+", " ", match.group("label") or "").strip(" :—-,")
        if len(label.split()) > 10 or _QL_SKIP_LABEL_RE.search(label):
            continue
        note = re.sub(r"\s+", " ", (match.group("note") or "").strip(" :—-,."))
        rows.append(
            (
                match.group(0),
                label,
                f"{match.group('pct')}%",
                match.group("amt"),
                note,
            )
        )
    for match in _QL_ALLOCATION_RE.finditer(text or ""):
        label = re.sub(r"\s+", " ", match.group("label") or "").strip(" :—-,")
        if len(label.split()) > 10 or _QL_SKIP_LABEL_RE.search(label):
            continue
        note = re.sub(r"\s+", " ", (match.group("note") or "").strip(" :—-,."))
        rows.append(
            (
                match.group(0),
                label,
                f"{match.group('pct')}%",
                match.group("amt"),
                note,
            )
        )
    return rows


def _extract_allocation_table(text: str) -> tuple[str, str]:
    rows = _allocation_rows_from_text(text)
    leftover = text
    seen: set[str] = set()
    unique: list[tuple[str, str, str, str]] = []
    for full, label, pct, amt, note in rows:
        leftover = leftover.replace(full, " ", 1)
        key = label.casefold()
        if key in seen:
            continue
        seen.add(key)
        unique.append((label, pct, amt, note))
    leftover = re.sub(r"[ \t]{2,}", " ", leftover)
    leftover = re.sub(r"\s+\.", ".", leftover)
    leftover = re.sub(r"\.{2,}", ".", leftover)
    leftover = re.sub(r"\n{3,}", "\n\n", leftover).strip(" \t\n.")
    leftover = leftover.strip()
    if len(unique) < 2:
        return "", text
    return _allocation_table_markdown(unique), leftover


def _allocation_table_markdown(rows: list[tuple[str, str, str, str]]) -> str:
    lines = [
        "| Component | Share | Amount | Notes |",
        "| --- | ---: | ---: | --- |",
    ]
    for label, pct, amt, note in rows:
        lines.append(f"| {label} | {pct} | {amt} | {note} |")
    return "\n".join(lines)


def _markdown_pipe_cells(line: str) -> list[str]:
    stripped = (line or "").strip()
    if "|" not in stripped:
        return []
    return [c.strip() for c in stripped.strip("|").split("|")]


def _is_component_share_header_line(line: str) -> bool:
    cells = [c.casefold() for c in _markdown_pipe_cells(line)]
    if len(cells) < 3:
        return False
    return "component" in cells and "share" in cells and "amount" in cells


def _is_markdown_separator_row(line: str) -> bool:
    cells = _markdown_pipe_cells(line)
    if not cells:
        return False
    return all((not c) or set(c) <= set("-: ") for c in cells)


def _heading_label(line: str) -> str:
    plain = (line or "").strip()
    while plain.startswith("#"):
        plain = plain[1:].lstrip()
    if plain.startswith("**") and plain.endswith("**"):
        plain = plain[2:-2].strip()
    return plain.casefold()


def _text_has_fee_detail_heading(text: str) -> bool:
    for line in (text or "").splitlines():
        label = _heading_label(line)
        if label.startswith("fee detail by phase") or label.startswith(
            "supporting fee detail"
        ):
            return True
    return False


def _text_has_component_share_table(text: str) -> bool:
    return any(_is_component_share_header_line(ln) for ln in (text or "").splitlines())


def _strip_component_share_markdown_tables(text: str) -> str:
    """Remove Component|Share|Amount mix tables via line structure (no regex)."""
    if not text or "Component" not in text:
        return text
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        if _is_component_share_header_line(lines[i]):
            i += 1
            if i < len(lines) and _is_markdown_separator_row(lines[i]):
                i += 1
            while i < len(lines) and lines[i].strip().startswith("|"):
                i += 1
            while i < len(lines) and not lines[i].strip():
                i += 1
            if out and out[-1].strip():
                out.append("")
            continue
        out.append(lines[i])
        i += 1
    cleaned = "\n".join(out)
    while "\n\n\n" in cleaned:
        cleaned = cleaned.replace("\n\n\n", "\n\n")
    return cleaned.strip()


def scrub_duplicate_budget_breakdown_tables(content: str) -> tuple[str, list[str]]:
    """When Fee Detail by Phase exists, drop conflicting Investment Framing mix tables.

    Both tables can independently sum to the same total while disagreeing on how
    categories roll up (e.g. Creative+Sponsorship vs one phase line). Fee Detail
    by Phase is the client-facing source of truth.
    """
    text = content or ""
    logs: list[str] = []
    if not text.strip():
        return text, logs
    if not _text_has_fee_detail_heading(text):
        return text, logs
    if not _text_has_component_share_table(text):
        return text, logs
    cleaned = _strip_component_share_markdown_tables(text)
    if cleaned.strip() != text.strip():
        logs.append(
            "Removed Investment Framing Component|Share table — Fee Detail by Phase "
            "is the sole fee breakdown"
        )
    return cleaned, logs


def _mix_table_from_line_items(line_items: list | None, total: float | None) -> str:
    if not line_items or not total or float(total) <= 0:
        return ""
    rows: list[tuple[str, str, str, str]] = []
    used_labels: set[str] = set()
    for item in line_items:
        amount = getattr(item, "extended", None)
        if not isinstance(amount, (int, float)) or float(amount) <= 0:
            continue
        phase, desc = _client_line_label(item)
        label = (phase or desc or "Fees").strip()
        key = label.casefold()
        if key in used_labels:
            # Disambiguate by using the category directly (e.g., "Discovery & Transition")
            # instead of truncating raw description text.
            cat = getattr(item, "category", "") or ""
            cat = cat.strip()
            if cat and cat.casefold() != key:
                label = cat
            else:
                # Try the original description's phase prefix (before the em dash).
                raw_desc = (item.description or "").strip()
                phase_prefix = re.split(r"\s*[—:\-]\s+", raw_desc, maxsplit=1)[0].strip()
                if phase_prefix and len(phase_prefix) < 60 and phase_prefix.casefold() != key:
                    label = phase_prefix
                else:
                    # Last resort: append a numeric suffix
                    suffix = 2
                    while f"{label} ({suffix})".casefold() in used_labels:
                        suffix += 1
                    label = f"{label} ({suffix})"
            key = label.casefold()
        used_labels.add(key)
        share = int(round(100.0 * float(amount) / float(total)))
        rows.append((label, f"{share}%", _usd(float(amount)), ""))
    if len(rows) < 2:
        return ""
    return _allocation_table_markdown(rows)


def _iter_qualifying_units(text: str) -> list[str]:
    raw = (text or "").strip()
    if not raw:
        return []
    if re.search(r"(?m)^\s*[-*]\s+\S", raw):
        units: list[str] = []
        for line in raw.splitlines():
            item = re.sub(r"^(?:\s*[-*]\s+)+", "", line).strip()
            if item:
                units.append(item)
        return units
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", raw) if s.strip()]


def _drop_percent_money_sentences(text: str) -> str:
    kept = [
        unit
        for unit in _iter_qualifying_units(text)
        if not ("%" in unit and "$" in unit)
    ]
    return _units_to_leftover(text, kept)


def _drop_dollar_sentences(text: str) -> str:
    kept = [unit for unit in _iter_qualifying_units(text) if "$" not in unit]
    return _units_to_leftover(text, kept)


def _drop_extraction_debris(text: str) -> str:
    kept = []
    for unit in _iter_qualifying_units(text):
        if re.search(r"(?:,\s*){2,}", unit) or re.search(r"\bwith\s*,", unit):
            continue
        if re.search(r"\b(?:and|with)\s*[.,]\s*$", unit):
            continue
        kept.append(unit)
    return _units_to_leftover(text, kept)


def _units_to_leftover(original: str, units: list[str]) -> str:
    if not units:
        return ""
    if re.search(r"(?m)^\s*[-*]\s+\S", original or ""):
        return "\n".join(
            u if u.startswith(("- ", "* ")) else f"- {u}" for u in units
        )
    return " ".join(units)


def _prose_to_qualifying_bullets(text: str) -> str:
    raw = _unstick_stacked_bullets(text or "").strip()
    if not raw:
        return ""
    if re.search(r"(?m)^\s*[-*]\s+\S", raw):
        lines: list[str] = []
        for line in raw.splitlines():
            item = re.sub(r"^(?:\s*[-*]\s+)+", "", line).strip()
            if not item:
                continue
            if item.startswith("|"):
                continue
            if not item.endswith((".", "!", "?")):
                item += "."
            lines.append(f"- {item}")
        return "\n".join(lines)
    blob = re.sub(r"\s+", " ", raw).strip()
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", blob) if s.strip()]
    if len(sentences) <= 1 and len(blob) <= 240:
        return blob
    out: list[str] = []
    for sentence in sentences:
        item = re.sub(r"^(?:\s*[-*]\s+)+", "", sentence).strip()
        if not item:
            continue
        if not item.endswith((".", "!", "?")):
            item += "."
        out.append(f"- {item}")
    return "\n".join(out)


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


_MENU_ID_PREFIX_RE = re.compile(r"^\d+\.\d+\s+")
_GUIDE_MENU_PHASE: dict[int, str] = {
    1: "Discovery & Research",
    2: "Strategy",
    3: "Creative",
    4: "Digital",
    5: "Content & Social",
    6: "Media",
    7: "Production",
    8: "Analytics",
    9: "Project Management",
}


def _scrub_internal_budget_jargon(text: str) -> str:
    """Remove KB / internal pricing references from client-facing budget prose."""
    if not text:
        return text
    out = text
    out = re.sub(r"00[_\s-]?Guide[_\s-]?Pricing", "zö agency fee schedule", out, flags=re.I)
    out = re.sub(
        r"(?i)Rates follow zö'?s Industry (?:Low|Average|High) pricing guide[^\n.]*\.?\s*",
        "",
        out,
    )
    out = re.sub(
        r"(?i)(?:per|from|using)\s+zö agency fee schedule(?:\s*\([^)]*\))?",
        "",
        out,
    )
    out = re.sub(
        r"(?i)Industry (?:Low|Average|High)(?:\s+tier)?",
        "published",
        out,
    )
    out = re.sub(
        r"(?is)\*?Rates are fully burdened agency work rates[^*]*\*?\s*",
        "",
        out,
    )
    # Footnote prose only — never strip intentional [MANUAL FILL: …] handoff tags.
    out = re.sub(
        r"(?i)\bblank cells are MANUAL FILL[^\n.]*\.?\s*",
        "",
        out,
    )
    out = re.sub(r"(?i)\bSonja\b", "agency leadership", out)
    out = re.sub(r"(?i)agency commission revenue", "professional fees", out)
    out = re.sub(r"(?i)\(base year\)", "", out)
    out = re.sub(r"(?i), not agency revenue", "", out)
    out = re.sub(r"(?i)not agency revenue", "not professional fees", out)
    out = re.sub(r"\[PRICING FLAG:[^\]]+\]", "", out, flags=re.I)
    out = _drop_internal_cost_mix_lines(out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def _drop_internal_cost_mix_lines(text: str) -> str:
    """Drop client-facing lines that leak the internal 50/30/20 cost mix."""
    kept: list[str] = []
    for line in (text or "").splitlines():
        compact = "".join(line.casefold().split())
        if (
            "50%wages" in compact
            and "30%g&a" in compact
            and "20%profit" in compact
        ):
            continue
        if (
            "50%labor" in compact
            and ("30%g&a" in compact or "30%overhead" in compact)
            and ("20%profit" in compact or "20%margin" in compact)
        ):
            continue
        kept.append(line)
    return "\n".join(kept)


def _client_deliverable_label(description: str) -> str:
    """Strip internal menu ids and redundant Phase prefixes for clean table output.

    The Phase column already carries the phase name, so the Scope/deliverable
    column should contain only the actual deliverable description.
    """
    desc = (description or "").strip()
    desc = _MENU_ID_PREFIX_RE.sub("", desc).strip()
    # Strip redundant "Phase N <Name> — " prefix (already shown in Phase column)
    desc = re.sub(
        r"^Phase\s+\d+\s+[^—–-]+[—–-]\s*",
        "",
        desc,
        flags=re.IGNORECASE,
    ).strip()
    return desc or "Professional services"


def _client_phase_from_deliverable(description: str, *, fallback: str = "Professional Services") -> str:
    match = re.match(r"^(\d+)\.", (description or "").strip())
    if match:
        return _GUIDE_MENU_PHASE.get(int(match.group(1)), fallback)
    return fallback


def _md_cell(text: str) -> str:
    """Keep markdown table cells single-line and pipe-safe."""
    cleaned = re.sub(r"\s+", " ", (text or "").replace("|", "/")).strip()
    return cleaned or "—"


def _no_em_dash(text: str) -> str:
    """House rule: no em/en dashes in client-facing budget text.

    A dash separating a label from its detail becomes a colon; anywhere else it
    becomes a comma, so "Group 1 — Media Planning" reads "Group 1: Media
    Planning" rather than losing the break entirely.
    """
    out = (text or "").replace("\u2014", "-").replace("\u2013", "-")
    out = out.replace(" - ", ": ", 1) if " - " in out else out
    return " ".join(out.replace(" - ", ", ").split())


def _scope_sentence(phase: str, descs: list[str]) -> str:
    """Readable scope prose, with the phase label not repeated in every item.

    Each line item's description carries its own phase prefix; joined raw they
    produced "Group 1 X - A; Group 1 X - B", restating the Phase column twice
    in one cell. The prefix is only stripped when a SEPARATOR follows it (the
    real shape: "Group 1 Media Planning & Advertising - Digital Campaign..."),
    so an item that merely starts with the same word - "Brand guidelines" under
    a "Brand" phase - keeps every word.
    """

    def _key(t: str) -> str:
        # "&" vs "and" is the difference between the phase label and the line
        # description on real data — without folding it the prefix survives and
        # the Phase column gets restated inside every Scope cell.
        return " ".join(
            _no_em_dash(t).casefold().replace("&", "and").replace(":", " ").split()
        )

    phase_key = _key(phase)
    cleaned: list[str] = []
    for raw in descs:
        d = _no_em_dash(raw).strip()
        if phase_key:
            # Walk the original string to the end of the phase prefix, comparing
            # on normalised keys so "&"/"and" and spacing cannot desync it.
            for cut in range(len(d), 0, -1):
                if _key(d[:cut]) == phase_key:
                    matched = d[:cut].rstrip()
                    rest = d[cut:].lstrip()
                    # The separator can sit on EITHER side: _no_em_dash already
                    # rewrites " - " to ": ", and _key folds ":" to a space, so
                    # the colon is often absorbed into the matched prefix.
                    sep_after = rest[:1] in {":", ",", "-"}
                    sep_inside = matched[-1:] in {":", ",", "-"}
                    if sep_after or sep_inside:
                        if sep_after:
                            rest = rest[1:].strip()
                        # Require real remaining content — never leave a fragment.
                        if len(rest.split()) >= 2:
                            d = rest
                    break
        d = d.rstrip(" .;")
        if d and d not in cleaned:
            cleaned.append(d)
    if not cleaned:
        return "Professional services"
    return ". ".join(cleaned) + "."


def _rollup_phase_fee_rows(
    budget: ProposalBudget,
) -> list[tuple[str, str, float | None]]:
    """Collapse line items into one client row per phase.

    Fee Detail is professional-fee dollars only:
    - ``client_passthrough`` never appears (ledger type — shown in investment header)
    - lines without a positive extended amount never appear (hollow / MANUAL FILL
      stubs go to outside-table notes, not zero-dollar table rows)

    No title/description keyword lists — RFP wording varies; structure + ledger type win.
    """
    from collections import OrderedDict

    from app.services.proposal_budget_validation import infer_line_item_type

    buckets: OrderedDict[str, dict[str, object]] = OrderedDict()
    for item in budget.line_items or []:
        kind = infer_line_item_type(item)
        if kind == "client_passthrough":
            continue
        amount_val = float(item.extended or 0) if item.extended is not None else 0.0
        if amount_val <= 0:
            continue
        phase, desc = _client_line_label(item)
        phase = _md_cell(_no_em_dash(phase))
        bucket = buckets.get(phase)
        if bucket is None:
            bucket = {"descs": [], "amount": 0.0, "has_amount": False}
            buckets[phase] = bucket
        descs = bucket["descs"]
        assert isinstance(descs, list)
        label = _md_cell(desc)
        if label and label not in descs and label != "—":
            descs.append(label)
        bucket["amount"] = float(bucket["amount"]) + amount_val
        bucket["has_amount"] = True

    rows: list[tuple[str, str, float | None]] = []
    for phase, data in buckets.items():
        phase_cf = phase.casefold()
        if "reference only" in phase_cf or "(reference)" in phase_cf:
            continue
        descs = list(data["descs"])  # type: ignore[arg-type]
        if len(descs) > 4:
            scope = _scope_sentence(phase, descs[:4]) + f" Plus {len(descs) - 4} more."
        else:
            scope = _scope_sentence(phase, descs)
        if not data["has_amount"]:
            continue
        amount = round(float(data["amount"]), 2)  # type: ignore[arg-type]
        if amount <= 0:
            continue
        rows.append((phase, scope, amount))
    return rows


def _outside_fee_detail_notes_markdown(budget: ProposalBudget) -> str:
    """Notes for ledger lines that must not sit in Fee Detail as hollow rows.

    Includes: client_passthrough (any), and agency/direct lines with no positive
    extended (MANUAL FILL / unpriced stubs). Labels come from the line itself —
    never a static synonym table of what “counts” as media/deployment.
    """
    from app.services.proposal_budget_validation import infer_line_item_type

    notes: list[str] = []
    for item in budget.line_items or []:
        kind = infer_line_item_type(item)
        desc = (item.description or item.role_title or item.category or "").strip()
        if not desc:
            continue
        amount = float(item.extended or 0) if item.extended is not None else 0.0
        hollow = amount <= 0
        if kind == "client_passthrough" or hollow:
            if desc.startswith("[MANUAL FILL") or desc.startswith("[VERIFY"):
                notes.append(f"- {desc}")
            elif hollow:
                label = _md_cell(_no_em_dash(desc))[:100]
                notes.append(
                    f"- [MANUAL FILL: Sonja — confirm dollars for “{label}” and whether "
                    "this sits inside or outside the professional-fee Total above]"
                )
            elif kind == "client_passthrough" and amount > 0:
                # Priced pass-through already in Proposed Investment header — skip.
                continue
    if not notes:
        return ""
    return (
        "### Items outside Fee Detail\n\n"
        "These ledger lines are not fee-table rows (pass-through and/or unpriced). "
        "Confirm each against THIS RFP’s cost ask before submission:\n\n"
        + "\n".join(notes)
        + "\n"
    )


def fee_detail_professional_total(budget: ProposalBudget) -> float:
    """Sum of Fee Detail by Phase amounts — client-facing professional-fee truth."""
    return round(
        sum(
            float(amount)
            for _phase, _scope, amount in _rollup_phase_fee_rows(budget)
            if amount is not None
        ),
        2,
    )


def render_fee_detail_by_phase_markdown(budget: ProposalBudget) -> str:
    """Standalone Fee Detail by Phase markdown from the canonical ledger."""
    lines: list[str] = []
    _append_fee_detail_by_phase_table(
        lines, budget, heading="## Fee Detail by Phase"
    )
    return "\n".join(lines).strip()


def ensure_fee_detail_table_in_budget_markdown(
    content: str,
    budget: ProposalBudget | None,
) -> str:
    """Re-inject Fee Detail by Phase when an LLM rewrite dropped the table.

    Keeps Cost client-ready: narrative alone is not a budget table.
    """
    text = content or ""
    if budget is None or not (budget.line_items or []):
        return text
    table = render_fee_detail_by_phase_markdown(budget)
    if not table.strip() or "| Phase |" not in table:
        return text
    has_table = bool(
        re.search(r"(?im)^##\s+Fee Detail by Phase\s*$", text)
        and re.search(r"(?i)\|\s*Phase\s*\|\s*Scope\s*\|\s*Fee\s*\|", text)
    )
    if has_table:
        # Replace existing Fee Detail block with canonical ledger table.
        match = re.search(r"(?im)^##\s+Fee Detail by Phase\s*$", text)
        if match:
            start = match.start()
            rest = text[match.end() :]
            next_h = re.search(r"(?im)^##\s+\S", rest)
            end = match.end() + (next_h.start() if next_h else len(rest))
            out = (text[:start] + table + "\n\n" + text[end:].lstrip()).strip() + "\n"
            return sync_proposed_investment_to_fee_detail_total(out, budget)
        return sync_proposed_investment_to_fee_detail_total(text, budget)
    # Insert before Hourly Rate Schedule / Additional Work / end.
    insert_at = None
    for pat in (
        r"(?im)^##\s+Hourly Rate Schedule",
        r"(?im)^##\s+Additional Work",
        r"(?im)^##\s+RFP Cost demand",
    ):
        m = re.search(pat, text)
        if m:
            insert_at = m.start()
            break
    if insert_at is None:
        out = text.rstrip() + "\n\n" + table + "\n"
    else:
        out = (
            text[:insert_at].rstrip()
            + "\n\n"
            + table
            + "\n\n"
            + text[insert_at:].lstrip()
        )
    return sync_proposed_investment_to_fee_detail_total(out, budget)


def _parse_fee_detail_table_total(content: str) -> float | None:
    """Read the Fee Detail **Total** cell — authoritative when present."""
    m = re.search(
        r"(?im)^\|\s*\*\*Total\*\*\s*\|\s*\|?\s*\*\*(\$[\d,]+(?:\.\d{2})?)\*\*\s*\|",
        content or "",
    )
    if not m:
        m = re.search(
            r"(?im)^\|\s*\*\*Total\*\*\s*\|\s*[^|]*\|\s*\*\*?(\$[\d,]+(?:\.\d{2})?)\*\*?\s*\|",
            content or "",
        )
    if not m:
        return None
    try:
        return round(float(m.group(1).replace("$", "").replace(",", "")), 2)
    except ValueError:
        return None


def sync_proposed_investment_to_fee_detail_total(
    content: str,
    budget: ProposalBudget | None = None,
) -> str:
    """Force Proposed Investment / fee prose to match Fee Detail Total.

    Fixes the recurring $69,000 header vs $67,500 table drift after rewrite.
    Does not change individual Fee Detail phase row amounts.
    """
    text = content or ""
    table_total = _parse_fee_detail_table_total(text)
    if table_total is None or table_total <= 0:
        if budget is not None:
            table_total = fee_detail_professional_total(budget)
        if table_total is None or table_total <= 0:
            return text

    fees_amt = table_total
    direct = 0.0
    passthrough = 0.0
    if budget is not None:
        _fees, direct = _professional_fees_and_direct(budget)
        passthrough = round(float(budget.client_media_passthrough or 0), 2)
    investment_total = round(fees_amt + direct + passthrough, 2)
    money = _usd(fees_amt)
    inv = _usd(investment_total)

    # Labeled bold headers (may appear more than once after rewrite stacking).
    text = re.sub(
        r"(?i)(\*{0,2}Professional fees:\s*\*{0,2})\s*\$[\d,]+(?:\.\d{2})?(\*{0,2})",
        rf"\1{money}\2",
        text,
    )
    text = re.sub(
        r"(?i)(\*{0,2}Total proposed investment:\s*\*{0,2})\s*\$[\d,]+(?:\.\d{2})?(\*{0,2})",
        rf"\1{inv}\2",
        text,
    )
    # "Total proposed investment: $X ($Y in professional fees)" — both → table.
    text = re.sub(
        r"(?i)(Total proposed investment:\s*)\$[\d,]+(?:\.\d{2})?"
        r"(\s*\(\s*)\$[\d,]+(?:\.\d{2})?(\s+in\s+professional\s+fees\s*\))",
        rf"\g<1>{inv}\2{money}\3",
        text,
    )
    # Trailing "($Y in professional fees)" alone.
    text = re.sub(
        r"(?i)(\(\s*)\$[\d,]+(?:\.\d{2})?(\s+in\s+professional\s+fees\s*\))",
        rf"\1{money}\2",
        text,
    )
    # NTE / all-in confirmations that repeat the stale professional-fee figure.
    text = re.sub(
        r"(?i)(not-to-exceed total contract amount of\s*\*?\*?)\$[\d,]+(?:\.\d{2})?",
        rf"\g<1>{money}",
        text,
    )
    text = re.sub(
        r"(?i)(confirms the\s*)\$[\d,]+(?:\.\d{2})?(\s+professional fee)",
        rf"\1{money}\2",
        text,
    )
    return text


def _append_fee_detail_by_phase_table(
    lines: list[str],
    budget: ProposalBudget,
    *,
    heading: str,
    include_residual_direct: bool = False,
) -> None:
    """Append a clean Phase | Scope | Fee rollup table to ``lines``.

    Professional-fee phases only. Travel and media pass-through stay in the
    Proposed Investment header so the table total matches professional fees
    (and does not fight the client grand total that includes pass-through).
    Explicit travel *line items* still appear as their own phase row.
    """
    rows = _rollup_phase_fee_rows(budget)
    fees, direct = _professional_fees_and_direct(budget)
    if not rows and not (include_residual_direct and direct > 0):
        return

    lines.append(heading)
    lines.append("")
    lines.append("| Phase | Scope | Fee |")
    lines.append("| --- | --- | ---: |")

    subtotal = 0.0
    for phase, scope, amount in rows:
        if amount is None:
            fee_cell = "—"
        else:
            fee_cell = _usd(amount)
            subtotal += amount
        lines.append(f"| {phase} | {scope} | {fee_cell} |")

    travel_already = any(
        "travel" in phase.casefold() or "reimburs" in phase.casefold()
        for phase, _, _ in rows
    )
    if include_residual_direct and direct > 0 and not travel_already:
        lines.append(
            f"| Travel / Reimbursables | Approved travel billed at cost | {_usd(direct)} |"
        )
        subtotal += direct

    table_total = round(subtotal, 2)
    if fees > 0 and table_total <= 0:
        table_total = round(fees + (direct if include_residual_direct else 0), 2)
    lines.append(f"| **Total** | | **{_usd(table_total)}** |")
    lines.append("")


def _professional_zo_budget_framing(*, client_name: str = "") -> str:
    buyer = client_name.strip() or "the client"
    return (
        f"zö agency proposes transparent, fixed project fees for this engagement with {buyer}. "
        "Fees reflect the scope described in this proposal; reimbursable travel is billed at cost "
        "with prior approval."
    )


# 00_Guide_Pricing.docx — marked USE VERBATIM. Do not paraphrase in Build Proposal.
PRICING_GUIDE_VERBATIM_INVESTMENT_FRAMING = (
    "zö agency works on a project-based fee schedule. We provide the cost for the "
    "project and we abide by those terms as does our client. When we are your partner, "
    "we work together to assess priorities and the overarching needs, timelines, and "
    "message so that the budget is wisely allocated to the highest-priority objectives. "
    "The following pricing represents estimates based on current information. Each "
    "engagement is scoped in detail at kickoff to reflect final requirements."
)
PRICING_GUIDE_VERBATIM_SCOPE_PROTECTION = (
    "Pricing reflects the scope as understood at proposal stage. As discovery progresses "
    "and priorities sharpen, we will refine specific deliverables with your team. Any "
    "material change in scope will be documented in a scope addendum before work proceeds."
)
PRICING_GUIDE_VERBATIM_REIMBURSABLE_EXPENSES = (
    "The following expenses will be billed at cost with prior approval: travel "
    "(mileage at current IRS rate, lodging, meals); photography/videography location "
    "fees and permits; specialized software licenses required for project-specific needs; "
    "stock photography/video licensing beyond standard subscriptions."
)
PRICING_GUIDE_VERBATIM_REVISION_ROUNDS = (
    "Standard engagement includes three rounds of review and revision on creative "
    "deliverables. Additional rounds available at scope addendum."
)

_VERBATIM_ANCHORS_CORE = (
    "we abide by those terms as does our client",
    "as discovery progresses and priorities sharpen",
)
_VERBATIM_ANCHORS_REIMBURSABLE = (
    "mileage at current irs rate",
    "photography/videography location fees and permits",
)
_VERBATIM_ANCHORS = _VERBATIM_ANCHORS_CORE + _VERBATIM_ANCHORS_REIMBURSABLE


def _professional_zo_budget_terms(*, include_reimbursable: bool = True) -> str:
    """Pricing Guide USE VERBATIM Terms blocks (Investment / Scope / Reimbursables / Revisions)."""
    parts = [
        "### Investment Framing\n\n"
        f"{PRICING_GUIDE_VERBATIM_INVESTMENT_FRAMING}\n\n"
        "### Scope Protection\n\n"
        f"{PRICING_GUIDE_VERBATIM_SCOPE_PROTECTION}\n\n"
    ]
    if include_reimbursable:
        parts.append(
            "### Reimbursable Expenses\n\n"
            f"{PRICING_GUIDE_VERBATIM_REIMBURSABLE_EXPENSES}\n\n"
        )
    else:
        parts.append(
            "### Expenses\n\n"
            "Professional fees are all-in for this engagement as scoped — no separate "
            "expense reimbursement line is billed beyond the fees stated above "
            "(RFP cost requirement). Travel or third-party costs, if any arise outside "
            "scope, require a written scope addendum before incurring.\n\n"
        )
    parts.append(
        "### Revision Rounds\n\n"
        f"{PRICING_GUIDE_VERBATIM_REVISION_ROUNDS}"
    )
    return "".join(parts)


def qualifying_language_has_pricing_guide_verbatim(
    text: str,
    *,
    require_reimbursable: bool = True,
) -> bool:
    blob = (text or "").casefold()
    anchors = _VERBATIM_ANCHORS if require_reimbursable else _VERBATIM_ANCHORS_CORE
    return all(anchor in blob for anchor in anchors)


def manuscript_asserts_all_in_no_separate_expenses(text: str) -> bool:
    """True when Cost already asserts expenses are not billed separately (RFP all-in)."""
    blob = (text or "").casefold()
    if not blob.strip():
        return False
    return bool(
        re.search(
            r"(?is)"
            r"("
            r"no\s+expense\s+(?:will\s+)?(?:appear|be\s+billed|reimbursement)"
            r"|no\s+expense\s+line\s+is\s+billed\s+separately"
            r"|expenses?\s+not\s+paid\s+separately"
            r"|not\s+billed\s+separately\s+from\s+the\s+fees"
            r"|all[\s-]?in\s+(?:professional\s+)?fees?"
            r"|no\s+separate\s+expense\s+reimbursement"
            r")",
            blob,
        )
    )


def strip_guide_reimbursable_expenses_heading_block(content: str) -> str:
    """Remove ### Reimbursable Expenses … through the next ###/## heading."""
    text = content or ""
    match = re.search(
        r"(?im)^###\s+Reimbursable\s+Expenses\s*$",
        text,
    )
    if not match:
        return text
    start = match.start()
    rest = text[match.end() :]
    next_h = re.search(r"(?im)^#{2,3}\s+\S", rest)
    end = match.end() + (next_h.start() if next_h else len(rest))
    return (text[:start] + text[end:]).strip() + ("\n" if text.endswith("\n") else "")


def _additive_reimbursable_extras(qualifying_language: str) -> str:
    """Keep RFP-specific reimbursable notes that sit alongside (not instead of) verbatim."""
    raw = (qualifying_language or "").strip()
    if not raw:
        return ""
    blocks = _split_qualifying_blocks(raw)
    body = raw
    for title, block_body in blocks:
        label = (title or "").casefold()
        if "reimburs" in label:
            body = block_body
            break
    extras: list[str] = []
    for para in re.split(r"\n{2,}", body):
        chunk = para.strip().lstrip("-•* ").strip()
        if not chunk:
            continue
        low = chunk.casefold()
        if "mileage at current irs rate" in low:
            continue
        if "photography/videography location fees" in low:
            continue
        if chunk.casefold() == PRICING_GUIDE_VERBATIM_REIMBURSABLE_EXPENSES.casefold():
            continue
        if (
            "billed at cost" in low
            and "travel" in low
            and ("lodging" in low or "meals" in low)
            and "software" in low
        ):
            continue
        extras.append(chunk)
    if not extras:
        return ""
    return "\n\n".join(extras)


def force_pricing_guide_verbatim_qualifying_language(
    qualifying_language: str = "",
    *,
    include_reimbursable: bool = True,
) -> str:
    """Ship Pricing Guide USE VERBATIM; optionally omit separate reimbursable block."""
    extras = _additive_reimbursable_extras(qualifying_language)
    if not include_reimbursable:
        # Keep additive RFP notes only (e.g. platform/ad-tech) under Expenses.
        base = _professional_zo_budget_terms(include_reimbursable=False)
        if extras:
            base = base.replace(
                "### Revision Rounds",
                f"{extras}\n\n### Revision Rounds",
                1,
            )
        return base
    reimbursable = PRICING_GUIDE_VERBATIM_REIMBURSABLE_EXPENSES
    if extras:
        reimbursable = f"{reimbursable}\n\n{extras}"
    return (
        "### Investment Framing\n\n"
        f"{PRICING_GUIDE_VERBATIM_INVESTMENT_FRAMING}\n\n"
        "### Scope Protection\n\n"
        f"{PRICING_GUIDE_VERBATIM_SCOPE_PROTECTION}\n\n"
        "### Reimbursable Expenses\n\n"
        f"{reimbursable}\n\n"
        "### Revision Rounds\n\n"
        f"{PRICING_GUIDE_VERBATIM_REVISION_ROUNDS}"
    )


_PRICING_GUIDE_BLOCK_START_RE = re.compile(
    r"(?im)^(?:#{1,4}\s+|\*\*)?"
    r"(Investment Framing|Scope Protection|Reimbursable Expenses|"
    r"Expenses|Revision Rounds)"
    r"(?:\*\*)?\s*:?\s*$"
)


def collapse_duplicate_pricing_guide_blocks(content: str) -> str:
    """Keep one Investment Framing→Revision Rounds cycle; drop stacked copies.

    Chat rewrite + ensure_verbatim can leave Investment Framing / Scope /
    Expenses / Revision Rounds repeated 2–3× under ## Terms. Early-return
    paths that see any verbatim anchor used to leave those stacks intact.
    """
    text = content or ""
    if text.count("Investment Framing") < 2 and text.count("Revision Rounds") < 2:
        return text

    matches = list(_PRICING_GUIDE_BLOCK_START_RE.finditer(text))
    if len(matches) < 2:
        return text

    # Walk cycles: each cycle starts at Investment Framing (or first guide
    # heading) and ends after Revision Rounds (or next ## Fee / ## heading).
    cycle_starts: list[int] = []
    for m in matches:
        label = (m.group(1) or "").casefold()
        if label == "investment framing" or (
            not cycle_starts and label in {"scope protection", "revision rounds"}
        ):
            # New cycle when we see Investment Framing again after a prior start.
            if label == "investment framing" and cycle_starts:
                cycle_starts.append(m.start())
            elif not cycle_starts:
                cycle_starts.append(m.start())

    if len(cycle_starts) < 2:
        # Fallback: second+ Investment Framing heading → strip through its
        # following Revision Rounds block repeatedly.
        out = text
        while True:
            first = re.search(r"(?im)^#{1,4}\s+Investment Framing\s*$", out)
            if not first:
                break
            second = re.search(
                r"(?im)^#{1,4}\s+Investment Framing\s*$",
                out[first.end() :],
            )
            if not second:
                break
            abs_second = first.end() + second.start()
            after = out[abs_second:]
            rev = re.search(r"(?im)^#{1,4}\s+Revision Rounds\s*$", after)
            if rev:
                rest = after[rev.end() :]
                next_h = re.search(r"(?im)^#{1,3}\s+\S", rest)
                end_rel = rev.end() + (next_h.start() if next_h else len(rest))
                # Drop from second Investment Framing through end of that
                # Revision Rounds section.
                out = (out[:abs_second] + after[end_rel:]).strip() + (
                    "\n" if out.endswith("\n") else ""
                )
            else:
                # No Revision Rounds — drop through next ## heading or EOF.
                next_h = re.search(r"(?im)^##\s+\S", after)
                end_rel = next_h.start() if next_h else len(after)
                out = (out[:abs_second] + after[end_rel:]).strip() + (
                    "\n" if out.endswith("\n") else ""
                )
        return out

    # Keep first cycle; remove subsequent cycles (Investment Framing … end of
    # its Revision Rounds).
    out = text
    # Process from the end so indices stay valid.
    for start in reversed(cycle_starts[1:]):
        chunk = out[start:]
        rev = re.search(r"(?im)^#{1,4}\s+Revision Rounds\s*$", chunk)
        if rev:
            rest = chunk[rev.end() :]
            next_h = re.search(r"(?im)^#{1,3}\s+\S", rest)
            end_rel = rev.end() + (next_h.start() if next_h else len(rest))
            out = out[:start] + chunk[end_rel:]
        else:
            next_h = re.search(r"(?im)^##\s+\S", chunk)
            end_rel = next_h.start() if next_h else len(chunk)
            out = out[:start] + chunk[end_rel:]
    # Normalize excess blank lines.
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out


def ensure_pricing_guide_verbatim_in_budget_markdown(
    content: str,
    *,
    include_reimbursable: bool | None = None,
) -> str:
    """Rewrite ## Terms (or bare qualifying blocks) to Pricing Guide USE VERBATIM."""
    text = content or ""
    if not text.strip():
        return text
    if include_reimbursable is None:
        include_reimbursable = not manuscript_asserts_all_in_no_separate_expenses(text)
    if not include_reimbursable:
        text = strip_guide_reimbursable_expenses_heading_block(text)
    # Collapse stacks before early-return / rewrite so verbatim anchors in a
    # duplicated Terms block cannot freeze a 3× Investment Framing mess.
    text = collapse_duplicate_pricing_guide_blocks(text)
    match = re.search(r"(?im)^##\s+Terms\s*$", text)
    if match:
        start = match.end()
        next_h = re.search(r"(?im)^##\s+\S", text[start:])
        end = start + next_h.start() if next_h else len(text)
        body = text[start:end].strip()
        forced = force_pricing_guide_verbatim_qualifying_language(
            body, include_reimbursable=include_reimbursable
        )
        formatted = format_qualifying_language_for_client(
            forced,
            suppress_mix_tables=_text_has_fee_detail_heading(text),
        )
        suffix = text[end:]
        joiner = "\n\n" if suffix.strip() else "\n"
        return collapse_duplicate_pricing_guide_blocks(
            text[:start] + "\n\n" + formatted + joiner + suffix
        )
    if any(
        h in text.casefold()
        for h in ("investment framing", "scope protection", "reimbursable")
    ):
        if qualifying_language_has_pricing_guide_verbatim(
            text, require_reimbursable=include_reimbursable
        ):
            if not include_reimbursable:
                return collapse_duplicate_pricing_guide_blocks(
                    strip_guide_reimbursable_expenses_heading_block(text)
                )
            return collapse_duplicate_pricing_guide_blocks(text)
        return collapse_duplicate_pricing_guide_blocks(
            force_pricing_guide_verbatim_qualifying_language(
                text, include_reimbursable=include_reimbursable
            )
        )
    return text


def normalize_fixed_pricing_narrative(
    budget: ProposalBudget,
    *,
    rfp_text: str = "",
) -> ProposalBudget:
    """Drop hourly-table prose when the RFP requires a fixed Pricing Table."""
    from app.services.proposal_budget_format_judge import rfp_indicates_fixed_pricing_table

    if (budget.budget_format or "").casefold() != "phased":
        return budget
    if not rfp_indicates_fixed_pricing_table(rfp_text):
        return budget
    ql = force_pricing_guide_verbatim_qualifying_language(
        _scrub_internal_budget_jargon(budget.qualifying_language or "")
    )
    scope = _scrub_internal_budget_jargon(budget.scope_summary or "")
    if not scope.strip() or any(
        token in (budget.scope_summary or "").casefold()
        for token in ("hourly", "00_guide", "pricing guide", "industry low", "industry average")
    ):
        scope = _professional_zo_budget_framing()
    return budget.model_copy(
        update={
            "qualifying_language": ql[:2000],
            "scope_summary": scope[:2000],
        }
    )


def prepare_budget_for_client_display(budget: ProposalBudget) -> ProposalBudget:
    """Dedupe travel, sync totals, scrub internal jargon before manuscript render.

    Preserves agency vs pass-through split: agency_revenue / lump_sum = agency fees
    (+ direct); total_client_invoicing = client grand total including pass-through.
    """
    from app.services.proposal_budget_validation import split_line_item_totals

    cleaned = dedupe_travel_vs_direct_expenses(budget)
    fees, reimbursables = _professional_fees_and_direct(cleaned)
    table_fees = fee_detail_professional_total(cleaned)
    if table_fees > 0 and abs(table_fees - fees) > 0.01:
        # Align ledger display totals to Fee Detail (never leave $69k vs $67.5k).
        fees = table_fees
    direct_bucket = round(float(cleaned.direct_expenses_total or 0), 2)
    line_sum, agency_fee, passthrough = split_line_item_totals(cleaned.line_items or [])
    if table_fees > 0:
        agency_fee = table_fees
    elif agency_fee <= 0:
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
            total=display_total,
        )
        ql = _scrub_unverified_benchmark_clients(ql)
        scope = _scrub_unverified_benchmark_clients(scope)
        if scope != (cleaned.scope_summary or ""):
            updates["scope_summary"] = scope
        # Pricing Guide USE VERBATIM — never ship paraphrased Terms from the LLM.
        ql = force_pricing_guide_verbatim_qualifying_language(ql)
        updates["qualifying_language"] = ql
        synced_scope = _sync_labeled_fee_subtotal(
            updates.get("scope_summary", cleaned.scope_summary or ""), fees
        )
        if synced_scope != (updates.get("scope_summary", cleaned.scope_summary or "")):
            updates["scope_summary"] = synced_scope
        formatted_ql = format_qualifying_language_for_client(
            updates.get("qualifying_language", cleaned.qualifying_language or ""),
            line_items=list(cleaned.line_items or []),
            total=display_total,
            # Fee Detail by Phase is appended on render — never dual-publish a
            # Component|Share mix that can disagree with the phase rollup.
            suppress_mix_tables=bool(cleaned.line_items),
        )
        if formatted_ql != (
            updates.get("qualifying_language", cleaned.qualifying_language or "")
        ):
            updates["qualifying_language"] = formatted_ql
        # Re-assert verbatim after format (format must not paraphrase guide copy).
        updates["qualifying_language"] = force_pricing_guide_verbatim_qualifying_language(
            updates.get("qualifying_language", "")
        )
    opt = (cleaned.option_term_notes or "").strip()
    if opt:
        # Internal jargon only — no topic/keyword rewrites of fee structure.
        opt2 = opt.replace("agency revenue estimate", "proposed fees")
        opt2 = opt2.replace("Agency revenue estimate", "Proposed fees")
        opt2 = re.sub(r"(?i)agency commission revenue", "professional fees", opt2)
        opt2 = re.sub(r"(?i)not agency revenue", "not professional fees", opt2)
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
        cleaned = cleaned.model_copy(
            update={
                "scope_summary": _scrub_internal_budget_jargon(cleaned.scope_summary or ""),
                "qualifying_language": force_pricing_guide_verbatim_qualifying_language(
                    _scrub_internal_budget_jargon(cleaned.qualifying_language or "")
                ),
                "option_term_notes": _scrub_internal_budget_jargon(
                    cleaned.option_term_notes or ""
                ),
            }
        )
        return cleaned
    cleaned = cleaned.model_copy(update=updates)
    cleaned = cleaned.model_copy(
        update={
            "scope_summary": _scrub_internal_budget_jargon(cleaned.scope_summary or ""),
            "qualifying_language": force_pricing_guide_verbatim_qualifying_language(
                _scrub_internal_budget_jargon(cleaned.qualifying_language or "")
            ),
            "option_term_notes": _scrub_internal_budget_jargon(
                cleaned.option_term_notes or ""
            ),
        }
    )
    return cleaned


def _client_line_label(item: BudgetLineItem) -> tuple[str, str]:
    """Return (delivery phase, deliverable label) for the client fee table.

    Phase comes from structured line data (category / line type) — never from
    keyword-guessing the description.
    """
    desc = (item.description or "").strip()
    if desc.startswith("[MANUAL FILL"):
        phase = _phase_label_for_line(item)
        return phase, desc
    # Strip internal source footnotes without keyword topic matching.
    if "*(Source:" in desc or "* (Source:" in desc:
        cut = desc.find("*(Source:")
        if cut < 0:
            cut = desc.find("* (Source:")
        if cut >= 0:
            desc = desc[:cut].strip().rstrip("*").strip()
    desc = _client_deliverable_label(desc)
    cat = (item.category or "").strip() or "Fees"

    phase = _client_phase_from_deliverable(item.description or "", fallback=_phase_label_for_line(item))
    generic = not desc or desc.casefold() in {
        "budget line item",
        "labor",
        "fees",
        "fee",
    }
    if generic and item.named_person:
        role = (item.role_title or "Team").strip()
        desc = f"{role} - {item.named_person}"
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
    approach_digest: str = "",
) -> str:
    """Client-facing budget: one total, phase/deliverable fee table, short terms."""
    budget = prepare_budget_for_client_display(budget)
    # Defensive: personnel_loading with priced fixed lines but no hourly rates
    # must not claim an hourly schedule or suppress Fee Detail.
    from app.services.proposal_pricing_service import coerce_budget_to_phased_from_guide

    budget, _coerce_logs = coerce_budget_to_phased_from_guide(
        budget, None, rfp_text=rfp_text
    )
    lines: list[str] = []
    fmt = (budget.budget_format or "").casefold()
    wants_personnel = fmt == "personnel_loading"
    wants_form = not wants_personnel and fmt == "blended_rate_form"
    strict_form = wants_form and rfp_forbids_quotation_form_changes(rfp_text)

    from app.services.proposal_budget_playbook import rfp_mandates_hourly_rate_schedule
    from app.services.proposal_budget_validation import infer_line_item_type

    def _has_priced_fixed_fees() -> bool:
        for item in budget.line_items or []:
            unit = (item.unit or "").casefold()
            if unit in {"hour", "hours", "hr", "hrs"}:
                continue
            if infer_line_item_type(item) in {"direct_expense", "client_passthrough"}:
                continue
            if float(item.extended or 0) > 0:
                return True
            if float(item.rate or 0) > 0 and float(item.quantity or 0) > 0:
                return True
        return False

    rfp_wants_hourly_schedule = (
        wants_personnel or rfp_mandates_hourly_rate_schedule(rfp_text)
    )
    # When RFP also prices phases / fixed fees, keep Fee Detail alongside the
    # hourly instrument (strict per-section: both asks → both blocks).
    also_wants_fee_detail = bool(budget.line_items) and (
        not wants_personnel or _has_priced_fixed_fees()
    )

    if wants_personnel:
        personnel_md = render_personnel_loading_form_markdown(
            budget, rfp_text=rfp_text
        ).rstrip()
        if personnel_md:
            lines.append(personnel_md)
            lines.append("")
        else:
            # Hollow rate table — cut it; fall through to Fee Detail / MANUAL FILL.
            wants_personnel = False
            rfp_wants_hourly_schedule = rfp_mandates_hourly_rate_schedule(rfp_text)
    elif wants_form:
        lines.append(
            render_pricing_proposal_form_markdown(budget, rfp_text=rfp_text).rstrip()
        )
        lines.append("")

    total = _canonical_client_total(budget)
    fees, direct = _professional_fees_and_direct(budget)
    # Fee Detail is client truth for professional fees — never show a higher
    # Proposed Investment than the table rows sum to.
    table_fees = fee_detail_professional_total(budget)
    if table_fees > 0:
        fees = table_fees
    passthrough = round(float(budget.client_media_passthrough or 0), 2)
    if table_fees > 0:
        total = round(fees + direct + passthrough, 2)
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
        lines.append(
            "zö agency structures fees as transparent project phases aligned to the scope "
            "outlined in this proposal."
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

    ql = format_qualifying_language_for_client(
        (budget.qualifying_language or "").strip(),
        line_items=list(budget.line_items or []),
        total=total,
        suppress_mix_tables=bool(budget.line_items) and not wants_personnel,
    )
    if ql:
        if strict_form:
            lines.append(
                "> Supporting terms only — not part of the official Pricing/Quotation form."
            )
            lines.append("")
        lines.append("## Terms")
        lines.append("")
        lines.append(ql)
        lines.append("")

    if also_wants_fee_detail and not wants_personnel:
        heading = (
            "## Fee Detail by Phase" if not wants_form else "## Supporting Fee Detail"
        )
        _append_fee_detail_by_phase_table(lines, budget, heading=heading)
        deploy = _outside_fee_detail_notes_markdown(budget)
        if deploy.strip():
            lines.append(deploy.rstrip())
            lines.append("")
    elif also_wants_fee_detail and wants_personnel and _has_priced_fixed_fees():
        # RFP asked for hourly + fixed/phased dollars — both blocks.
        _append_fee_detail_by_phase_table(
            lines, budget, heading="## Fee Detail by Phase"
        )
        deploy = _outside_fee_detail_notes_markdown(budget)
        if deploy.strip():
            lines.append(deploy.rstrip())
            lines.append("")

    # Classification schedule ONLY when THIS RFP explicitly demands it — never
    # because KB verifiedRates happen to exist on the ledger.
    if not wants_personnel and rfp_wants_hourly_schedule:
        schedule = render_kb_classification_rate_schedule_markdown(
            budget, rfp_text=rfp_text
        )
        if schedule.strip():
            lines.append(schedule.rstrip())
            lines.append("")
        elif rfp_mandates_hourly_rate_schedule(rfp_text) and not _budget_line_has_hourly_rate(
            budget
        ):
            lines.append("## Hourly Rate Schedule by Classification")
            lines.append("")
            lines.append(
                "[MANUAL FILL: Sonja — complete hourly rate schedule by classification "
                "from KB labor/role billable rates; phased fees alone do not satisfy "
                "this RFP ask.]"
            )
            lines.append("")

    # Honest gap when narrative mentions Additional Work hourly but we have no rates.
    scope_cf = (budget.scope_summary or "").casefold()
    if (
        not wants_personnel
        and ("hour" in scope_cf or "hourly" in scope_cf)
        and not _budget_line_has_hourly_rate(budget)
        and rfp_mandates_hourly_rate_schedule(rfp_text)
    ):
        lines.append("## Additional Work — Hourly Rates")
        lines.append("")
        lines.append(
            "[MANUAL FILL: Sonja — labor-category / role hourly rates for Additional "
            "Work outside the annual scope, per RFP. Do not invent rates.]"
        )
        lines.append("")

    # Always rebuild from ledger — never ship truncated/LLM-corrupted option prose
    # (mid-sentence cuts like "Client media pass-through (at net. Total… $2,900").
    from app.services.proposal_budget_validation import rebuild_option_term_notes

    opt2 = rebuild_option_term_notes(budget, rfp_context=rfp_text or "")
    if opt2:
        opt2 = opt2.replace("agency revenue estimate", "proposed fees")
        opt2 = opt2.replace("Agency revenue estimate", "Proposed fees")
        opt2 = re.sub(r"(?i)agency commission revenue", "professional fees", opt2)
        opt2 = re.sub(r"(?i)not agency revenue", "not professional fees", opt2)
        lines.append("## Option Terms")
        lines.append(opt2)
        lines.append("")

    rendered = "\n".join(lines).strip()
    rendered, _mix_logs = scrub_duplicate_budget_breakdown_tables(rendered)
    rendered = scrub_budget_designer_handoff_issues(
        rendered,
        budget=budget,
        approach_digest=approach_digest,
    )
    from app.services.proposal_manuscript import scrub_client_facing_section_artifacts

    rendered = scrub_client_facing_section_artifacts(rendered)
    return _scrub_internal_budget_jargon(rendered) + "\n"


_TBD_NEEDS_INPUT_RE = re.compile(
    r"(?i)\bTBD\s*[—–\-]\s*Needs?\s+your\s+input(?:\s*[—–\-]\s*[^\n|]{0,200})?"
)


def scrub_budget_designer_handoff_issues(
    text: str,
    *,
    budget: ProposalBudget | None = None,
    approach_digest: str = "",
) -> str:
    """Fix designer blockers on Generate Budget handoff — structural only.

    - Convert leaked UI chrome ("TBD — Needs your input") back to MANUAL FILL
    - Deduplicate repeated "Total proposed investment" clauses
    - When ledger has pass-through dollars AND Expenses says absolute all-in with
      zero additional billing, clarify that pass-through sits outside Fee Detail
      (ledger-driven — not a synonym list of RFP phrases)
    """
    del approach_digest  # layout meaning comes from Stage 3 / cost-demands LLM
    body = text or ""
    if not body.strip():
        return body

    def _tbd_repl(match: re.Match[str]) -> str:
        tail = match.group(0)
        detail = re.sub(
            r"(?i)^TBD\s*[—–\-]\s*Needs?\s+your\s+input\s*[—–\-]?\s*",
            "",
            tail,
        ).strip(" .;")
        if detail:
            return f"[MANUAL FILL: Sonja — {detail}]"
        return (
            "[MANUAL FILL: Sonja — confirm this budget cell from ClientList/KB "
            "before submission]"
        )

    body = _TBD_NEEDS_INPUT_RE.sub(_tbd_repl, body)
    body = re.sub(
        r"(?i)\bNeeds?\s+your\s+input\b(?:\s*[—–\-]\s*)?",
        "",
        body,
    )

    seen_total = False

    def _dedupe_total(match: re.Match[str]) -> str:
        nonlocal seen_total
        if seen_total:
            return ""
        seen_total = True
        return match.group(0)

    body = re.sub(
        r"(?i)\*{0,2}Total\s+proposed\s+investment:\*{0,2}\s*\$[\d,]+(?:\.\d{2})?"
        r"(?:\s*\([^)]*\))?\*?\*?",
        _dedupe_total,
        body,
    )
    body = re.sub(r"[ \t]{2,}", " ", body)
    body = re.sub(r"\n{3,}", "\n\n", body)

    passthrough = (
        float(getattr(budget, "client_media_passthrough", 0) or 0) if budget else 0.0
    )
    has_passthrough_lines = False
    if budget is not None:
        from app.services.proposal_budget_validation import infer_line_item_type

        has_passthrough_lines = any(
            infer_line_item_type(item) == "client_passthrough"
            for item in (budget.line_items or [])
        )
    if passthrough > 0 or has_passthrough_lines:
        # Ledger says pass-through exists — Expenses must not claim absolute all-in.
        body = re.sub(
            r"(?is)(###\s+Expenses\s*\n\n)(.*?)(?=\n###\s+|\n##\s+|\Z)",
            lambda m: (
                m.group(1)
                + "Professional fees in Fee Detail are all-in for scoped delivery. "
                "Client pass-through amounts on the ledger (if any) are billed "
                "separately outside that professional-fee Total — not implied as "
                "zero additional billing.\n\n"
            ),
            body,
            count=1,
        )

    return body.strip()


def _budget_line_has_hourly_rate(budget: ProposalBudget) -> bool:
    for item in budget.line_items or []:
        unit = (item.unit or "").casefold()
        if unit in {"hour", "hours", "hr", "hrs"} and float(item.rate or 0) > 0:
            return True
    return False


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
    lines.append("")
    if budget.line_items:
        _append_fee_detail_by_phase_table(
            lines,
            budget,
            heading="### Fee Detail by Phase",
        )
    rendered = "\n".join(lines).strip() + "\n"
    from app.services.proposal_manuscript import scrub_client_facing_section_artifacts

    rendered = scrub_client_facing_section_artifacts(rendered)
    return _scrub_internal_budget_jargon(rendered) + "\n"


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
            r"(Professional\s+(?:services\s+)?fees?)"
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


# A drafted section shorter than this is an outline stub, not real content —
# still worth appending the internal Budget & Pricing tab in that case.
_DRAFTED_PRICING_MIN_CHARS = 200


def rfp_pricing_section_already_drafted(sections: list[ProposalSection]) -> bool:
    """True when the RFP's OWN scored pricing section already has real content.

    find_budget_section_index / budget_section_score exist to pick the right
    WRITE TARGET for the internal budget render and deliberately do not match
    a buyer title like CNM's "SECTION VII — Economy and Price" — broadening
    them was tried and reverted: it made that function's overwrite branch
    treat the section as its target and destroy the drafted VII.1–VII.5
    narrative, the same failure this module's own incorporate_budget_into_draft
    docstring already documents once happening to a different RFP's filled
    form. This check is intentionally separate and narrower: it only answers
    "should a NEW internal Budget & Pricing tab be skipped", never "what
    should be overwritten" — it has no overwrite branch to be dangerous in.

    Reuses proposal_outline_dedup.is_pricing_outline_title — the same
    general, already-vetted pricing-title signal used elsewhere in the
    outline pipeline, not a new one-off pattern for this RFP.
    """
    from app.services.proposal_outline_dedup import is_pricing_outline_title

    return any(
        is_pricing_outline_title(section.title or "")
        and len((section.content or "").strip()) >= _DRAFTED_PRICING_MIN_CHARS
        for section in sections
    )


def ensure_budget_section_present(
    sections: list[ProposalSection],
    budget: ProposalBudget | None,
    *,
    rfp_text: str = "",
) -> tuple[list[ProposalSection], bool]:
    """If the fee tab is missing but canon budget exists, append Budget & Pricing.

    Used after Senior Editor / Scan compact so dedupe or delete tickets cannot
    permanently erase a token-expensive regenerated fee table. Skips entirely
    when the RFP's own scored pricing section is already drafted — see
    rfp_pricing_section_already_drafted.
    """
    if find_budget_section_index(sections) is not None:
        return sections, False
    if rfp_pricing_section_already_drafted(sections):
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
    from app.services.rfp_cost_demands import approach_digest_from_draft_sections

    content = render_budget_markdown(
        budget,
        rfp_text=rfp_text,
        approach_digest=approach_digest_from_draft_sections(sections),
    )
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
    """Rewrite Budget to lead with the pricing agent's budgetFormat instrument.

    Supports blended 3-rate forms and multi-role personnel_loading hourly tables.
    Never overwrite an already-filled official RFQ / Quotation pricing form tab.
    """
    if not budget:
        return None
    fmt = (budget.budget_format or "").casefold()
    if fmt not in {"personnel_loading", "blended_rate_form"}:
        return None
    wants_personnel = fmt == "personnel_loading"
    wants_blended = fmt == "blended_rate_form"
    idx = find_budget_section_index(draft.sections)
    if idx is None:
        return None
    target = draft.sections[idx]
    if section_looks_like_official_pricing_form(target) and official_pricing_form_is_filled(
        target.content or ""
    ):
        return None
    from app.services.rfp_cost_demands import approach_digest_from_draft_sections

    content = render_budget_markdown(
        budget,
        rfp_text=rfp_text,
        approach_digest=approach_digest_from_draft_sections(draft.sections),
    )
    sections = list(draft.sections)
    sections[idx] = sections[idx].model_copy(
        update={"content": content, "status": "generated"}
    )
    if wants_blended and not wants_personnel:
        form_md = render_pricing_proposal_form_markdown(budget, rfp_text=rfp_text)
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


def apply_rfp_required_budget_instrument(
    draft: ProposalDraft,
    budget: ProposalBudget,
    *,
    rfp_text: str,
) -> tuple[ProposalDraft, ProposalBudget, bool]:
    """Render Cost Proposal from the pricing agent's budgetFormat (Generate + Scan).

    Returns (draft, budget, changed). Does not override format via RFP synonym regex.
    """
    fmt = (budget.budget_format or "").casefold()
    if fmt not in {"personnel_loading", "blended_rate_form"}:
        return draft, budget, False
    reshaped = reshape_budget_for_rfp_form(draft, budget, rfp_text=rfp_text)
    if reshaped is None:
        return draft, budget, False
    return reshaped, budget, True



def fill_hollow_pricing_stubs_from_canon_budget(
    draft: ProposalDraft,
    budget: ProposalBudget | None,
    *,
    rfp_text: str = "",
) -> tuple[ProposalDraft, list[str]]:
    """When Senior Editor mints a Rate/Fee stub AFTER Budget phase, fill it now.

    Staging logs: Budget goes green, then coverage audit adds
    ``PROPOSAL RATE/FEE SCHEDULE`` as MANUAL FILL — so Budget never had a chance
    to write that tab. Copy the best already-filled budgetish body, or render
    from the canonical ProposalBudget.
    """
    logs: list[str] = []
    if budget is None and not draft.sections:
        return draft, logs

    from app.services.proposal_draft_structure_stubs import section_is_rfp_draft_stub
    from app.services.proposal_outline_dedup import is_pricing_outline_title
    from app.services.rfp_cost_demands import approach_digest_from_draft_sections

    source = ""
    best_score = -1
    for section in draft.sections:
        title = section.title or ""
        if budget_section_score(title) <= 0 and not is_pricing_outline_title(title):
            continue
        if section_is_rfp_draft_stub(section):
            continue
        body = (section.content or "").strip()
        if len(body) < 200 or not re.search(r"\$\s*[\d,]", body):
            continue
        score = budget_section_score(title)
        if score > best_score:
            best_score = score
            source = body

    if not source and budget is not None:
        source = render_budget_markdown(
            budget,
            rfp_text=rfp_text or "",
            approach_digest=approach_digest_from_draft_sections(draft.sections),
        ).strip()
    if not source:
        return draft, logs

    sections = list(draft.sections)
    changed = False
    for i, section in enumerate(sections):
        title = section.title or ""
        pricingish = (
            budget_section_score(title) >= 8
            or is_pricing_outline_title(title)
            or bool(_DEDICATED_BUDGET_TITLE_RE.search(title))
        )
        if not pricingish or budget_section_score(title) <= 0:
            continue
        if not section_is_rfp_draft_stub(section):
            continue
        sections[i] = section.model_copy(
            update={"content": source, "status": "generated"}
        )
        changed = True
        logs.append(
            f"Filled hollow pricing stub «{title}» from canonical budget "
            "(stub was added after Budget phase)"
        )
    if not changed:
        return draft, logs
    from datetime import datetime, timezone

    updated = draft.model_copy(
        update={
            "sections": sections,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    return updated, logs


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

    budget = normalize_fixed_pricing_narrative(budget, rfp_text=rfp_text)
    from app.services.rfp_cost_demands import approach_digest_from_draft_sections

    approach_digest = approach_digest_from_draft_sections(draft.sections)
    content = render_budget_markdown(
        budget, rfp_text=rfp_text, approach_digest=approach_digest
    )
    try:
        from app.services.rfp_cost_demands import (
            ensure_rfp_cost_demands_in_budget_markdown,
            pricing_flags_for_rfp_cost_demands,
        )

        content, demands, demand_logs = await ensure_rfp_cost_demands_in_budget_markdown(
            content,
            rfp_text=rfp_text or "",
            approach_digest=approach_digest,
            budget=budget,
            rewrite=True,
        )
        for line in demand_logs[:12]:
            logger.info("incorporate_budget rfp_cost_demand: %s", line)
        demand_flags = pricing_flags_for_rfp_cost_demands(demands)
        if demand_flags:
            # Drop prior RFP Cost demand flags, then append current set.
            prior = [
                f
                for f in (budget.pricing_flags or [])
                if not str(f).startswith("RFP Cost demand [")
            ]
            budget = budget.model_copy(
                update={"pricing_flags": prior + demand_flags}
            )
    except Exception:
        logger.warning(
            "incorporate_budget rfp_cost_demands failed rfp_id=%s", rfp_id, exc_info=True
        )
    now = datetime.now(timezone.utc).isoformat()
    sections = list(draft.sections)
    idx = find_budget_section_index(sections)

    if idx is None and rfp_pricing_section_already_drafted(sections):
        # The RFP's own scored pricing section (e.g. "SECTION VII — Economy
        # and Price") already answers the buyer's pricing ask with real
        # content. find_budget_section_index doesn't recognize titles like
        # that as A write target — correctly, since teaching it to would
        # route the OVERWRITE branch below onto that narrative and destroy it
        # (see this function's own docstring for the prior incident that
        # exact mistake caused on a different RFP). This check only
        # suppresses the redundant SECOND tab; it never touches an existing
        # section's content.
        return draft

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
