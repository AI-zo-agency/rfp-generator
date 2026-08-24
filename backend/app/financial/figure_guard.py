"""Checks the model's prose against the figures it was actually handed.

Layer 3 of the figure guard. The first live brief carried three wrong statements
and not one of them contained a digit: `$288,199` was written up as "nearly
three-quarters of a million", a 4.99x payables position became "nearly four
times", and $1,200 of aged receivables became "the bulk of" the book. A guard
built on ``\\d`` would have passed all three and bought nothing but confidence.

So quantities come out of the prose in verbal form as well as digit form, and
get compared against every number present in the evidence.

What this cannot do, written down here rather than discovered later:

- A wrong claim carrying no quantity ("collections are improving") passes.
- A correctly quoted figure attached to the wrong subject passes. The number is
  real; only the sentence around it is wrong.
- The word-number parser has gaps. It covers the scales that turn up in
  financial prose, not every English numeral.

It reduces exposure, it does not remove it. The prohibitions in
`qb_insights._SYSTEM` do most of the work; this is the net underneath them.
"""

from __future__ import annotations

import re
from typing import Any

_ONES = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90,
}
_SCALES = {
    "hundred": 100, "thousand": 1_000, "million": 1_000_000,
    "billion": 1_000_000_000,
}
_NUMBER_WORDS = _ONES | _TENS | _SCALES

_FRACTIONS = {
    "half": 0.5, "halves": 0.5,
    "third": 1 / 3, "thirds": 1 / 3,
    "quarter": 0.25, "quarters": 0.25,
    "fourth": 0.25, "fourths": 0.25,
    "fifth": 0.2, "fifths": 0.2,
}

# A verbal number only counts as a quantity in one of these contexts. Without
# the rule, "one thing to watch" and "the first of these" fail every brief, the
# guard gets switched off within a week, and the net is gone.
_HEDGES = {
    "nearly", "almost", "about", "roughly", "approximately", "around", "over",
    "under", "some", "just", "close", "well", "than",
}
_UNIT_WORDS = {
    "day", "days", "week", "weeks", "month", "months", "year", "years",
    "percent", "times", "dollars", "cents", "invoice", "invoices", "client",
    "clients", "vendor", "vendors", "account", "accounts",
}
_ARTICLES = {"a", "an", "the"}

_WORD_RE = re.compile(r"[A-Za-z]+")
_DIGIT_RE = re.compile(
    r"\$?\s?(\d[\d,]*(?:\.\d+)?)\s*"
    r"(%|x|k|m|bn|hundred|thousand|million|billion)?\b",
    re.IGNORECASE,
)
_DIGIT_SUFFIXES = {
    "k": 1_000, "m": 1_000_000, "bn": 1_000_000_000, "hundred": 100,
    "thousand": 1_000, "million": 1_000_000, "billion": 1_000_000_000,
}

_MAGNITUDE_PHRASES = (
    "the bulk of", "most of", "the majority of", "the lion's share",
)
_SHARE_RE = re.compile(r"\d+\s*%|\bpercent\b", re.IGNORECASE)

# usd() rounds to whole dollars, so a quoted figure may sit half a dollar from
# the raw value it was formatted from. Verbalisation is approximate by nature,
# so a word-resolved value gets far more room — but 750,000 against a real
# 288,199 clears no sane tolerance.
_DIGIT_TOLERANCE = 0.5
_VERBAL_TOLERANCE = 0.15


def _eval_run(words: list[str]) -> float:
    """Evaluate a run of number words. ["seventy", "two"] -> 72."""
    total = 0.0
    current = 0.0
    for word in words:
        if word in _ONES:
            current += _ONES[word]
        elif word in _TENS:
            current += _TENS[word]
        elif word == "hundred":
            current = (current or 1) * 100
        else:
            total += (current or 1) * _SCALES[word]
            current = 0.0
    return total + current


