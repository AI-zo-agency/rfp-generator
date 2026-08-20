from datetime import date

from app.leads.scoring import build_brief, build_leads, disqualify, load_dataset

TODAY = date(2026, 8, 20)


def _by_email(leads):
    return {lead.contact["email"]: lead for lead in leads}


def test_disqualify_gate_catches_the_junk_in_the_crm():
    cases = {
        "receivables@simpli.fi": "role inbox",
        "tw.29.80850697.68@replies.hubspot.com": "tracker",
        "42b29f39dd073be38079fa1@example.invalid": "machine",
        "ddorf62@me.com": "personal",
        "shrey.chaudhari@e2m.solutions": "vendor",
    }
    for email in cases:
        assert disqualify({"email": email}) is not None, email
    assert disqualify({"email": "mark@giustinaland.com"}) is None


def test_domain_join_attaches_company_firmographics():
    """The join that replaces HubSpot's empty Primary company column."""
    lead = _by_email(build_leads(load_dataset(), TODAY))["philb@vaagenbros.com"]
    assert lead.company is not None
    assert lead.company["industry"] == "Paper & Forest Products"
    assert lead.company["state"] == "WA"


def test_core_sector_in_core_territory_outranks_out_of_sector():
    leads = _by_email(build_leads(load_dataset(), TODAY))
    lumber = leads["philb@vaagenbros.com"]      # Paper & Forest, WA
    msp = leads["avillalobos@levelupmsp.com"]   # IT Services, no location
    assert lumber.score > msp.score
    assert lumber.band == "Hot"


def test_recency_decays_the_score():
    contact = {"id": "x", "email": "a@vaagenbros.com", "last_activity": "2026-08-20"}
    stale = {**contact, "last_activity": "2026-01-01"}
    data = {**load_dataset(), "contacts": [contact, stale]}
    fresh_lead, stale_lead = build_leads(data, TODAY)[:2]
    assert fresh_lead.breakdown["engagement_recency"] == 15
    assert stale_lead.breakdown["engagement_recency"] == 0


def test_brief_never_drafts_messaging():
    data = load_dataset()
    lead = _by_email(build_leads(data, TODAY))["djones@cityofsacramento.org"]
    brief = build_brief(lead, data["case_studies"])
    assert brief["company"] == "City of Sacramento"
    assert brief["case_studies"]  # phase 6 match by industry
    assert "drafts no messaging" in brief["next_step"]
    assert brief["visitor_intel"] is None  # RB2B deferred
