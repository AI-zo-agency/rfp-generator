"""Decide whether a proposal section is genuinely drafted or a dead placeholder.

Phase 3 writes a short ``[VERIFY: ...]`` stub when a section cannot be drafted.
Those stubs are non-empty, so ``content.strip()`` reports them as drafted, and
comparing against a single canonical constant misses punctuation variants that
already exist in stored drafts (an older writer used a comma where the current
constant uses an em dash).

This module is the single source of truth for that question. Read paths should
call it instead of comparing against ``SECTION_DRAFT_FAILURE_PLACEHOLDER``.
Writers keep using the constant.

Mirrored in ``frontend/src/lib/proposal-section-health.ts``; the two are kept in
step by a shared fixture asserted in both test suites.
"""

from __future__ import annotations

import re
from enum import Enum


class SectionHealth(str, Enum):
    """Why a section holds no usable draft."""

    #: Phase 3 raised an LlmError; content was never written.
    DRAFT_FAILED = "draft_failed"
    #: The writer returned nothing usable, or the corpus had no evidence.
    NO_EVIDENCE = "no_evidence"
    #: Headings and placeholder tags, but no actual prose.
    PLACEHOLDER_ONLY = "placeholder_only"
    #: Whitespace only.
    EMPTY = "empty"


# Separator between the two halves of the failure sentinel. Stored drafts contain
# both an em dash (current constant) and a comma (older writer), so accept any
# run of punctuation or whitespace. The trailing "-" is literal inside the class.
_SEP = r"[\s,;:—–-]+"

_DRAFT_FAILED_RE = re.compile(
    rf"^\[\s*VERIFY:\s*section\s+drafting\s+failed{_SEP}"
    r"needs\s+manual\s+regeneration\s*\]$",
    re.I | re.S,
)

_NO_EVIDENCE_RE = re.compile(
    r"^\[\s*VERIFY:\s*draft\s+content\s+for\s+.+?"
    r"(?:insufficient\s+evidence\s+in\s+corpus|writer\s+returned\s+empty\s+prose)",
    re.I | re.S,
)


def _whole_body_tag(text: str) -> str | None:
    """Return ``text`` when it is exactly one bracketed tag and nothing else.

    This is the rule that protects real work. Drafted sections routinely contain
    inline ``[VERIFY: ...]`` chips; only a section whose *entire* body is a single
    tag is a placeholder. Anything followed by prose is a real draft.

    A tag body containing its own "]" fails this check and is reported as
    drafted — deliberately the safe direction, since the cost of missing a dead
    section is a refusal, while the cost of a false positive is overwriting
    finished content.
    """
    if not text.startswith("["):
        return None
    if text.find("]") != len(text) - 1:
        return None
    return text


_PLACEHOLDER_TAG_RE = re.compile(
    r"\[(?:MANUAL\s+FILL|VERIFY|PLACEHOLDER|INSERT|TBD)\b[^\]]*\]", re.I
)
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s.*$", re.M)
#: A line that is entirely bold ("**Challenge**") is a label, not prose.
_BOLD_LABEL_RE = re.compile(r"^\*\*[^*]+\*\*[:.]?$")

#: Words a single line needs before it counts as real prose rather than a label.
#: Deliberately low: a short but genuine sentence ("We accept the terms in Exhibit
#: A. No exceptions are taken.") must survive, while "**Challenge**" must not.
_MIN_PROSE_WORDS_PER_LINE = 4


def _has_section_label(text: str) -> bool:
    """True when the body carries a markdown heading or a bold label line.

    A dead section still has its skeleton ("### Title", "**Challenge**"). A terse
    but real line such as "Role: [MANUAL FILL: Title]" has neither.
    """
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if _HEADING_RE.match(line) or _BOLD_LABEL_RE.match(line):
            return True
    return False


def _has_prose_line(text: str) -> bool:
    """True when at least one line carries real prose.

    Structural rather than length-based. Counting words across the whole body
    misclassified short but genuine sections — an 11-word acknowledgment with two
    inline [VERIFY] chips looked identical to a stub — and a false positive here
    means regenerating over finished work.
    """
    for raw_line in text.splitlines():
        line = _PLACEHOLDER_TAG_RE.sub(" ", raw_line).strip()
        if not line:
            continue
        # "### Heading" and "**Challenge**" are both labels, not prose.
        if _HEADING_RE.match(line) or _BOLD_LABEL_RE.match(line):
            continue
        line = re.sub(r"[*_`>#|~-]", " ", line)
        # Count only tokens carrying a letter or digit: "Solution / Our Approach"
        # is three words, not four, and must not read as prose.
        words = [w for w in line.split() if re.search(r"[A-Za-z0-9]", w)]
        if len(words) >= _MIN_PROSE_WORDS_PER_LINE:
            return True
    return False


def classify_section_health(content: str | None) -> SectionHealth | None:
    """Classify a section body. ``None`` means the section holds a real draft."""
    text = (content or "").strip()
    if not text:
        return SectionHealth.EMPTY

    tag = _whole_body_tag(text)
    if tag is not None:
        if _DRAFT_FAILED_RE.match(tag):
            return SectionHealth.DRAFT_FAILED
        if _NO_EVIDENCE_RE.match(tag):
            return SectionHealth.NO_EVIDENCE
        return None

    # A skeleton: section headings, placeholder tags, and no prose between them.
    # Such a section has to be written, not value-filled.
    #
    # All three conditions are required. Dropping the heading requirement made
    # "Role: [MANUAL FILL: Title]" — a real one-line section legitimately awaiting
    # a value — look dead, which hijacked the MANUAL FILL path into a full
    # regeneration and discarded the surrounding sentence.
    if (
        _has_section_label(text)
        and _PLACEHOLDER_TAG_RE.search(text)
        and not _has_prose_line(text)
    ):
        return SectionHealth.PLACEHOLDER_ONLY

    return None


def is_dead_section(content: str | None) -> bool:
    """True when the section holds no usable draft. Includes never-populated ones.

    Use for display ("is this drafted?"). For deciding whether to re-run drafting,
    use :func:`is_failed_draft_stub` instead.
    """
    return classify_section_health(content) is not None


def is_failed_draft_stub(content: str | None) -> bool:
    """True when drafting ran and left a failure stub behind.

    Deliberately excludes empty sections. An empty section is handled by the
    normal chat edit path — the user asking to write it means "write it", and
    hijacking that into an isolated Phase 3 redraft would change long-standing
    behaviour (see tests/test_chat_redraft_failed_section.py).
    """
    return classify_section_health(content) in (
        SectionHealth.DRAFT_FAILED,
        SectionHealth.NO_EVIDENCE,
        SectionHealth.PLACEHOLDER_ONLY,
    )


def is_section_drafted(content: str | None) -> bool:
    """True when the section holds real drafted content."""
    return classify_section_health(content) is None
