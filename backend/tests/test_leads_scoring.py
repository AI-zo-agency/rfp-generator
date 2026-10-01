from datetime import date

from app.leads.ai import preparation_facts
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


def test_preparation_facts_include_monid_company_and_person_data():
    facts = preparation_facts(
        {"contact_id": "1", "company": "Mt Baker", "industry": "Wholesale"},
        {
            "employee_band": "51-200",
            "what_they_do": "Forest products wholesaler.",
            "person": {"job_title": "Purchasing Manager", "job_title_levels": "manager"},
        },
    )

    assert facts["monid_company"] == {
        "employee_band": "51-200",
        "what_they_do": "Forest products wholesaler.",
    }
    assert facts["monid_contact"] == {
        "job_title": "Purchasing Manager",
        "job_title_levels": "manager",
    }


def test_disqualify_catches_the_junk_found_in_the_live_hubspot_list():
    """Patterns from the real 1,296-contact HubSpot export (Sep 2026)."""
    junk = {
        "accounts.payable@everfastfiber.com": "role inbox",
        "#helpdesk@hamptonlumber.com": "role inbox",
        "hsdprocurement@maricopa.gov": "role inbox",
        "billing_requests@miro.com": "role inbox",
        "sonja@zo.agency": "zö/E2M team",
        "vivek@e2msolutions.com": "zö/E2M team",
        "epson1@mailph.custhelp.com": "vendor",          # subdomain of a vendor
        "client-mtviewheating-aaaa@ci-web-group.org.slack.com": "forwarding",
        "case+26-934668260@progressive.assuredclaims.net": "machine",
        "phantizy@comcast.net": "personal",
    }
    for email, expected in junk.items():
        reason = disqualify({"email": email})
        assert reason and expected in reason, (email, reason)


def test_disqualify_keeps_real_people_whose_address_contains_a_role_word():
    for email in (
        "aaurand@bendoregon.gov",
        "apond@deschutesbrewery.com",     # starts with "ap", but is a person
        "carrie.shilhanek@umatilla.gov",
        "teamcathy@promotionsnow.example",  # "team" glued to a name is not a role word
    ):
        assert disqualify({"email": email}) is None, email
