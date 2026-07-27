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

# Strong signals for the actual proposal submission deadline (prefer these).
_PROPOSAL_DEADLINE_RE = re.compile(
    r"(?:"
    r"proposal\s+deadline|"
    r"proposals?\s+(?:are\s+)?due|"
    r"proposal\s+due(?:\s+date)?|"
    r"submission\s+deadline|"
    r"deadline\s+for\s+(?:submissions?|proposals?)|"
    r"closing\s+date|"
    r"bids?\s+(?:due|must\s+be\s+received)|"
    r"responses?\s+due|"
    r"must\s+be\s+received\s+by|"
    r"(?:will\s+be\s+)?received\s+up\s+to|"
    r"will\s+be\s+received|"
    r"submit(?:ted)?\s+by|"
    r"no\s+later\s+than"
    r")"
    r"[\s:.\-]*"
    r"([^\n.;]{6,80})",
    re.I,
)

# Weaker / generic "due date" — often Q&A or pre-bid.
_GENERIC_DUE_RE = re.compile(
    r"(?:"
    r"due\s+date|"
    r"due\s+on|"
    r"due:"
    r")"
    r"[\s:.\-]*"
    r"([^\n.;]{6,48})",
    re.I,
)

# Demote matches whose nearby context is clearly not the proposal deadline.
_DEMOTE_CONTEXT_RE = re.compile(
    r"(?i)\b("
    r"q\s*&\s*a|questions?|pre[- ]?bid|pre[- ]?proposal|"
    r"site\s+visit|conference|addenda?\s+due|intent\s+to\s+bid"
    r")\b"
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


def _window_around(text: str, start: int, end: int, radius: int = 80) -> str:
    return text[max(0, start - radius) : min(len(text), end + radius)]


def _score_match(
    *,
    iso: str,
    priority: int,
    context: str,
) -> tuple[int, str]:
    """Higher score wins. Demote Q&A / pre-bid; prefer proposal-deadline phrasing."""
    score = priority
    if _DEMOTE_CONTEXT_RE.search(context):
        score -= 50
    return score, iso


def extract_due_date_from_text(text: str) -> str | None:
    """Return ISO date (YYYY-MM-DD) when a due-date phrase is found.

    Prefers proposal-deadline wording (e.g. "will be received … on August 31")
    over Q&A / pre-bid due dates that often appear earlier in the notice.
    """
    if not text.strip():
        return None

    scored: list[tuple[int, str]] = []

    for match in _PROPOSAL_DEADLINE_RE.finditer(text):
        parsed = _parse_fragment(match.group(1))
        if not parsed:
            # Cover pages often put the date after "ON" outside a tight capture;
            # also try the next ~80 chars after the phrase.
            parsed = _parse_fragment(text[match.end() : match.end() + 80])
        if parsed:
            ctx = _window_around(text, match.start(), match.end())
            scored.append(_score_match(iso=parsed, priority=100, context=ctx))

    for match in _GENERIC_DUE_RE.finditer(text):
        parsed = _parse_fragment(match.group(1))
        if parsed:
            ctx = _window_around(text, match.start(), match.end())
            scored.append(_score_match(iso=parsed, priority=40, context=ctx))

    if scored:
        scored.sort(key=lambda item: item[0], reverse=True)
        return scored[0][1]

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
