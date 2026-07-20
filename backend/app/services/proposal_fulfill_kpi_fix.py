"""Deterministic contractor KPI fixes — HTA-style four-KPI language → Section 2.3 three KPIs."""

from __future__ import annotations

import re

# Canonical contractor accountability (RFP Section 2.3 — HTA Destination Brand RFP).
_CONTRACTOR_KPI_SENTENCE = (
    "three contractor KPIs under Section 2.3: Total Visitor Arrivals (+3.0% annual growth), "
    "Total Visitor Expenditures (+4.6% annual growth), and Average Islands Visited Per Person "
    "(+0.8% annual growth)"
)

_CONTRACTOR_KPI_PAREN = (
    "three Key Performance Indicators (Total Visitor Arrivals, Total Visitor Expenditures, "
    "and Average Islands Visited Per Person, with the Section 2.3 annual growth targets)"
)

# Long wrong enumerations (agency / strategic-plan scorecard).
_WRONG_ENUM_RE = re.compile(
    r"(?:the\s+)?four\s+(?:HTA\s+)?(?:headline\s+)?KPIs?\s*"
    r"(?:\([^)]*\))?\s*:?\s*"
    r"Resident Sentiment\s*,\s*Visitor Satisfaction\s*,\s*"
    r"Average Daily Visitor Spending\s*,\s*and\s*Total Visitor Expenditures",
    re.I,
)

_WRONG_ENUM_SHORT_RE = re.compile(
    r"Resident Sentiment\s*,\s*Visitor Satisfaction\s*,\s*"
    r"Average Daily Visitor Spending\s*,\s*and\s*Total Visitor Expenditures",
    re.I,
)

_DIRECT_LINE_FOUR_RE = re.compile(
    r"a direct line to one of the four HTA KPIs:\s*"
    r"Resident Sentiment\s*,\s*Visitor Satisfaction\s*,\s*"
    r"Average Daily Visitor Spending\s*,\s*and\s*Total Visitor Expenditures",
    re.I,
)

_FOUR_KPI_BRAND_CERT_RE = re.compile(
    r"four Key Performance Indicators\s*"
    r"\(\s*Resident Sentiment\s*,\s*Visitor Satisfaction\s*,\s*"
    r"Average Daily Visitor Spending\s*,\s*and\s*Total Visitor Expenditures\s*\)",
    re.I,
)

_AGENCY_KPI_MARKERS = (
    "resident sentiment",
    "visitor satisfaction",
    "average daily visitor spending",
    "four headline kpi",
    "four hta kpi",
    "four key performance",
    "one of the four hta kpi",
    "tied back to the four kpi",
    "against the four hta kpi",
    "accountable to four headline kpi",
    "every deliverable tied back to the four kpi",
)

_FOUR_KPI_NEAR_RE = re.compile(
    r"\bfour\b.{0,56}\bkpi",
    re.I,
)


def content_uses_agency_kpi_framework(content: str) -> bool:
    blob = (content or "").casefold()
    if any(m in blob for m in _AGENCY_KPI_MARKERS):
        return True
    if _FOUR_KPI_NEAR_RE.search(content or ""):
        return True
    if _WRONG_ENUM_SHORT_RE.search(content or ""):
        return True
    return False


def apply_contractor_kpi_text_fixes(content: str) -> tuple[str, list[str]]:
    """Replace wrong agency four-KPI language; preserve unrelated prose."""
    if not (content or "").strip():
        return content or "", []

    logs: list[str] = []
    out = content

    subs: list[tuple[re.Pattern[str] | str, str, str]] = [
        (_WRONG_ENUM_RE, _CONTRACTOR_KPI_SENTENCE, "four-KPI enumeration"),
        (_WRONG_ENUM_SHORT_RE, _CONTRACTOR_KPI_PAREN, "agency KPI name list"),
        (_DIRECT_LINE_FOUR_RE, f"accountability to {_CONTRACTOR_KPI_SENTENCE}", "BMP direct-line four KPI"),
        (_FOUR_KPI_BRAND_CERT_RE, _CONTRACTOR_KPI_PAREN, "signature four-KPI certification"),
        (
            re.compile(r"\bevery deliverable tied back to the four KPIs\b", re.I),
            f"every deliverable tied back to {_CONTRACTOR_KPI_SENTENCE}",
            "org four KPI tie-back",
        ),
        (
            re.compile(r"\bWe size both markets against the four HTA KPIs\b", re.I),
            f"We size both markets against {_CONTRACTOR_KPI_SENTENCE}",
            "market knowledge four KPI",
        ),
        (
            re.compile(
                r"\bHTA's contract holds us accountable to four headline KPIs\b",
                re.I,
            ),
            f"HTA's contract holds us accountable to {_CONTRACTOR_KPI_SENTENCE}",
            "activity measures four headline KPI",
        ),
        (
            re.compile(r"\b(?:the\s+)?four\s+(?:HTA\s+)?(?:headline\s+)?KPIs?\b", re.I),
            "the three contractor KPIs (Section 2.3)",
            "four KPI → three contractor KPI",
        ),
    ]

    for pattern, repl, label in subs:
        if isinstance(pattern, str):
            if pattern not in out:
                continue
            out = out.replace(pattern, repl)
            logs.append(f"KPI fix: {label}")
            continue
        if pattern.search(out):
            out = pattern.sub(repl, out)
            logs.append(f"KPI fix: {label}")

    return out, logs
