"""Tests for manuscript locks + internal meta stripping."""

from app.models.proposal import ManuscriptLocks, ProposalDraft, ProposalResearchCache, ProposalSection
from app.services.proposal_manuscript_locks import (
    format_manuscript_locks_block,
    scan_manuscript_lock_issues,
    strip_internal_proposal_meta,
)


def test_strip_leaked_markdown_wrappers() -> None:
    from app.services.proposal_manuscript_locks import strip_leaked_markdown_wrappers

    raw = (
        "retrieval:\n```markdown\n# YOUR KEY TEAM\n\n## JUSTIN BRONSON\n\nBio text.\n```"
    )
    clean = strip_leaked_markdown_wrappers(raw)
    assert "retrieval:" not in clean.lower()
    assert "```" not in clean
    assert "JUSTIN BRONSON" in clean


def test_strip_case_study_meta_and_word_count() -> None:
    text = (
        "Deschutes Brewery Heritage on Tap\n\n"
        "Results\nMessage consistency across 20+ product lines\n\n"
        "---\n\n"
        "*Note: The requested file `03_CS_DeschutesBrewery.pdf` was not present in the "
        "knowledge base. This case study was built from the verified Deschutes Brewery "
        "entry in the Case Study Master reference document. Outcomes are confirmed at "
        "the level documented in that source. If a standalone verified case study PDF "
        "exists, pull additional metrics from it before final submission.*\n\n"
        "336 words\n"
    )
    clean = strip_internal_proposal_meta(text)
    assert "not present" not in clean.lower()
    assert "case study master" not in clean.lower()
    assert "336 words" not in clean.lower()
    assert "Deschutes Brewery" in clean


def test_scan_primary_contact_conflict() -> None:
    locks = ManuscriptLocks(
        primaryContactName="Ron Comer",
        primaryContactTitle="Senior Account Manager",
        requiredKpis=["Mississippi Outdoors Media viewership/subscriptions"],
        updatedAt="2026-07-20T00:00:00Z",
    )
    draft = ProposalDraft(
        rfpId="rfp-1",
        sections=[
            ProposalSection(
                id="section-2-bio-sonja",
                title="Team Bios",
                content=(
                    "Sonja M. Anderson — Agency Director & Primary Account Representative. "
                    "She is the person MDWFP will hear from first every morning."
                ),
            ),
            ProposalSection(
                id="methodology",
                title="Methodology",
                content=(
                    "We assign Ron Comer as Senior Account Manager and primary liaison. "
                    "Brand awareness tracked via annual surveys and website visits."
                ),
            ),
        ],
        updatedAt="2026-07-20T00:00:00Z",
    )
    research = ProposalResearchCache(
        rfpId="rfp-1",
        manuscriptLocks=locks,
        updatedAt="2026-07-20T00:00:00Z",
    )
    issues = scan_manuscript_lock_issues(draft=draft, research=research)
    assert any("Primary contact lock" in (i.message or "") for i in issues)
    assert any("Mississippi Outdoors Media" in (i.message or "") for i in issues)


def test_locks_block_mentions_primary() -> None:
    locks = ManuscriptLocks(
        primaryContactName="Ron Comer",
        primaryContactTitle="Senior Account Manager",
        requiredKpis=["Mississippi Outdoors Media viewership/subscriptions"],
        updatedAt="2026-07-20T00:00:00Z",
    )
    block = format_manuscript_locks_block(locks)
    assert "Ron Comer" in block
    assert "Mississippi Outdoors Media" in block
