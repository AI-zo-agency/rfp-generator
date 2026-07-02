"""Generic section completeness checks — word targets, weakness scoring, improvement gate."""

from __future__ import annotations

import re

from app.models.proposal import ProposalSection

from app.services.proposal_manuscript_cleanup import GRAMMAR_GLITCH_RE

_VERIFY_RE = re.compile(r"\[VERIFY:", re.I)
_STUB_RE = re.compile(
    r"insufficient evidence in corpus|section drafting failed|generation failed|"
    r"error generating|failed to generate|drafting error",
    re.I,
)
_GRAMMAR_GLITCH_RE = GRAMMAR_GLITCH_RE
MIN_WORDS_RATIO = 0.2


def word_count(text: str) -> int:
    return len(text.split())


def effective_word_target(section: ProposalSection) -> int:
    return max(section.word_target or 400, 200)


def min_acceptable_words(section: ProposalSection, *, ratio: float = 0.12) -> int:
    return max(120, int(effective_word_target(section) * ratio))


def section_content_is_substantial(section: ProposalSection, content: str) -> bool:
    if not content.strip():
        return False
    return word_count(content) >= min_acceptable_words(section)


def prior_content_for_redraft(section: ProposalSection) -> tuple[str, bool]:
    """
    Choose what prior text to show the revise agent.
    Returns (prior_text, full_rewrite_mode).
    """
    content = (section.content or "").strip()
    if section_content_is_substantial(section, content) and section.status == "generated":
        return content, False
    if content and word_count(content) >= 40:
        return content, True
    return "", True


def redraft_is_inadequate(
    section: ProposalSection,
    new_content: str,
    *,
    original_content: str,
) -> bool:
    if not new_content.strip():
        return True
    if new_content.strip() == original_content.strip():
        return True
    return not section_content_is_substantial(section, new_content)


def verify_count(text: str) -> int:
    return len(_VERIFY_RE.findall(text))


def is_stub_content(content: str) -> bool:
    return bool(_STUB_RE.search(content))


def weakness_score(section: ProposalSection) -> int:
    content = section.content or ""
    if not content.strip():
        return 1000
    score = verify_count(content) * 25
    if is_stub_content(content):
        score += 200
    if _GRAMMAR_GLITCH_RE.search(content):
        score += 40
    target = effective_word_target(section)
    words = word_count(content)
    if words < int(target * MIN_WORDS_RATIO):
        score += 80
    elif words < int(target * 0.45):
        score += 30
    return score


def is_weak_section(section: ProposalSection) -> bool:
    content = section.content or ""
    if verify_count(content) > 0:
        return True
    if _GRAMMAR_GLITCH_RE.search(content):
        return True
    return weakness_score(section) >= 30


def is_strict_improvement(
    before: ProposalSection,
    after: ProposalSection,
) -> bool:
    """Accept patch only if measurable structural quality improved."""
    if not after.content.strip():
        return False
    if after.content.strip() == before.content.strip():
        return False

    before_score = weakness_score(before)
    after_score = weakness_score(after)
    if after_score < before_score:
        return True

    bv, av = verify_count(before.content), verify_count(after.content)
    bw, aw = word_count(before.content), word_count(after.content)
    target = effective_word_target(before)

    if is_stub_content(before.content) and not is_stub_content(after.content) and aw >= 80:
        return True
    if bv > 0 and av < bv and aw >= bw:
        return True
    if bw < int(target * MIN_WORDS_RATIO) and aw >= int(target * MIN_WORDS_RATIO):
        return True
    if _GRAMMAR_GLITCH_RE.search(before.content) and not _GRAMMAR_GLITCH_RE.search(after.content):
        return True
    return False
