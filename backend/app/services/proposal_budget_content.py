"""Render Stage 3 budget into proposal section content and sync to draft."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from app.models.pricing_instrument import PricingInstrument
from app.models.proposal import BudgetLineItem, ProposalBudget, ProposalDraft, ProposalSection
from app.services.proposal_repository import aget_proposal_draft, asave_proposal_draft

logger = logging.getLogger(__name__)

_BUDGET_TITLE_PATTERN = re.compile(
    r"\b(budget|pricing|price\s*proposal|fee\s*schedule|cost\s*proposal|compensation)\b",
    re.I,
)

def _usd(value: float | None) -> str:
    if value is None:
        return "—"
    if abs(value - round(value)) < 0.01:
        return f"${value:,.0f}"
    return f"${value:,.2f}"


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
        role = (item.role_title or "").strip() or (item.description or "").strip()
        # Strip LLM mapping chrome before client schedule.
        role = re.split(r"\s*[—–]\s*", role, maxsplit=1)[0].strip()
        role = re.sub(r"(?i)\s+classification\s*$", "", role).strip()
        role = re.sub(r"\s*\([^)]*$", "", role).strip()
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
    # Buyer fee tabs often title this "Compensation and Payment Schedule"
    # (not workers' compensation — that returns score 0 above).
    r"compensation\s+and\s+payment(?:\s+schedule)?|"
    r"payment\s+and\s+compensation(?:\s+schedule)?|"
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


def _sync_year1_investment_phrase(text: str, total: float) -> str:
    """Keep 'Year 1 investment of $X' on the canonical client total."""
    if not text or total <= 0:
        return text
    return _YEAR1_INVESTMENT_RE.sub(lambda m: f"{m.group(1)}{_usd(total)}", text)


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


def render_budget_markdown(
    budget: ProposalBudget,
    *,
    rfp_text: str = "",
    approach_digest: str = "",
    pricing_instrument: PricingInstrument | None = None,
) -> str:
    """Cost section markdown from the pricing plan.

    Returns "" for budgets without a pricing plan (pre-v2, frozen as saved).
    Contract for every caller: "" means leave the Cost section as it is.
    """
    del rfp_text, approach_digest, pricing_instrument  # kept for caller kwargs
    if budget.pricing_plan:
        from app.services.pricing_plan_service import render_pricing_plan_budget

        return render_pricing_plan_budget(budget)
    return ""


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
    # figure copying the grand total) through untouched.
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


async def incorporate_budget_into_draft(
    rfp_id: str,
    budget: ProposalBudget,
    *,
    rfp_text: str = "",
    pricing_instrument: PricingInstrument | None = None,
) -> ProposalDraft | None:
    """Write generated budget into the best-matching proposal section (or append one).

    Never overwrite a filled official RFQ / Quotation Pricing Form — Phase 3.5 /
    Continue Proposal was wiping DuPage contact fields by treating that tab as
    the Budget & Pricing write target.
    """
    draft = await aget_proposal_draft(rfp_id)
    if not draft:
        return None

    content = render_budget_markdown(
        budget,
        rfp_text=rfp_text,
        pricing_instrument=pricing_instrument,
    )
    if not content.strip():
        return draft  # no pricing plan: Cost section stays as saved
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
        from app.services.pricing_delivery_context import is_buyer_pricing_form_instrument

        # Typed buyer form → always overwrite the Pricing Form tab. The "filled
        # official form" protect path exists to stop Fee Detail narrative from
        # wiping a buyer form — not to block deterministic instrument re-render
        # (or leave a stale FEIN worksheet in place).
        protect_filled_form = (
            not is_buyer_pricing_form_instrument(instrument=pricing_instrument)
            and section_looks_like_official_pricing_form(target)
            and official_pricing_form_is_filled(target.content or "")
        )
        if protect_filled_form:
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


