"""Parse an explicit page limit out of RFP solicitation text.

Nothing previously extracted "quotes are limited to 12 pages" from the RFP
body: ``RfpRecord.page_limit`` was populated only from a manual upload-form
field, so ``page_limit`` was ``None`` for every RFP synced from JustWin or
uploaded without a human filling that field in — which meant the entire
page-budget allocation system in ``proposal_drafting_graph``/
``proposal_generator`` was inert (see task-4-brief.md).

The overriding design constraint: a FALSE positive here is worse than a
false negative. Returning a limit that isn't real truncates a proposal
mid-generation; returning ``None`` when a limit did exist just leaves the
document unconstrained, same as today. So every rule below is written to
fail closed — when a match is ambiguous or the surrounding words don't
clearly express a limit, this returns ``None`` rather than guessing.
"""

from __future__ import annotations

import re

# Page limits worth acting on are always small, double- or low-triple-digit
# numbers. Rejecting anything outside this range guards against a phone
# number, dollar figure, or section number that happens to sit next to the
# word "pages" some other way slipping through.
_MIN_PLAUSIBLE_LIMIT = 1
_MAX_PLAUSIBLE_LIMIT = 500

_NUMBER_WORDS: dict[str, int] = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
}

_TENS_WORDS = {
    k: v for k, v in _NUMBER_WORDS.items() if v >= 20 and v % 10 == 0
}
_ONES_WORDS = {k: v for k, v in _NUMBER_WORDS.items() if v < 10}


def _word_to_number(word: str) -> int | None:
    """"twenty" -> 20, "twenty-five" -> 25. None if not a recognized number word."""
    word = word.strip().lower()
    if word in _NUMBER_WORDS:
        return _NUMBER_WORDS[word]
    if "-" in word:
        tens_part, _, ones_part = word.partition("-")
        if tens_part in _TENS_WORDS and ones_part in _ONES_WORDS:
            return _TENS_WORDS[tens_part] + _ONES_WORDS[ones_part]
    return None


# A "number phrase" the limit can bind to: plain digits, a spelled-out number
# optionally annotated with a parenthetical digit form ("twenty (20)"), or a
# spelled-out number on its own ("twenty pages"). Digits win when present —
# they're the unambiguous, human-proofread form.
_NUMBER_PHRASE = r"""
    (?:
        (?P<digits>\d{1,3})
      |
        (?P<numword>[A-Za-z]+(?:-[A-Za-z]+)?)
        \s*
        (?:\(\s*(?P<paren>\d{1,3})\s*\))?
    )
"""

# Verb phrases that express a hard cap, not a passing mention. Deliberately
# excludes bare mentions like "page limit" or "N-page" on their own — those
# show up in sentences that are *about* the limit without stating one
# ("excluded from the page limit"), which must not parse.
_LIMIT_VERB = r"""
    (?:
        not\ to\ exceed
      | shall\ not\ exceed
      | must\ not\ exceed
      | may\ not\ exceed
      | will\ not\ exceed
      | cannot\ exceed
      | shall\ be\ limited\ to
      | is\ limited\ to
      | are\ limited\ to
      | limited\ to
      | capped\ at
      | no\ more\ than
      | not\ more\ than
      | maximum\ of
    )
"""

# Optional connective filler between the verb and the number, e.g.
# "limited to a maximum of twenty (20) pages".
_CONNECTIVE = r"(?:\s+(?:a\s+)?(?:maximum\s+of\s+)?(?:a\s+)?)?"

_LIMIT_RE = re.compile(
    rf"""
    {_LIMIT_VERB}
    {_CONNECTIVE}
    \s*
    {_NUMBER_PHRASE}
    \s*-?\s*
    pages?\b
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _match_to_number(match: re.Match[str]) -> int | None:
    digits = match.group("digits")
    if digits:
        return int(digits)
    paren = match.group("paren")
    if paren:
        return int(paren)
    numword = match.group("numword")
    if numword:
        return _word_to_number(numword)
    return None


def parse_page_limit(rfp_text: str | None) -> int | None:
    """Extract an explicit page cap from RFP solicitation text.

    Returns ``None`` whenever the result would be a guess: no match, a
    number that isn't a plausible page count, or more than one distinct
    limit stated in the text (e.g. a narrative cap and a submission-wide
    cap that disagree — picking either would be fabricating certainty the
    document doesn't have).
    """
    if not rfp_text or not rfp_text.strip():
        return None

    found: set[int] = set()
    for match in _LIMIT_RE.finditer(rfp_text):
        value = _match_to_number(match)
        if value is None:
            continue
        if _MIN_PLAUSIBLE_LIMIT <= value <= _MAX_PLAUSIBLE_LIMIT:
            found.add(value)

    if len(found) == 1:
        return found.pop()
    return None


def resolve_page_limit(
    manual_page_limit: int | None,
    rfp_text: str | None,
) -> int | None:
    """The effective page limit: the manual upload-form value when present,
    otherwise whatever ``parse_page_limit`` can safely extract from the RFP
    text. A human who typed a number into the form always wins — they may
    know something the text can't say unambiguously (e.g. two conflicting
    limits that a person resolved by reading the whole document).
    """
    if manual_page_limit is not None and manual_page_limit > 0:
        return manual_page_limit
    return parse_page_limit(rfp_text)
