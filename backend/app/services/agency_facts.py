"""Canonical zö agency identity facts — single source of truth for Sections 1.x.

Business Information (1.3) and Who We Are (1.1) MUST use the same tenure numbers.
Do not hardcode years in scattered prompts.
"""

from __future__ import annotations

import re
from datetime import date

# Verified company fact (Z'Onion Creative Group LLC / zö agency).
AGENCY_FOUNDED_DATE = date(2013, 8, 21)
AGENCY_FOUNDED_DISPLAY = "August 21, 2013"
AGENCY_LEGAL_NAME = "Z'Onion Creative Group LLC"
AGENCY_DBA = "zö agency"


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
    return out
