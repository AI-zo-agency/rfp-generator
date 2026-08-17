"""Canonical zö agency identity facts — single source of truth for Sections 1.x.

Business Information (1.3) and Who We Are (1.1) MUST use the same tenure numbers.
Do not hardcode years in scattered prompts.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

# Verified company fact (Z'Onion Creative Group LLC / zö agency).
AGENCY_FOUNDED_DATE = date(2013, 8, 21)
AGENCY_FOUNDED_DISPLAY = "August 21, 2013"
AGENCY_LEGAL_NAME = "Z'Onion Creative Group LLC"
AGENCY_DBA = "zö agency"
AGENCY_FEIN = "47-4333943"
AGENCY_OFFICE = "220 NW Oregon Ave, Suite 204, Bend, OR 97703"
AGENCY_MAILING = "70 SW Century Drive #1100, Bend, OR 97702"
AGENCY_PHONE = "(541) 678-4048"
AGENCY_EMAIL = "sonja@zo.agency"


def default_business_information_markdown(as_of: date | None = None) -> str:
    """Fallback 1.3 table when generation left a bare header (never ship empty)."""
    years = agency_years_in_operation(as_of)
    return (
        "## Business Information\n\n"
        "| Field | Detail |\n"
        "| --- | --- |\n"
        f"| Legal Name | {AGENCY_LEGAL_NAME} |\n"
        f"| DBA | {AGENCY_DBA} |\n"
        f"| Founded | {AGENCY_FOUNDED_DISPLAY} |\n"
        f"| Years in Operation | {years} |\n"
        f"| Federal EIN (FEIN) | {AGENCY_FEIN} |\n"
        "| Ownership | Women-owned — Sonja Anderson, sole owner |\n"
        f"| Office | {AGENCY_OFFICE} |\n"
        f"| Mailing | {AGENCY_MAILING} |\n"
        f"| Phone | {AGENCY_PHONE} |\n"
        f"| Email | {AGENCY_EMAIL} |\n"
    )


def agency_years_in_operation(as_of: date | None = None) -> int:
    """Calendar years since founding year (matches Business Information convention).

    Founded 2013 → in 2026 this is 13. Do not use anniversary truncation here —
    Who We Are and Business Information must stay in lockstep.
    """
    as_of = as_of or date.today()
    return max(as_of.year - AGENCY_FOUNDED_DATE.year, 0)


def agency_tenure_block(as_of: date | None = None) -> str:
    years = agency_years_in_operation(as_of)
    return (
        f"CANONICAL AGENCY TENURE (mandatory — never contradict):\n"
        f"- Founded: {AGENCY_FOUNDED_DISPLAY}\n"
        f"- Years in operation: {years}\n"
        f"- When mentioning agency experience, ALWAYS say '{years} years' "
        f"(never {years - 1}, never {years + 1}, never 'about/over/nearly').\n"
        f"- Legal name: {AGENCY_LEGAL_NAME}; DBA: {AGENCY_DBA}."
    )


def enforce_agency_tenure(text: str, as_of: date | None = None) -> str:
    """Normalize tenure phrases so 1.1 and 1.3 cannot drift (e.g. 12 vs 13 years)."""
    if not text or not text.strip():
        return text
    years = agency_years_in_operation(as_of)
    out = text

    # Years in Operation table / field
    out = re.sub(
        r"(Years in Operation\s*[:|]\s*)\d+",
        rf"\g<1>{years}",
        out,
        flags=re.IGNORECASE,
    )
    # Founded field — correct wrong year (2012 hallucination)
    out = re.sub(
        r"(Founded\s*[:|]\s*)(?:August\s+21,?\s+)?2012\b",
        rf"\g<1>{AGENCY_FOUNDED_DISPLAY}",
        out,
        flags=re.IGNORECASE,
    )
    out = re.sub(
        r"\b(?:founded|established)\s+in\s+2012\b",
        f"founded in {AGENCY_FOUNDED_DATE.year}",
        out,
        flags=re.IGNORECASE,
    )
    # "12 years of lived experience" / "13 years as zö" etc.
    out = re.sub(
        r"\b(?:over|nearly|almost|about|approximately)\s+\d+\s+years?\b",
        f"{years} years",
        out,
        flags=re.IGNORECASE,
    )
    out = re.sub(
        r"\b\d+\s+years?\s+of\s+lived\s+experience\b",
        f"{years} years of lived experience",
        out,
        flags=re.IGNORECASE,
    )
    out = re.sub(
        r"\b\d+\s+years?\s+(?:total\s+)?as\s+zö\b",
        f"{years} years as zö",
        out,
        flags=re.IGNORECASE,
    )
    out = re.sub(
        r"\bwith\s+\d+\s+years?\s+(?:of\s+)?(?:lived\s+)?experience\b",
        f"with {years} years of lived experience",
        out,
        flags=re.IGNORECASE,
    )
    out = re.sub(
        r"\b\d+\s+years?\s+of\s+(?:agency|marketing)\s+experience\b",
        f"{years} years of agency experience",
        out,
        flags=re.IGNORECASE,
    )
    # Who We Are voice: "zö agency combines 12 years of experience with strategy"
    # Only rewrite years-of-experience in a sentence that is about the agency,
    # never a named specialist bio ("Shawn has 12 years of WordPress…").
    def _agency_sentence_years(sentence: str) -> str:
        if not re.search(
            r"(?i)\b(?:zö|zo)\s+agency\b|\b(?:our|the)\s+agency\b|\bwe\s+combin",
            sentence,
        ):
            return sentence
        return re.sub(
            r"\b\d+\s+years?\s+of\s+experience\b",
            f"{years} years of experience",
            sentence,
            flags=re.IGNORECASE,
        )

    out = re.sub(
        r"[^.!?\n]+(?:[.!?]+|\n+|$)",
        lambda m: _agency_sentence_years(m.group(0)),
        out,
    )
    return out


_TENURE_AUDITOR_TAG_RE = re.compile(
    r"\n*\s*\[(?:MANUAL\s+FILL|VERIFY):[^\]]*"
    r"(?:years?\s+of\s+(?:lived\s+)?experience|years?\s+in\s+operation|"
    r"founded|tenure|resolve fact contradiction[^\]]*(?:year|founded|tenure))"
    r"[^\]]*\]",
    re.IGNORECASE,
)


def strip_tenure_auditor_tags(text: str) -> str:
    """Drop Complete & Clean banners about years — canonical tenure already applies."""
    if not text:
        return text
    cleaned = _TENURE_AUDITOR_TAG_RE.sub("", text)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


def apply_canonical_agency_tenure_to_draft(draft: Any) -> tuple[Any, list[str]]:
    """Lock Sections 1.1 / 1.3 (and agency-voice tenure elsewhere) to companyfacts years."""
    logs: list[str] = []
    sections = []
    changed = False
    for section in getattr(draft, "sections", []) or []:
        body = getattr(section, "content", None) or ""
        sid = getattr(section, "id", "") or ""
        title = (getattr(section, "title", "") or "").casefold()
        is_company_tab = sid in {
            "section-1-who-we-are",
            "section-1-business-info",
        } or "who we are" in title or "business information" in title
        if not body.strip() or (
            not is_company_tab and "zö" not in body.casefold() and "zo agency" not in body.casefold()
        ):
            sections.append(section)
            continue
        updated = enforce_agency_tenure(body)
        if is_company_tab:
            updated = strip_tenure_auditor_tags(updated)
            updated = enforce_agency_tenure(updated)
        if updated != body:
            changed = True
            sections.append(section.model_copy(update={"content": updated}))
            logs.append(f"{getattr(section, 'title', sid) or sid}: canonical agency tenure")
        else:
            sections.append(section)
    if not changed:
        return draft, logs
    return draft.model_copy(update={"sections": sections}), logs


def ticket_is_agency_tenure(text: str) -> bool:
    blob = (text or "").casefold()
    if not blob:
        return False
    return any(
        token in blob
        for token in (
            "years of experience",
            "years in operation",
            "lived experience",
            "founded",
            "tenure",
            "years as zö",
            "years as zo",
        )
    )
