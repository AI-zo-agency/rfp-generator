"""Sections that Scan RFP must not rewrite — add missing pieces only."""

from __future__ import annotations

from app.models.proposal import ProposalSection

_BIO_PLACEHOLDER = "section-2-bio-placeholder"
_MIN_BIO_CHARS = 120
_MIN_CASE_STUDY_CHARS = 350


def is_team_bio_section(section_id: str) -> bool:
    return section_id.startswith("section-2-bio-") and section_id != _BIO_PLACEHOLDER


def is_case_study_section(section_id: str) -> bool:
    return section_id.startswith("section-3-work-") and section_id != "section-3-work-placeholder"


def fulfill_scan_preserves_section(section: ProposalSection) -> bool:
    """True when Scan RFP & add missing pieces must not replace existing body text."""
    body = (section.content or "").strip()
    if not body:
        return False
    if is_team_bio_section(section.id) and len(body) >= _MIN_BIO_CHARS:
        return True
    if is_case_study_section(section.id) and len(body) >= _MIN_CASE_STUDY_CHARS:
        return True
    return False


def fulfill_scan_preserve_bio_and_case_study_ids(draft: "ProposalDraft") -> set[str]:
    """Section ids Scan RFP must not LLM-rewrite (team bios + case studies only)."""
    return {s.id for s in draft.sections if fulfill_scan_preserves_section(s)}


def section_id_preserved_in_fulfill(section_id: str, draft_sections: list[ProposalSection]) -> bool:
    section = next((s for s in draft_sections if s.id == section_id), None)
    if not section:
        return False
    return fulfill_scan_preserves_section(section)
