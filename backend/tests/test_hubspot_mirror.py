from datetime import date, datetime, timezone

import httpx

from app.leads import hubspot
from app.leads.scoring import build_leads

TODAY = date(2026, 9, 28)


def _mock_http(handler) -> httpx.Client:
    return httpx.Client(base_url=hubspot.API_BASE, transport=httpx.MockTransport(handler))


def test_contact_row_maps_the_fields_scoring_needs():
    obj = {
        "id": "467050401525",
        "updatedAt": "2026-07-22T13:39:06.741Z",
        "archived": False,
        "properties": {
            "email": " Jennifer.Sparacino@CityOfMedford.org ",
            "firstname": "Jennifer",
            "lastname": "Sparacino",
            "hubspot_owner_id": "77",
            "associatedcompanyid": "9001",
            "notes_last_updated": "2026-09-20T10:00:00Z",
            "phone": "",
        },
    }
    row = hubspot.contact_row(obj, {"77": "Dana Owner"}, "2026-09-28T00:00:00+00:00")
    assert row["hs_id"] == 467050401525
    assert row["email"] == "jennifer.sparacino@cityofmedford.org"
    assert row["owner_name"] == "Dana Owner"
    assert row["company_hs_id"] == 9001
    assert row["phone"] is None


def test_company_row_uses_industry_label_not_enum():
    obj = {"id": "9001", "properties": {"domain": "OchocoLumber.com", "industry": "PAPER_FOREST_PRODUCTS"}}
    row = hubspot.company_row(obj, {"PAPER_FOREST_PRODUCTS": "Paper & Forest Products"}, "x")
    assert row["industry"] == "Paper & Forest Products"
    assert row["domain"] == "ochocolumber.com"


def test_mirror_dataset_joins_on_association_before_email_domain():
    """City staff often email from a different domain than the company record."""
    contacts = [{
        "hs_id": 1, "email": "jennifer@cityofmedford.org", "firstname": "Jennifer",
        "lastname": "Sparacino", "phone": "541-555-0100", "company_hs_id": 9001,
        "owner_name": "Dana", "last_activity_at": "2026-09-27T09:00:00+00:00",
    }]
    companies = [
        {"hs_id": 9001, "domain": "medfordoregon.gov", "name": "City of Medford",
         "industry": "Paper & Forest Products", "city": "Medford", "state": "Oregon"},
        {"hs_id": 9002, "domain": None, "name": "No-domain Co", "industry": None, "city": None, "state": None},
    ]
    data = hubspot.dataset_from_rows(contacts, companies)
    (lead,) = build_leads(data, TODAY)
    assert lead.company["name"] == "City of Medford"
    assert lead.company["state"] == "OR"          # "Oregon" normalized for geography scoring
    assert lead.breakdown["geography"] == 25
    assert lead.contact["last_activity"] == "2026-09-27"
    assert lead.band == "Hot"


def test_contact_without_email_does_not_match_a_domainless_company():
    data = hubspot.dataset_from_rows(
        [{"hs_id": 2, "email": None, "firstname": "A", "lastname": "B"}],
        [{"hs_id": 7, "domain": None, "name": "Ghost"}],
    )
    (lead,) = build_leads(data, TODAY)
    assert lead.company is None
    assert lead.disqualified_reason == "no usable email"


def test_search_past_the_cap_falls_back_to_full_sync():
    http = _mock_http(lambda request: httpx.Response(200, json={"total": 10_000, "results": []}))
    since = datetime(2026, 9, 1, tzinfo=timezone.utc)
    assert hubspot.search_since(http, "contacts", since) is None


def test_search_pages_until_no_next_cursor():
    pages = iter([
        {"total": 3, "results": [{"id": "1"}, {"id": "2"}], "paging": {"next": {"after": "2"}}},
        {"total": 3, "results": [{"id": "3"}]},
    ])
    http = _mock_http(lambda request: httpx.Response(200, json=next(pages)))
    rows = hubspot.search_since(http, "contacts", datetime(2026, 9, 1, tzinfo=timezone.utc))
    assert [r["id"] for r in rows] == ["1", "2", "3"]


