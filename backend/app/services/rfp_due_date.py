"""Extract RFP due dates from uploaded PDF text using regex heuristics."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from app.services.pdf_text import extract_pdf_text_from_bytes

_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "sept": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}

_CONTEXT_RE = re.compile(
    r"(?:"
    r"due\s+date|proposal\s+due(?:\s+date)?|submission\s+deadline|"
    r"deadline\s+for\s+(?:submissions?|proposals?)|closing\s+date|"
    r"bids?\s+(?:due|must\s+be\s+received)|responses?\s+due|"
    r"must\s+be\s+received\s+by|submit(?:ted)?\s+by|no\s+later\s+than"
    r")"
    r"[\s:.\-]*"
    r"([^\n.;]{6,48})",
    re.I,
)

_ISO_RE = re.compile(r"\b(20\d{2})-(\d{1,2})-(\d{1,2})\b")
_SLASH_RE = re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-]((?:20)?\d{2})\b")
_MONTH_NAME_RE = re.compile(
    r"\b(" + "|".join(_MONTHS) + r")\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(20\d{2})\b",
    re.I,
)
_DAY_MONTH_RE = re.compile(
    r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(" + "|".join(_MONTHS) + r")\s+(20\d{2})\b",
    re.I,
)


def _normalize_year(year: int) -> int:
    if year < 100:
        return 2000 + year
    return year


def _to_iso(candidate: date) -> str | None:
    today = date.today()
    if candidate < today - timedelta(days=30):
        return None
    if candidate > today + timedelta(days=730):
        return None
    return candidate.isoformat()


def _parse_date_parts(day: int, month: int, year: int) -> str | None:
    try:
        return _to_iso(date(_normalize_year(year), month, day))
    except ValueError:
        return None


def _parse_fragment(fragment: str) -> str | None:
    text = fragment.strip()
    if not text:
        return None

    match = _ISO_RE.search(text)
    if match:
        return _parse_date_parts(int(match.group(3)), int(match.group(2)), int(match.group(1)))

    match = _MONTH_NAME_RE.search(text)
    if match:
        month = _MONTHS[match.group(1).lower()]
        return _parse_date_parts(int(match.group(2)), month, int(match.group(3)))

    match = _DAY_MONTH_RE.search(text)
    if match:
        month = _MONTHS[match.group(2).lower()]
        return _parse_date_parts(int(match.group(1)), month, int(match.group(3)))

    match = _SLASH_RE.search(text)
    if match:
        a, b, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
        # Try MM/DD first (US RFPs), then DD/MM.
        for day, month in ((b, a), (a, b)):
            parsed = _parse_date_parts(day, month, year)
            if parsed:
                return parsed

    return None


def extract_due_date_from_text(text: str) -> str | None:
    """Return ISO date (YYYY-MM-DD) when a due-date phrase is found."""
    if not text.strip():
        return None

    candidates: list[str] = []
    for match in _CONTEXT_RE.finditer(text):
        parsed = _parse_fragment(match.group(1))
        if parsed:
            candidates.append(parsed)

    if candidates:
        return candidates[0]

    # Fallback: first plausible future date anywhere in the document.
    for pattern in (_ISO_RE, _MONTH_NAME_RE, _DAY_MONTH_RE, _SLASH_RE):
        for match in pattern.finditer(text):
            parsed = _parse_fragment(match.group(0))
            if parsed:
                return parsed

    return None


def extract_due_date_from_pdf_bytes(content: bytes) -> str | None:
    text = extract_pdf_text_from_bytes(content, max_chars=80_000)
    return extract_due_date_from_text(text)
