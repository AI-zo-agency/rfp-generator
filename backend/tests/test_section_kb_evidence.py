"""General packed KB evidence for section improve — no vertical hardcodes."""

from __future__ import annotations

from app.services.proposal_section_kb_evidence import (
    ACCURATE_KB_EDITOR_RULES,
    build_section_kb_question,
    draft_entity_hints,
    inject_packed_evidence_into_instruction,
    section_wants_packed_kb_evidence,
)


def test_evidence_heavy_from_section_title():
    assert section_wants_packed_kb_evidence(
        section_title="Examples of Tourism or Destination Marketing Social Media Accounts Managed",
        section_content="",
        user_message="",
    )


def test_budget_section_not_forced_through_packed_path():
    assert not section_wants_packed_kb_evidence(
        section_title="Budget Summary",
        section_content="Fee table",
        user_message="recalculate totals",
    )


def test_draft_entity_hints_from_headings_not_static_list():
    content = (
        "## Acme Destination Board\n"
        "We ran social.\n\n"
        "## Strategy\n"
        "Ignore this heading.\n\n"
        "**North Bend Outfitters**\n"
        "More work.\n"
    )
    hints = draft_entity_hints(content)
    assert "Acme Destination Board" in hints
    assert "North Bend Outfitters" in hints
    assert "Strategy" not in hints


def test_question_includes_draft_entities_and_rfp_needs():
    q = build_section_kb_question(
        section_title="Client Examples",
        user_message="fill KPIs from KB",
        requirements=["before/after metrics", "accounts managed"],
        section_content="## Acme Destination Board\nBody",
    )
    assert "Acme Destination Board" in q
    assert "before/after metrics" in q
    assert "fill KPIs" in q
    # Must not hardcode a tourism win list
    assert "San Francisco Travel" not in q
    assert "Seventh Mountain" not in q


def test_user_asks_kb_fetch_or_fill_patterns():
    from app.services.proposal_section_kb_evidence import user_asks_kb_fetch_or_fill

    assert user_asks_kb_fetch_or_fill(
        "here San Francisco Travel case study is empty fetch that"
    )
    assert user_asks_kb_fetch_or_fill("fill San Francisco Travel from KB")
    assert user_asks_kb_fetch_or_fill("fetch case study from knowledge base")
    assert user_asks_kb_fetch_or_fill("get the firm address from knowledge base")
    assert user_asks_kb_fetch_or_fill("search KB for contact info")
    assert not user_asks_kb_fetch_or_fill("Does 3.3 meet the RFP?")


def test_fetch_ask_triggers_packed_kb_evidence():
    assert section_wants_packed_kb_evidence(
        section_title="Budget Summary",
        section_content="Fee table",
        user_message="here SF Travel case study is empty fetch that",
    )
    out = inject_packed_evidence_into_instruction(
        "Improve this section.",
        "=== PACKED KB EVIDENCE ===\nKPI: bookings up",
    )
    assert "Improve this section." in out
    assert "PACKED KB EVIDENCE" in out
    assert "ACCURATE KB EVIDENCE RULES" in ACCURATE_KB_EDITOR_RULES
    assert "do not invent" in out.casefold() or "ACCURATE KB" in out