def test_rate_limit_is_retried(monkeypatch):
    monkeypatch.setattr(hubspot.time, "sleep", lambda _s: None)
    responses = iter([httpx.Response(429, headers={"Retry-After": "1"}), httpx.Response(200, json={"ok": True})])
    http = _mock_http(lambda request: next(responses))
    assert hubspot._call(http, "GET", "/crm/v3/owners") == {"ok": True}


def _contact(hs_id, email, **extra):
    return {"hs_id": hs_id, "email": email, "firstname": "A", "lastname": "B", **extra}


def _reasons(contacts, companies):
    leads = build_leads(hubspot.dataset_from_rows(contacts, companies), TODAY)
    return {lead.contact["email"]: lead.disqualified_reason for lead in leads}


def test_company_marked_vendor_in_hubspot_excludes_its_whole_domain():
    """Only one Yebo contact is linked to the company; the domain carries the flag to the rest."""
    companies = [{"hs_id": 50, "name": "Yebo Group", "domain": None, "type": "VENDOR"}]
    reasons = _reasons(
        [_contact(1, "alfredo@yebogroup.com", company_hs_id=50), _contact(2, "brian@yebogroup.com")],
        companies,
    )
    assert reasons == {
        "alfredo@yebogroup.com": "HubSpot: company marked Vendor",
        "brian@yebogroup.com": "HubSpot: company marked Vendor",
    }


def test_relationship_type_and_contact_role_are_honoured():
    companies = [
        {"hs_id": 60, "name": "KTVZ", "domain": "ktvz.com", "relationship_type": "Media"},
        {"hs_id": 61, "name": "Bend", "domain": "bendoregon.gov", "relationship_type": "Active Prospect"},
    ]
    reasons = _reasons(
        [
            _contact(1, "jake@ktvz.com"),
            _contact(2, "rep@partnerco.com", role="Vendor/partner"),
            _contact(3, "other@partnerco.com"),
            _contact(4, "aaurand@bendoregon.gov", company_hs_id=61),
        ],
        companies,
    )
    assert reasons["jake@ktvz.com"] == "HubSpot: company marked Media"
    assert reasons["other@partnerco.com"] == "HubSpot: contact role Vendor/partner"
    assert reasons["aaurand@bendoregon.gov"] is None


def test_do_not_contact_person_does_not_flag_colleagues():
    reasons = _reasons(
        [_contact(1, "gone@insightglobal.com", contact_status="Do Not Contact"), _contact(2, "stay@insightglobal.com")],
        [],
    )
    assert reasons["gone@insightglobal.com"] == "HubSpot: contact marked Do Not Contact"
    assert reasons["stay@insightglobal.com"] is None


def test_vendor_rep_on_gmail_does_not_flag_every_gmail_contact():
    reasons = _reasons(
        [_contact(1, "rep@gmail.com", role="Vendor/partner"), _contact(2, "owner@gmail.com")],
        [],
    )
    assert reasons["rep@gmail.com"] == "HubSpot: contact role Vendor/partner"
    assert reasons["owner@gmail.com"] == "personal email domain, no company context"


def test_flagged_company_without_domain_matches_by_name():
    companies = [
        {"hs_id": 70, "name": "Kopp Consulting", "domain": None, "type": "VENDOR"},
        {"hs_id": 71, "name": "VFS", "domain": None, "type": "VENDOR"},
    ]
    reasons = _reasons(
        [
            _contact(1, "tbraidman@koppconsultingusa.com"),   # long name may prefix the domain
            _contact(2, "a@vfs.com"),                          # short name must match exactly
            _contact(3, "b@vfsglobalpartners.com"),
        ],
        companies,
    )
    assert reasons["tbraidman@koppconsultingusa.com"] == "HubSpot: company marked Vendor"
    assert reasons["a@vfs.com"] == "HubSpot: company marked Vendor"
    assert reasons["b@vfsglobalpartners.com"] is None
