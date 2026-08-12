"""Guardrails: Section 3 picks real single-project case studies only."""

from __future__ import annotations

import re

# Master dumps / templates that look like "case studies" in search but must never
# become a Section 3 card — they cause fidelity wipes and wrong tabs.
_INELIGIBLE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"all\s*case\s*stud", re.I),
    re.compile(r"allcasestud", re.I),
    re.compile(r"master\s*template", re.I),
    re.compile(r"org\s*structure", re.I),
    re.compile(r"all\s*team\s*bios?", re.I),
    re.compile(r"02_mastertemplate", re.I),
    re.compile(r"company\s*facts?", re.I),
    re.compile(r"01_company", re.I),
    re.compile(r"00_guide", re.I),
    re.compile(r"pricing\s*guide", re.I),
    re.compile(r"filing\s*guide", re.I),
    re.compile(r"06_won_", re.I),
    re.compile(r"07_fin_", re.I),
)


def is_eligible_section3_case_study_title(
    title: str | None,
    *,
    rfp_title: str = "",
    rfp_sector: str = "",
) -> bool:
    """Return False for mega-dumps and non-case-study KB artefacts.

    Also blocks personal-brand / private financial-advisor studies on
    government / civic public-education RFPs (Infinite Assets pattern).
    """
    text = (title or "").strip()
    if not text:
        return False
    for pattern in _INELIGIBLE_PATTERNS:
        if pattern.search(text):
            return False
    rfp_blob = f"{rfp_title} {rfp_sector}".casefold()
    civic = any(
        token in rfp_blob
        for token in (
            "government",
            "municipal",
            "public education",
            "ballot",
            "charter",
            "transit",
            "transportation authority",
            "city of",
            "county",
            "nycedc",
            "coge",
        )
    )
    if civic and re.search(
        r"infinite\s+assets|personal\s+brand|keynote\s+speaker|"
        r"financial\s+advisor|thought\s+leader.*coach",
        text,
        re.I,
    ):
        return False
    return True
