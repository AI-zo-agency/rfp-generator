"""The Complete & Scan dedupe reviewer must survive unmapped sections.

_requirements_by_section runs at the top of run_complete_scan_senior_reviewer,
before any LLM call, and raised AttributeError on both of its paths:

  * `{m.id: m for m in (research.rfp_sections or []) if research}` evaluates
    research.rfp_sections before the `if research` guard, so research=None raised.
  * the `elif section.requirements` fallback read a field ProposalSection does not
    have, so any section missing from research.rfp_sections raised.

Either raise was swallowed by the FULFILL_STEPS try/except, silently skipping the
"Senior editor review" stage — which is the stage that dedupes the manuscript.
Closing and submission sections added during Scan are never in
research.rfp_sections, so this fired on ordinary drafts.
"""

from __future__ import annotations

from app.models.proposal import (
    ProposalDraft,
    ProposalResearchCache,
    ProposalSection,
    RfpSectionMap,
)
from app.services.proposal_scan_senior_reviewer import _requirements_by_section


def _draft(*section_ids: str) -> ProposalDraft:
    return ProposalDraft(
        rfpId="rfp-1",
        sections=[
            ProposalSection(id=sid, title=f"Section {sid}", content="body")
            for sid in section_ids
        ],
        updatedAt="2026-08-13T00:00:00Z",
    )


def _research(*maps: RfpSectionMap) -> ProposalResearchCache:
    return ProposalResearchCache(
        rfpId="rfp-1",
        updatedAt="2026-08-13T00:00:00Z",
        rfpSections=list(maps),
    )


def test_no_research_cache_returns_empty_not_raises():
    assert _requirements_by_section(_draft("a"), None) == {}


def test_section_absent_from_research_is_skipped_not_raises():
    """The regression: a Scan-added closing tab has no RfpSectionMap entry."""
    research = _research(
        RfpSectionMap(id="a", title="A", requirements=["Provide references"])
    )
    out = _requirements_by_section(_draft("a", "closing-signature"), research)
    assert out == {"a": ["Provide references"]}


def test_mapped_requirements_are_returned():
    research = _research(
        RfpSectionMap(id="a", title="A", requirements=["R1", "R2"]),
        RfpSectionMap(id="b", title="B", requirements=["R3"]),
    )
    out = _requirements_by_section(_draft("a", "b"), research)
    assert out == {"a": ["R1", "R2"], "b": ["R3"]}


def test_mapped_entry_with_empty_requirements_is_omitted():
    research = _research(RfpSectionMap(id="a", title="A", requirements=[]))
    assert _requirements_by_section(_draft("a"), research) == {}
