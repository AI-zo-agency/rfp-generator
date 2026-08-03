"""Stage map: judgment nodes must not silently fall through to the economy model."""

from app.services.llm_routing import classify_node, is_quality_critical_node




def test_unknown_node_defaults_to_quality():
    # The previous behaviour defaulted unknown nodes to the cheapest provider,
    # which is how every repair agent ended up on the economy model.
    assert classify_node("some_brand_new_node") == "quality"
    assert is_quality_critical_node("some_brand_new_node") is True


def test_unnamed_node_defaults_to_quality():
    assert classify_node("") == "quality"
    assert classify_node(None) == "quality"


def test_judgment_stages_are_quality():
    for node in (
        "senior_editor",
        "section_repair",
        "user_revise",
        "surgical_fix",
        "verify_optional_scrub",
        "kb_fact_check",
        "bio_extract",
        "manuscript_auditor",
    ):
        assert classify_node(node) == "quality", node


def test_mechanical_stages_stay_cheap():
    for node in (
        "query_planner",
        "section_dedup",
        "team_select",
        "case_select",
        "chat_structure_plan",
    ):
        assert classify_node(node) == "mechanical", node


def test_drafting_prefixes_are_quality():
    assert classify_node("draft_sections:section-4") == "quality"
    assert classify_node("build_section_1_cq") == "quality"
    assert classify_node("chat_full_redraft") == "quality"
