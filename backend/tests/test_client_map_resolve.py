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
