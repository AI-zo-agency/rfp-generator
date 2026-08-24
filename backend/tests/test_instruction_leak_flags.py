"""Instruction text the generator narrates must become a visible internal flag."""

from app.services.proposal_manuscript import (
    convert_instruction_blocks,
    scrub_text_for_client_export,
)

CARSON_LEAK = """## A. Professional References

Three references are provided below from municipal and public-sector clients.

> ⚠ ACTION REQUIRED BEFORE SUBMISSION, PASS/FAIL ITEM
> This section is a pass/fail responsiveness requirement. The proposal cannot be
> submitted until all three reference records are fully populated from verified
> ClientList / KB contacts. Fields marked Action needed — VERIFY must be
> confirmed with Sonja Anderson before submission. Do not invent contacts,
> organizations, or project details.

### Reference 1
"""


def test_blockquote_instruction_becomes_a_manual_fill_tag() -> None:
    out = convert_instruction_blocks(CARSON_LEAK)
    assert "[MANUAL FILL: Sonja —" in out
    assert "ACTION REQUIRED BEFORE SUBMISSION" not in out.replace(
        "[MANUAL FILL: Sonja —", ""
    ) or "[MANUAL FILL" in out


def test_actionable_body_text_is_preserved_inside_the_tag() -> None:
    out = convert_instruction_blocks(CARSON_LEAK)
    assert "three reference records" in out
    tag_start = out.index("[MANUAL FILL: Sonja —")
    tag_end = out.index("]", tag_start)
    assert "reference records" in out[tag_start:tag_end]


def test_surrounding_content_is_untouched() -> None:
    out = convert_instruction_blocks(CARSON_LEAK)
    assert "## A. Professional References" in out
    assert "Three references are provided below" in out
    assert "### Reference 1" in out


def test_converted_block_is_stripped_from_client_export() -> None:
    out = scrub_text_for_client_export(convert_instruction_blocks(CARSON_LEAK))
    assert "ACTION REQUIRED" not in out
    assert "Do not invent" not in out
    assert "cannot be" not in out
    assert "Three references are provided below" in out


def test_presentation_instruction_routes_to_designer_note() -> None:
    text = "> ACTION REQUIRED: the logo layout on this page must be confirmed before submission.\n"
    out = convert_instruction_blocks(text)
    assert "[DESIGNER NOTE:" in out
    assert "delete this note if the RFP does not require it" in out


def test_pure_meta_commentary_is_dropped_not_flagged() -> None:
    text = (
        "Intro paragraph.\n\n"
        "> ACTION REQUIRED: Do not invent contacts, organizations, or project details.\n"
        "> This section is a pass/fail responsiveness requirement.\n\n"
        "Closing paragraph.\n"
    )
    out = convert_instruction_blocks(text)
    assert "MANUAL FILL" not in out
    assert "DESIGNER NOTE" not in out
    assert "Do not invent" not in out
    assert "Intro paragraph." in out
    assert "Closing paragraph." in out


def test_carson_block_keeps_only_the_actionable_sentence() -> None:
    out = convert_instruction_blocks(CARSON_LEAK)
    assert "reference records" in out
    assert "Do not invent" not in out


def test_ordinary_prose_is_left_alone() -> None:
    text = (
        "Our team required action from three departments to deliver the campaign "
        "before the submission deadline.\n"
    )
    assert convert_instruction_blocks(text) == text


def test_already_tagged_block_is_not_double_wrapped() -> None:
    text = "> ACTION REQUIRED: [MANUAL FILL: Sonja — confirm the reference list]\n"
    assert convert_instruction_blocks(text) == text


def test_find_instruction_leaks_reports_untagged_instruction_prose() -> None:
    from app.services.proposal_manuscript import find_instruction_leaks

    leaks = find_instruction_leaks(
        "The proposal cannot be submitted until the references are populated.\n"
    )
    assert leaks and "cannot be submitted" in leaks[0]


def test_find_instruction_leaks_ignores_tagged_content() -> None:
    from app.services.proposal_manuscript import find_instruction_leaks

    assert (
        find_instruction_leaks(
            "[MANUAL FILL: Sonja — the proposal cannot be submitted until refs are populated]\n"
        )
        == []
    )


def test_find_instruction_leaks_clean_text() -> None:
    from app.services.proposal_manuscript import find_instruction_leaks

    assert find_instruction_leaks("We delivered the campaign on schedule.\n") == []
