"""Unit tests for _explode_response_format_containers and
split_response_format_container_sections (no LLM, no network, no async)."""

from app.models.proposal import ProposalDraft, ProposalSection
from app.services.proposal_fulfill_rfp_structure import (
    RfpSectionSpec,
    _explode_response_format_containers,
    split_response_format_container_sections,
)


def _draft(sections: list[ProposalSection]) -> ProposalDraft:
    return ProposalDraft(rfpId="rfp-1", sections=sections, updatedAt="2026-01-01T00:00:00Z")


def _non_whitespace_len(draft: ProposalDraft) -> int:
    return sum(len("".join((s.content or "").split())) for s in draft.sections)


def test_real_world_container_explodes_in_place():
    before = RfpSectionSpec(rfp_title="A. General Instructions")
    container = RfpSectionSpec(
        rfp_title="D. Required Elements in Response/Response Format",
        required_headings=[
            "1. Cover Letter",
            "2. Experience and Capability",
            "3. References",
        ],
    )
    after = RfpSectionSpec(rfp_title="Cost Information")

    specs, logs = _explode_response_format_containers([before, container, after])

    titles = [s.rfp_title for s in specs]
    assert titles == [
        "A. General Instructions",
        "1. Cover Letter",
        "2. Experience and Capability",
        "3. References",
        "Cost Information",
    ]
    assert specs[0] is before
    assert specs[-1] is after
    for promoted in specs[1:4]:
        assert promoted.required_headings == []
        assert promoted.same_ask_as == []
    assert any("exploded container" in log for log in logs)


def test_container_without_headings_is_preserved_with_advisory_log():
    container = RfpSectionSpec(rfp_title="Response Format", required_headings=[])

    specs, logs = _explode_response_format_containers([container])

    assert specs == [container]
    assert any(
        "looks like a response-format instruction heading with no enumerated items"
        in log
        for log in logs
    )


def test_ordinary_deliverable_spec_is_untouched():
    spec = RfpSectionSpec(rfp_title="Cost Information")

    specs, logs = _explode_response_format_containers([spec])

    assert specs == [spec]
    assert logs == []


def test_promoted_title_deduplicated_against_existing_spec():
    existing = RfpSectionSpec(rfp_title="Cover Letter")
    container = RfpSectionSpec(
        rfp_title="Required Elements in Response",
        required_headings=["Cover Letter", "References"],
    )

    specs, _logs = _explode_response_format_containers([existing, container])

    titles = [s.rfp_title for s in specs]
    assert titles.count("Cover Letter") == 1
    assert "References" in titles


def test_promoted_rows_keep_mandated_submission_format_from_container():
    container = RfpSectionSpec(
        rfp_title="Required Elements in Response",
        required_headings=["Cover Letter", "References"],
        mandated_submission_format=True,
    )

    specs, _logs = _explode_response_format_containers([container])

    assert len(specs) == 2
    assert all(s.mandated_submission_format is True for s in specs)


def test_child_duplicating_a_later_scored_row_is_not_promoted_twice():
    # The container lists "Experience and Capability"; the scored extract
    # already contributed "F.1 — Experience and Capability" AFTER it. Both
    # must not become tabs — the buyer's scored wording wins.
    specs = [
        RfpSectionSpec(
            rfp_title="D. Required Elements in Response/Response Format",
            required_headings=["1. Cover Letter", "2. Experience and Capability"],
        ),
        RfpSectionSpec(rfp_title="F.1 — Experience and Capability"),
    ]
    out, _logs = _explode_response_format_containers(specs)
    titles = [s.rfp_title for s in out]
    assert "1. Cover Letter" in titles
    assert sum(1 for t in titles if "Experience and Capability" in t) == 1, titles
    assert "F.1 — Experience and Capability" in titles


CONTAINER_CONTENT = (
    "**1. Cover Letter**\n\n"
    "We are pleased to submit this proposal for your review and consideration.\n\n"
    "**2. References**\n\n"
    "Reference One — Contact: jane@example.com\n"
    "Reference Two — Contact: bob@example.com\n"
)


