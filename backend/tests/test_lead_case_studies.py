import asyncio

from app.leads import case_studies
from app.leads.case_studies import titles_from_hits


def test_titles_from_hits_keeps_unique_case_studies_and_drops_dumps():
    hits = [
        {"metadata": {"fileName": "03_CS_Hampton_Lumber_Brand.pdf"}},
        {"metadata": {"fileName": "03_CS_AllCaseStudies.pdf"}},
        {"metadata": {"fileName": "01_CompanyFacts.pdf"}},
        {"metadata": {"fileName": "03_CS_Hampton_Lumber_Brand.pdf"}},
        {"title": "03_CS_Vaagen_Mill_Campaign.pdf"},
        {"title": "03_CS_Roseburg_Forest_Products.pdf"},
        {"title": "03_CS_Fourth_Study.pdf"},
    ]
    assert titles_from_hits(hits) == [
        "Hampton Lumber Brand",
        "Vaagen Mill Campaign",
        "Roseburg Forest Products",
    ]


def test_find_case_studies_query_does_not_prefix_agency_name(monkeypatch):
    """'zö agency' in the query ranks companyfacts/MasterTemplate over 03_CS_ PDFs."""
    captured: dict = {}

    async def fake_search(**kwargs):
        captured.update(kwargs)
        return [
            {"metadata": {"fileName": "03_CS_Hampton Lumber_Educational Packet_2025.pdf"}},
        ]

    monkeypatch.setattr("app.services.supermemory.is_configured", lambda: True)
    monkeypatch.setattr("app.services.supermemory.search_hybrid", fake_search)
    case_studies._CACHE.clear()
    titles = asyncio.run(case_studies.find_case_studies("Paper & Forest Products"))
    assert captured["query"] == "03_CS_ Paper & Forest Products case study"
    assert titles == ["Hampton Lumber Educational Packet 2025"]
