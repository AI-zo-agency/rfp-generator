"""Unit tests for JustWin Python sync helpers (no browser)."""

from app.services.justwin_sync.api import (
    JustWinLead,
    _to_lead,
    apply_justwin_due_date,
    due_date_from_justwin_payload,
    lead_matches_posted_date,
    posted_date_of,
    resolve_tabs,
)
from app.services.justwin_sync.mapper import map_lead_to_rfp, parse_justwin_date


def test_posted_date_of_utc():
    assert posted_date_of({"created": "2026-08-06T14:27:47.169398+00:00"}) == "2026-08-06"


def test_lead_matches_posted_date_across_timezones():
    """Evening UTC Aug 6 is already Aug 7 in IST — must match syncing 'today' Aug 7."""
    lead = {"created": "2026-08-06T20:00:00+00:00"}
    assert posted_date_of(lead) == "2026-08-06"
    assert lead_matches_posted_date(lead, "2026-08-07") is True
    assert lead_matches_posted_date(lead, "2026-08-06") is True
    assert lead_matches_posted_date(lead, "2026-08-05") is False


def test_lead_matches_us_evening_posted_column():
    """Late Aug 7 Pacific can be Aug 8 UTC — still matches Posted Aug 7."""
    lead = {"created": "2026-08-08T04:00:00+00:00"}  # Aug 7 21:00 PT
    assert lead_matches_posted_date(lead, "2026-08-07") is True


def test_resolve_tabs():
    assert resolve_tabs("all") == ["hot", "warm", "review"]
    assert resolve_tabs("hot") == ["hot"]


def test_map_lead_to_rfp():
    lead = JustWinLead(
        external_id="xyz",
        title="Website Redesign for San Benito [CA]",
        location="CA",
        posted_date="2026-08-01",
        due_date="2026-09-15",
        score=5,
        description="summary",
        detail_url="https://app.justwin.ai/leads/xyz/summary",
        tab="warm",
    )
    record = map_lead_to_rfp(lead)
    assert record.id == "rfp-jw-xyz"
    assert record.client == "San Benito"
    assert record.priority == "high"
    assert record.justwin_tab == "warm"
    assert record.due_date == "2026-09-15"


def test_map_lead_empty_due_date_stays_empty():
    lead = JustWinLead(
        external_id="xyz",
        title="Website Redesign for San Benito [CA]",
        location="CA",
        posted_date="2026-08-01",
        due_date="",
        score=5,
        description="summary",
        detail_url="https://app.justwin.ai/leads/xyz/summary",
        tab="warm",
    )
    record = map_lead_to_rfp(lead)
    assert record.due_date == ""


def test_map_lead_parses_justwin_display_due_date():
    lead = JustWinLead(
        external_id="octa-print",
        title="Marketing Print Services for Orange County Transportation Authority",
        location="CA",
        posted_date="2026-09-11",
        due_date="October 1, 2026",
        score=4,
        description="summary",
        detail_url="https://app.justwin.ai/leads/octa-print/summary",
        tab="hot",
    )
    record = map_lead_to_rfp(lead)
    assert record.due_date == "2026-10-01"


def test_due_date_from_insights_when_top_level_missing():
    raw = {
        "id": "abc",
        "due_date": None,
        "readonly_values": {
            "name": "Marketing Print Services for OCTA",
            "insights": {"due_date": "2026-10-01", "qa_due_date": "2026-09-16"},
        },
    }
    assert due_date_from_justwin_payload(raw) == "2026-10-01"


def test_due_date_prefers_top_level_over_insights():
    raw = {
        "id": "abc",
        "due_date": "2026-10-01",
        "readonly_values": {"insights": {"due_date": "2026-09-16"}},
    }
    assert due_date_from_justwin_payload(raw) == "2026-10-01"


def test_due_date_ignores_qa_deadline():
    raw = {
        "id": "abc",
        "readonly_values": {
            "insights": {"qa_due_date": "2026-09-16", "questions_due": "2026-09-16"}
        },
    }
    assert due_date_from_justwin_payload(raw) == ""


def test_to_lead_uses_insights_due_date():
    raw = {
        "id": "octa-print",
        "created": "2026-09-11T10:00:00+00:00",
        "readonly_values": {
            "name": "Marketing Print Services for Orange County Transportation Authority",
            "relevance_score_integer": 4,
            "insights": {
                "title": "Marketing Print Services",
                "summary": "Print services",
                "due_date": "2026-10-01",
            },
        },
        "state": {"abbreviation": "CA"},
    }
    lead = _to_lead(raw, "hot")
    assert lead.due_date == "2026-10-01"


def test_apply_justwin_due_date_overwrites_empty_list_value():
    lead = JustWinLead(
        external_id="octa-print",
        title="Marketing Print Services",
        location="CA",
        posted_date="2026-09-11",
        due_date="",
        score=4,
        description="Print services",
        detail_url="https://app.justwin.ai/leads/octa-print/summary",
        tab="hot",
    )
    apply_justwin_due_date(
        lead,
        {"readonly_values": {"insights": {"due_date": "October 1, 2026"}}},
    )
    assert lead.due_date == "October 1, 2026"


def test_parse_justwin_full_month_name():
    assert parse_justwin_date("October 1, 2026") == "2026-10-01"
    assert parse_justwin_date("September 16, 2026") == "2026-09-16"