def _digit_quantities(text: str) -> list[tuple[str, float, bool]]:
    out: list[tuple[str, float, bool]] = []
    for match in _DIGIT_RE.finditer(text):
        raw, suffix = match.group(1), (match.group(2) or "").lower()
        try:
            value = float(raw.replace(",", ""))
        except ValueError:  # pragma: no cover — the pattern cannot produce this
            continue
        out.append((match.group(0).strip(), value * _DIGIT_SUFFIXES.get(suffix, 1), False))
    return out


def _verbal_quantities(text: str) -> list[tuple[str, float, bool]]:
    tokens = [(m.group(0).lower(), m.start(), m.end()) for m in _WORD_RE.finditer(text)]
    out: list[tuple[str, float, bool]] = []
    index, count = 0, len(tokens)

    while index < count:
        if tokens[index][0] not in _NUMBER_WORDS and tokens[index][0] not in _FRACTIONS:
            index += 1
            continue

        start = index
        run: list[str] = []
        while index < count and tokens[index][0] in _NUMBER_WORDS:
            run.append(tokens[index][0])
            index += 1

        value = _eval_run(run) if run else 1.0
        has_scale = any(word in _SCALES for word in run)

        # Optional fraction tail: "three-quarters of a million".
        if index < count and tokens[index][0] in _FRACTIONS:
            value *= _FRACTIONS[tokens[index][0]]
            index += 1
            if index < count and tokens[index][0] == "of":
                index += 1
                while index < count and tokens[index][0] in _ARTICLES:
                    index += 1
                if index < count and tokens[index][0] in _SCALES:
                    value *= _SCALES[tokens[index][0]]
                    has_scale = True
                    index += 1
        elif not run:  # pragma: no cover — a fraction word always advances above
            index += 1
            continue

        following = tokens[index][0] if index < count else ""
        preceding = ""
        back = start - 1
        while back >= 0 and tokens[back][0] in _ARTICLES:
            back -= 1
        if back >= 0:
            preceding = tokens[back][0]

        if has_scale or following in _UNIT_WORDS or preceding in _HEDGES:
            out.append((text[tokens[start][1]:tokens[index - 1][2]], value, True))

    return out


def parse_quantities(text: str) -> list[tuple[str, float, bool]]:
    """Every quantity in `text` as (surface_text, value, was_verbal)."""
    return _digit_quantities(text) + _verbal_quantities(text)


def evidence_numbers(evidence: Any) -> set[float]:
    """Every number the model was given, raw or embedded in a formatted string."""
    allowed: set[float] = set()

    def walk(node: Any) -> None:
        if isinstance(node, bool) or node is None:
            return
        if isinstance(node, (int, float)):
            allowed.add(float(node))
        elif isinstance(node, str):
            for _, value, _ in _digit_quantities(node):
                allowed.add(value)
        elif isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, (list, tuple)):
            for value in node:
                walk(value)

    walk(evidence)
    return allowed


def _supported(value: float, allowed: set[float], verbal: bool) -> bool:
    for known in allowed:
        if verbal:
            room = _VERBAL_TOLERANCE * max(abs(known), abs(value), 1.0)
        else:
            room = _DIGIT_TOLERANCE
        if abs(known - value) <= room:
            return True
    return False


def check_quantities(text: str, allowed: set[float]) -> str | None:
    """Return the first quantity in `text` that traces to nothing in `allowed`."""
    for surface, value, verbal in parse_quantities(text):
        if not _supported(value, allowed, verbal):
            return surface
    return None


def check_magnitude_claims(text: str) -> str | None:
    """Return the first "the bulk of"-style claim made without a stated share.

    Deliberately four phrases. A longer list trades a real catch for false
    positives on ordinary prose, and a guard that cries wolf gets turned off.
    """
    normalised = text.replace("’", "'")
    for sentence in re.split(r"(?<=[.!?])\s+", normalised):
        if _SHARE_RE.search(sentence):
            continue
        lowered = sentence.lower()
        for phrase in _MAGNITUDE_PHRASES:
            if phrase in lowered:
                return phrase
    return None
