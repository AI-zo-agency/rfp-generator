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

# Per-attachment sub-limits are extremely common in real RFPs — "Resumes are
# limited to 2 pages each", "The cover letter is limited to 1 page" — and are
# NOT the whole-document page budget. A verb phrase alone ("is limited to N
# pages") can't tell a sub-limit apart from the real submission-wide cap; the
# regex has no notion of grammatical subject. So a match only counts toward
# the returned limit when the clause it sits in names the submission as a
# whole. Word list per the task ruling: proposal / quote / response /
# submission / bid (+ "quotation" and plural forms as natural variants).
_WHOLE_SUBMISSION_SUBJECT_RE = re.compile(
    r"\b(?:proposals?|quotes?|quotations?|responses?|submissions?|bids?)\b",
    re.IGNORECASE,
)

# A clause boundary — the subject search for a match must not cross into an
# earlier, unrelated sentence (e.g. a document that mentions "the proposal"
# two sentences before an unrelated "Resumes are limited to 2 pages" line
# must not let that earlier mention qualify the resume sub-limit).
_CLAUSE_BOUNDARY_RE = re.compile(r"[.;:\n]")


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


def _clause_before(text: str, start: int) -> str:
    """Text of the current clause up to (not including) ``start`` — from the
    nearest preceding clause boundary, or the start of the string."""
    boundary_end = 0
    for boundary in _CLAUSE_BOUNDARY_RE.finditer(text, 0, start):
        boundary_end = boundary.end()
    return text[boundary_end:start]


def _binds_to_whole_submission(text: str, match_start: int) -> bool:
    """True when the verb phrase at ``match_start`` sits in a clause that
    names the submission as a whole, not a sub-part (resume, cover letter,
    references, letters of support, ...)."""
    return bool(_WHOLE_SUBMISSION_SUBJECT_RE.search(_clause_before(text, match_start)))


def parse_page_limit(rfp_text: str | None) -> int | None:
    """Extract an explicit page cap from RFP solicitation text.

    Returns ``None`` whenever the result would be a guess: no match, a
    number that isn't a plausible page count, more than one distinct limit
    stated in the text (e.g. a narrative cap and a submission-wide cap that
    disagree — picking either would be fabricating certainty the document
    doesn't have), or the only value(s) found are stated for a sub-part of
    the submission (resumes, cover letter, references, an "elliptical"
    second clause like "...limited to 10 pages and the cost volume to 5
    pages") rather than the submission as a whole.
    """
    if not rfp_text or not rfp_text.strip():
        return None

    all_values: set[int] = set()
    qualified_values: set[int] = set()
    for match in _LIMIT_RE.finditer(rfp_text):
        value = _match_to_number(match)
        if value is None:
            continue
        if not (_MIN_PLAUSIBLE_LIMIT <= value <= _MAX_PLAUSIBLE_LIMIT):
            continue
        all_values.add(value)
        if _binds_to_whole_submission(rfp_text, match.start()):
            qualified_values.add(value)

    # More than one distinct number anywhere in the text is always treated
    # as too uncertain to act on — unchanged from the original design; this
    # branch doesn't need subject information because ANY disagreement
    # (whole-document vs. whole-document, or whole-document vs. sub-part)
    # is a reason to fail closed rather than guess which one governs.
    if len(all_values) != 1:
        return None

    (value,) = all_values
    # The single value in the text only counts if at least one of its
    # mentions was bound to the whole submission. This also fixes a second,
    # narrower defect for free: two DIFFERENT sub-limits that happen to
    # share a number (e.g. "the appendix is limited to 10 pages" ... "the
    # technical narrative is limited to 10 pages") no longer silently
    # collapse into a false non-ambiguous 10 — neither mention qualifies,
    # so this returns None instead.
    if value in qualified_values:
        return value
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
