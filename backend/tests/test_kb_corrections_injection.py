"""Corrections reach the prompts that matter."""

import asyncio

from app.services import go_no_go_service
from app.services import proposal_langchain
from app.services import proposal_sections_graph


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


def test_narrative_preamble_includes_corrections() -> None:
    state = {
        "rfp_client": "City of San Leandro",
        "rfp_sector": "public sector",
        "kb_corrections": "## STANDING CORRECTIONS (authoritative)\n- (2026-08-24) Ron Comer has retired",
    }
    preamble = proposal_sections_graph._narrative_section_preamble(state)
    assert "Ron Comer has retired" in preamble


def test_narrative_preamble_clean_without_corrections() -> None:
    state = {
        "rfp_client": "City of San Leandro",
        "rfp_sector": "public sector",
        "kb_corrections": "",
    }
    preamble = proposal_sections_graph._narrative_section_preamble(state)
    assert "STANDING CORRECTIONS" not in preamble


def test_lead_with_corrections_puts_block_first() -> None:
    out = proposal_langchain.lead_with_corrections(
        "Ron Comer, Creative Director",
        "## STANDING CORRECTIONS (authoritative)\n- (2026-08-24) Ron Comer has retired",
    )
    assert out.index("Ron Comer has retired") < out.index("Creative Director")


def test_lead_with_corrections_returns_text_unchanged_when_block_blank() -> None:
    assert proposal_langchain.lead_with_corrections("Ron Comer, Creative Director", "   ") == (
        "Ron Comer, Creative Director"
    )


def test_search_tool_leads_with_corrections(monkeypatch) -> None:
    async def fake_search(query, **kwargs):
        return "Ron Comer, Creative Director", []

    async def fake_block():
        return "## STANDING CORRECTIONS (authoritative)\n- (2026-08-24) Ron Comer has retired"

    monkeypatch.setattr(
        proposal_langchain.proposal_knowledge_base_tools,
        "search_knowledge_base",
        fake_search,
    )
    monkeypatch.setattr(proposal_langchain, "corrections_prompt_block", fake_block)

    tools = proposal_langchain.build_proposal_tools(
        rfp_id="rfp-1", title="Brand refresh", client="City of San Leandro", sector="public"
    )
    kb_tool = next(tool for tool in tools if tool.name == "search_knowledge_base")

    out = asyncio.run(kb_tool.coroutine("zo team bios"))
    assert out.index("Ron Comer has retired") < out.index("Creative Director")
