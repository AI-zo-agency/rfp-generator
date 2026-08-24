"""Corrections reach the prompts that matter."""

from app.services import go_no_go_service


def test_go_no_go_prompt_places_corrections_above_kb_excerpts() -> None:
    prompt = go_no_go_service._compose_stage_one_prompt(
        corrections_block="## STANDING CORRECTIONS (authoritative)\n- (2026-08-24) Ron Comer has retired",
        body="## Knowledge base excerpts (verified facts only — do not go beyond this)\nold bio text",
    )
    assert "Ron Comer has retired" in prompt
    assert prompt.index("STANDING CORRECTIONS") < prompt.index("Knowledge base excerpts")


def test_go_no_go_prompt_omits_block_when_no_corrections() -> None:
    prompt = go_no_go_service._compose_stage_one_prompt(
        corrections_block="",
        body="## Knowledge base excerpts\nfacts",
    )
    assert "STANDING CORRECTIONS" not in prompt
    assert prompt.startswith("## Knowledge base excerpts")