def _cover_letter_and_references_specs() -> list[RfpSectionSpec]:
    return [
        RfpSectionSpec(rfp_title="Cover Letter"),
        RfpSectionSpec(rfp_title="References"),
    ]


def test_real_container_splits_into_two_sections_container_removed():
    container = ProposalSection(
        id="rfp-structure-required-elements",
        title="D. Required Elements in Response/Response Format",
        content=CONTAINER_CONTENT,
        source="generated",
        mode="write",
        status="generated",
    )
    draft = _draft([container])
    before_len = _non_whitespace_len(draft)

    new_draft, logs = split_response_format_container_sections(
        draft, _cover_letter_and_references_specs()
    )

    titles = [s.title for s in new_draft.sections]
    assert titles == ["1. Cover Letter", "2. References"]
    assert all(s.id != container.id for s in new_draft.sections)
    cover = new_draft.sections[0]
    refs = new_draft.sections[1]
    assert "We are pleased to submit" in cover.content
    assert "Reference One" in refs.content
    assert "Reference Two" in refs.content
    assert any("→ 2 section(s)" in log for log in logs)
    assert _non_whitespace_len(new_draft) == before_len


def test_preamble_moves_out_and_the_container_tab_is_dropped():
    content = (
        "Please respond using the following structure and format.\n\n"
        + CONTAINER_CONTENT
    )
    container = ProposalSection(
        id="rfp-structure-required-elements",
        title="Response Format",
        content=content,
        source="generated",
        mode="write",
        status="generated",
    )
    draft = _draft([container])
    before_len = _non_whitespace_len(draft)

    new_draft, logs = split_response_format_container_sections(
        draft, _cover_letter_and_references_specs()
    )

    # The container is the RFP's instruction heading, not a deliverable: a
    # leading preamble must not keep that tab alive. The preamble rides out with
    # the first deliverable so nothing is lost, and the bogus tab is dropped.
    ids = [s.id for s in new_draft.sections]
    assert container.id not in ids
    titles = [s.title for s in new_draft.sections]
    assert titles == ["1. Cover Letter", "2. References"]
    cover = new_draft.sections[0]
    assert "Please respond using the following structure" in cover.content
    assert "Dear" in cover.content or "Cover Letter" in cover.content
    assert any("preamble moved into" in log for log in logs)
    assert any("→ 2 section(s)" in log for log in logs)
    assert _non_whitespace_len(new_draft) == before_len


def test_collision_with_nonempty_existing_section_stays_in_container():
    existing_cover_letter = ProposalSection(
        id="cover-letter-existing",
        title="Cover Letter",
        content="Already-drafted, real cover letter prose that must not be clobbered.",
        source="generated",
        mode="write",
        status="generated",
    )
    container = ProposalSection(
        id="rfp-structure-required-elements",
        title="Response Format",
        content=CONTAINER_CONTENT,
        source="generated",
        mode="write",
        status="generated",
    )
    draft = _draft([existing_cover_letter, container])
    before_len = _non_whitespace_len(draft)

    new_draft, logs = split_response_format_container_sections(
        draft, _cover_letter_and_references_specs()
    )

    unchanged_existing = next(
        s for s in new_draft.sections if s.id == "cover-letter-existing"
    )
    assert unchanged_existing.content == existing_cover_letter.content
    kept_container = next(s for s in new_draft.sections if s.id == container.id)
    assert "We are pleased to submit" in kept_container.content
    assert any("already exists as a non-empty tab" in log for log in logs)
    # References still split out since it did not collide.
    assert any(s.title == "2. References" for s in new_draft.sections)
    assert _non_whitespace_len(new_draft) == before_len


