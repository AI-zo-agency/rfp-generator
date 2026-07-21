"""Hard isolation helpers: a section ticket may only mutate that section."""

from __future__ import annotations

from app.models.proposal import ProposalDraft, ProposalSection


class SectionIsolationError(ValueError):
    """Raised when a redraft would change a non-ticketed section."""


def snapshot_section_contents(draft: ProposalDraft) -> dict[str, str]:
    return {s.id: s.content or "" for s in draft.sections}


def assert_only_section_changed(
    before: dict[str, str],
    after: ProposalDraft,
    *,
    allowed_section_id: str,
) -> None:
    """Ensure every section except allowed_section_id is byte-identical."""
    for section in after.sections:
        prior = before.get(section.id)
        if prior is None:
            if section.id != allowed_section_id:
                raise SectionIsolationError(
                    f"Unexpected new section {section.id!r} while ticketed {allowed_section_id!r}"
                )
            continue
        if section.id == allowed_section_id:
            continue
        if (section.content or "") != prior:
            raise SectionIsolationError(
                f"Section {section.id!r} changed while only {allowed_section_id!r} was ticketed"
            )


def replace_section_isolated(
    draft: ProposalDraft,
    updated: ProposalSection,
) -> ProposalDraft:
    """Replace one section by id; assert no other content drifted."""
    before = snapshot_section_contents(draft)
    if updated.id not in before:
        raise SectionIsolationError(f"Section {updated.id!r} not in draft")
    sections = [updated if s.id == updated.id else s for s in draft.sections]
    next_draft = draft.model_copy(update={"sections": sections})
    assert_only_section_changed(before, next_draft, allowed_section_id=updated.id)
    return next_draft
