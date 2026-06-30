"""Generic section completeness checks — word targets and status only, no message patterns."""

from __future__ import annotations

from app.models.proposal import ProposalSection


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
