from datetime import date

from app.financial.hs_forecast_match import match_won_deals


def test_won_matched_when_invoices_cover_80_percent():
    deals = [{
        "hs_id": 1,
        "amount": 10000,
        "closedate": date(2026, 8, 1),
        "hs_is_closed_won": True,
        "company_hs_id": 7,
        "dealname": "Acme",
    }]
    companies = {7: {"name": "Acme Corp", "domain": "acme.com"}}
    invoices = [{
        "customer_name": "Acme Corp",
        "txn_date": "2026-08-10",
        "total_amt": 9000,
        "is_deleted": False,
    }]
    result = match_won_deals(deals, companies=companies, invoices=invoices, as_of=date(2026, 10, 5))
    assert 1 in result.matched_ids
    assert result.unmatched == []


def test_won_unmatched_kept_when_no_customer():
    deals = [{
        "hs_id": 2,
        "amount": 5000,
        "closedate": date(2026, 9, 1),
        "hs_is_closed_won": True,
        "company_hs_id": None,
        "dealname": "Mystery",
    }]
    result = match_won_deals(deals, companies={}, invoices=[], as_of=date(2026, 10, 5))
    assert 2 in {d["hs_id"] for d in result.unmatched}
    assert result.matched_ids == set()
