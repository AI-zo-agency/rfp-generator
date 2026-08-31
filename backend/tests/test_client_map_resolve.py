from app.financial.client_map import parse_job_key, resolve_project


def test_parse_job_key():
    assert parse_job_key("MVH 26139 Comfort Suite Print") == {"tag": "MVH", "job_number": "26139"}
    assert parse_job_key("Contra Costa RFP Deck") is None


def test_resolve_prefers_override(monkeypatch):
    monkeypatch.setattr(
        "app.financial.client_map_repository.get_job_override",
        lambda site_id, project_id: {
            "qb_customer_ids": ["99"],
            "link_confidence": "confirmed",
            "client_map_id": "cm1",
        },
    )
    m = resolve_project("zo", 1, "MVH 26139 x", None, None, client_rows=[], overrides_loaded=False)
    assert m.via == "override"
    assert m.qb_customer_ids == ["99"]


def test_ambiguous_tag_returns_ambiguous():
    rows = [
        {"id": "1", "tag_code": "ATC", "client_name": "Arctic", "qb_customer_ids": [],
         "link_confidence": "unmatched", "is_internal": False},
        {"id": "2", "tag_code": "ATC", "client_name": "Anti-Trafficking", "qb_customer_ids": [],
         "link_confidence": "unmatched", "is_internal": False},
    ]
    m = resolve_project("zo", 2, "ATC 25001 x", None, None, client_rows=rows, overrides_loaded=True)
    assert m.kind == "ambiguous"


def test_resolve_single_tag_match():
    rows = [
        {"id": "cm-mvh", "tag_code": "MVH", "client_name": "Mountain View Heating",
         "qb_customer_ids": ["10"], "link_confidence": "confirmed", "is_internal": False},
    ]
    m = resolve_project(
        "zo", 3, "MVH 26139 Comfort Suite Print", None, None,
        client_rows=rows, overrides_loaded=True, override=None,
    )
    assert m.via == "tag"
    assert m.client_map_id == "cm-mvh"
    assert m.qb_customer_ids == ["10"]


def test_resolve_company_id_match():
    rows = [
        {"id": "cm-co", "tag_code": "TOR", "client_name": "Torrent Laboratories",
         "qb_customer_ids": ["55"], "teamwork_company_ids": [42],
         "teamwork_company_names": [], "link_confidence": "confirmed", "is_internal": False},
    ]
    m = resolve_project(
        "zo", 4, "Contra Costa RFP Deck", 42, None,
        client_rows=rows, overrides_loaded=True, override=None,
    )
    assert m.via == "company"
    assert m.client_map_id == "cm-co"


def test_resolve_company_name_normalized_match():
    rows = [
        {"id": "cm-tor", "tag_code": "TOR", "client_name": "Torrent Laboratories",
         "qb_customer_ids": ["55"], "teamwork_company_ids": [],
         "teamwork_company_names": ["Torrent Laboratories"],
         "link_confidence": "confirmed", "is_internal": False},
    ]
    m = resolve_project(
        "zo", 5, "Some Project", None, "Torrent Laboratories LLC",
        client_rows=rows, overrides_loaded=True, override=None,
    )
    assert m.via == "company"
    assert m.client_map_id == "cm-tor"


def test_resolve_no_match_returns_none():
    m = resolve_project(
        "zo", 6, "Contra Costa RFP Deck", None, "Unknown Client LLC",
        client_rows=[], overrides_loaded=True, override=None,
    )
    assert m is None
