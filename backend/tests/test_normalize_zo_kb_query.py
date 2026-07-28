"""KB queries must target zö materials — never the RFP buyer as subject."""

from app.services.proposal_knowledge_base_tools import normalize_zo_kb_query


def test_strips_buyer_as_subject_kvcc():
    out = normalize_zo_kb_query(
        "KVCC Kennebec Valley Community College Maine community college marketing",
        rfp_client="KVCC / Kennebec Valley Community College",
        rfp_sector="higher education marketing",
    )
    lower = out.casefold()
    assert "zö" in lower or "zo agency" in lower
    assert "kvcc" not in lower
    assert "kennebec" not in lower
    assert "marketing" in lower or "higher" in lower


def test_keeps_pricing_guide_untouched():
    q = "00_Guide_Pricing tier ranges Low Average High discovery"
    out = normalize_zo_kb_query(q, rfp_client="KVCC")
    lower = out.casefold()
    assert "00_guide_pricing" in lower
    assert "low" in lower and "average" in lower and "high" in lower
    assert "kvcc" not in lower


def test_strips_buyer_from_pricing_guide_query():
    from app.services.proposal_knowledge_base_tools import sanitize_pricing_guide_query

    out = sanitize_pricing_guide_query(
        "00_Guide_Pricing Kennebec Valley Community College Marketing Plan for Kennebec V",
        rfp_client="KVCC / Kennebec Valley Community College",
        rfp_title="Kennebec Valley Community College Marketing Plan",
    )
    lower = out.casefold()
    assert "00_guide_pricing" in lower
    assert "kennebec" not in lower
    assert "kvcc" not in lower


def test_pricing_sanitize_keeps_guide_line_phrases():
    from app.services.proposal_knowledge_base_tools import sanitize_pricing_guide_query

    out = sanitize_pricing_guide_query(
        "00_Guide_Pricing 9.1 9.2 Project Management short projects "
        "campaign-specific 5-8 percent floor",
        rfp_client="KVCC",
        rfp_title="KVCC Marketing Plan",
    )
    lower = out.casefold()
    assert "9.1" in lower and "9.2" in lower
    assert "9.19.2" not in lower
    assert "short" in lower
    assert "percent" in lower
    assert "kvcc" not in lower


def test_prepends_zo_when_missing():
    out = normalize_zo_kb_query(
        "03_CS higher education enrollment campaign outcomes",
        rfp_client="Some College",
        rfp_sector="higher education",
    )
    assert out.casefold().startswith("zö") or "zö agency" in out.casefold()


def test_sector_theme_not_buyer_name():
    out = normalize_zo_kb_query(
        "technical ability specifications methodology four-phase",
        rfp_client="KVCC",
        rfp_sector="community college marketing",
    )
    assert "zö" in out.casefold()
    assert "kvcc" not in out.casefold()
