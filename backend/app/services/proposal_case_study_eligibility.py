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
)


def is_eligible_section3_case_study_title(title: str | None) -> bool:
    """Return False for mega-dumps and non-case-study KB artefacts."""
    text = (title or "").strip()
    if not text:
        return False
    for pattern in _INELIGIBLE_PATTERNS:
        if pattern.search(text):
            return False
    return True
