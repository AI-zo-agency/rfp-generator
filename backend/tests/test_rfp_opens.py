"""Opens: one throttled activity row per person per RFP, hidden from the flat feed by default."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.main import app  # imported here so conftest's sign-in bypass applies
from app.services.rfp_opens import OPEN_ACTION, record_rfp_open
from app.services.user_activity import emit_activity, list_activity


@pytest.fixture()
def store(sqlite_activity_store, monkeypatch):
    monkeypatch.setattr("app.services.rfp_opens.rfp_exists", lambda _id: True)
    monkeypatch.setattr("app.services.rfp_opens.get_rfp", lambda _id: None)
    return sqlite_activity_store


def _opens() -> list[dict]:
    return list_activity(workspace="rfp", action=OPEN_ACTION)["items"]


def test_first_open_records(store):
    assert record_rfp_open("rfp-1", "A@zo.agency") is True
    items = _opens()
    assert len(items) == 1
    assert items[0]["actor_email"] == "a@zo.agency"
    assert items[0]["entity_id"] == "rfp-1"
    assert items[0]["outcome"] == "recorded"


def test_second_open_within_30_minutes_is_dropped(store):
    t0 = datetime.now(timezone.utc)
    assert record_rfp_open("rfp-1", "a@zo.agency", now=t0) is True
    assert record_rfp_open("rfp-1", "a@zo.agency", now=t0 + timedelta(minutes=29)) is False
    assert len(_opens()) == 1


def test_open_after_window_records_again(store):
    t0 = datetime.now(timezone.utc)
    assert record_rfp_open("rfp-1", "a@zo.agency", now=t0) is True
    assert record_rfp_open("rfp-1", "a@zo.agency", now=t0 + timedelta(minutes=31)) is True
    assert len(_opens()) == 2


def test_other_person_or_rfp_records_separately(store):
    assert record_rfp_open("rfp-1", "a@zo.agency") is True
    assert record_rfp_open("rfp-1", "b@zo.agency") is True
    assert record_rfp_open("rfp-2", "a@zo.agency") is True
    assert len(_opens()) == 3


def test_unknown_rfp_writes_nothing(store, monkeypatch):
    monkeypatch.setattr("app.services.rfp_opens.rfp_exists", lambda _id: False)
    assert record_rfp_open("nope", "a@zo.agency") is False
    assert _opens() == []


def test_blank_actor_writes_nothing(store):
    assert record_rfp_open("rfp-1", "") is False
    assert record_rfp_open("rfp-1", "not-an-email") is False
    assert _opens() == []


def test_exclude_action_hides_opens(store):
    record_rfp_open("rfp-1", "a@zo.agency")
    emit_activity(workspace="rfp", action="rfp.deleted", summary="Deleted RFP", entity_id="rfp-1")
    actions = [
        i["action"]
        for i in list_activity(workspace="rfp", exclude_action=OPEN_ACTION)["items"]
    ]
    assert actions == ["rfp.deleted"]


def test_stats_ignore_opens(store):
    record_rfp_open("rfp-1", "a@zo.agency")
    stats = list_activity(workspace="rfp")["stats"]
    assert stats == {"events_today": 0, "unique_actors_7d": 0}


def test_with_stats_false_skips_stats(store):
    result = list_activity(workspace="rfp", with_stats=False)
    assert result["stats"] == {"events_today": 0, "unique_actors_7d": 0}


def test_endpoint_records_open_for_header_actor(store):
    client = TestClient(app)
    res = client.post(
        "/api/v1/activity/rfp-opened",
        json={"rfpId": "rfp-9"},
        headers={"X-User-Email": "c@zo.agency"},
    )
    assert res.status_code == 200
    assert res.json() == {"recorded": True}
    assert _opens()[0]["actor_email"] == "c@zo.agency"
