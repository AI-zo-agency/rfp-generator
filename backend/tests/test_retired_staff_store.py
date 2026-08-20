"""Retired staff store — UI-driven, used by agents as the do-not-assign list."""

from __future__ import annotations

from pathlib import Path

from app.services import retired_staff_store
from app.services.evidence_trust.personnel_grounding import (
    find_retired_team_names,
    is_retired_team_member,
    retired_team_personnel,
)


def test_seed_includes_ron_comer(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(retired_staff_store, "_STORE_PATH", tmp_path / "retired_staff.json")
    names = retired_team_personnel()
    assert "Ron Comer" in names
    assert is_retired_team_member("Ron Comer")
    assert find_retired_team_names("Assign Ron Comer as SAM") == ["Ron Comer"]


def test_mark_and_unmark_retired(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(retired_staff_store, "_STORE_PATH", tmp_path / "retired_staff.json")
    retired_staff_store.set_retired(
        person_id="haley-neff",
        name="Haley Neff",
        retired=True,
    )
    assert is_retired_team_member("Haley Neff")
    assert "Haley Neff" in retired_team_personnel()

    retired_staff_store.set_retired(
        person_id="haley-neff",
        name="Haley Neff",
        retired=False,
    )
    assert not is_retired_team_member("Haley Neff")
    assert "Haley Neff" not in retired_team_personnel()


def test_retired_endpoint_round_trip(tmp_path: Path, monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.setattr(retired_staff_store, "_STORE_PATH", tmp_path / "retired_staff.json")
    client = TestClient(app)
    res = client.patch(
        "/api/v1/knowledge-base/key-personas/retired",
        json={"personId": "test-person", "name": "Test Person", "retired": True},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert any(row["name"] == "Test Person" for row in body["retired"])
    personas = body["personas"]
    match = next(p for p in personas if p["id"] == "test-person")
    assert match["retired"] is True
