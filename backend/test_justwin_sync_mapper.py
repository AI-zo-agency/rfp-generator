"""Unit tests for JustWin Python sync helpers (no browser)."""

from app.services.justwin_sync.api import (
    JustWinLead,
    lead_matches_posted_date,
    posted_date_of,
    resolve_tabs,
)
from app.services.justwin_sync.mapper import map_lead_to_rfp


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
