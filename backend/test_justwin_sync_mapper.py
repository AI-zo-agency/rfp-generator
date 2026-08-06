"""Unit tests for JustWin Python sync helpers (no browser)."""

from app.services.justwin_sync.api import JustWinLead, posted_date_of, resolve_tabs
from app.services.justwin_sync.mapper import map_lead_to_rfp


def test_posted_date_of_utc():
    assert posted_date_of({"created": "2026-08-06T14:27:47.169398+00:00"}) == "2026-08-06"


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