def test_empty_target_section_is_filled_in_place_no_duplicate():
    empty_cover_letter = ProposalSection(
        id="cover-letter-empty",
        title="Cover Letter",
        content="",
        source="generated",
        mode="write",
        status="empty",
    )
    container = ProposalSection(
        id="rfp-structure-required-elements",
        title="Response Format",
        content=CONTAINER_CONTENT,
        source="generated",
        mode="write",
        status="generated",
    )
    draft = _draft([empty_cover_letter, container])
    before_len = _non_whitespace_len(draft)

    new_draft, logs = split_response_format_container_sections(
        draft, _cover_letter_and_references_specs()
    )

    assert len(new_draft.sections) == 2
    filled = next(s for s in new_draft.sections if s.id == "cover-letter-empty")
    assert "We are pleased to submit" in filled.content
    # No duplicate "Cover Letter" tab was minted.
    cover_letter_titles = [
        s for s in new_draft.sections if s.title and "Cover Letter" in s.title
    ]
    assert len(cover_letter_titles) == 1
    assert all(s.id != container.id for s in new_draft.sections)
    assert _non_whitespace_len(new_draft) == before_len


def test_fewer_than_two_mapped_headings_leaves_section_untouched():
    content = "**1. Cover Letter**\n\nWe are pleased to submit this proposal.\n"
    container = ProposalSection(
        id="rfp-structure-required-elements",
        title="Response Format",
        content=content,
        source="generated",
        mode="write",
        status="generated",
    )
    draft = _draft([container])
    before_len = _non_whitespace_len(draft)

    new_draft, logs = split_response_format_container_sections(
        draft, _cover_letter_and_references_specs()
    )

    assert new_draft is draft
    assert len(new_draft.sections) == 1
    assert new_draft.sections[0].content == content
    assert any("no mapped deliverable" in log for log in logs)
    assert _non_whitespace_len(new_draft) == before_len


def test_no_container_present_is_a_no_op():
    section = ProposalSection(
        id="cost",
        title="Cost Information",
        content="Our fees are itemized below.",
        source="generated",
        mode="write",
        status="generated",
    )
    draft = _draft([section])

    new_draft, logs = split_response_format_container_sections(
        draft, _cover_letter_and_references_specs()
    )

    assert new_draft is draft
    assert logs == []


# --- non-deliverable clause tabs -------------------------------------------

from app.services.proposal_fulfill_rfp_structure import (  # noqa: E402
    drop_non_deliverable_rfp_sections,
)

_CLAUSE_TITLE = (
    "Consideration of the responses will be governed by the following "
    "schedule, which is subject to"
)


def test_empty_rfp_clause_tab_is_dropped():
    junk = ProposalSection(
        id="rfp-eval-9",
        title=_CLAUSE_TITLE,
        content="",
        source="generated",
        mode="write",
        status="outline",
    )
    real = ProposalSection(
        id="rfp-structure-cost",
        title="Cost Information",
        content="Our fee is $100,000.",
        source="generated",
        mode="write",
        status="generated",
    )
    new_draft, logs = drop_non_deliverable_rfp_sections(_draft([junk, real]))
    assert [s.id for s in new_draft.sections] == ["rfp-structure-cost"]
    assert any("Dropped non-deliverable tab" in log for log in logs)


def test_clause_tab_with_real_prose_is_kept_and_flagged():
    # Never delete written content, however junk the title looks.
    junk = ProposalSection(
        id="rfp-eval-9",
        title=_CLAUSE_TITLE,
        content="zö agency accepts the stated evaluation schedule and will meet every date.",
        source="generated",
        mode="write",
        status="generated",
    )
    new_draft, logs = drop_non_deliverable_rfp_sections(_draft([junk]))
    assert [s.id for s in new_draft.sections] == ["rfp-eval-9"]
    assert new_draft.sections[0].content == junk.content
    assert any("Manual review" in log for log in logs)


def test_real_deliverable_tabs_are_never_touched():
    real = ProposalSection(
        id="rfp-structure-cover-letter",
        title="1. Cover Letter",
        content="",
        source="generated",
        mode="write",
        status="outline",
    )
    new_draft, logs = drop_non_deliverable_rfp_sections(_draft([real]))
    assert new_draft is _draft([real]) or [s.id for s in new_draft.sections] == [real.id]
    assert logs == []
