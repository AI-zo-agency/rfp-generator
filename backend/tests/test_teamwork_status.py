from __future__ import annotations

from app.financial.teamwork import status
from app.financial.teamwork.errors import TeamworkAuthError


def test_connection_status_reports_auth_failure_without_credentials(monkeypatch):
    monkeypatch.setattr(status.settings, "teamwork_base_url", "https://zoagency.teamwork.com")
    monkeypatch.setattr(status.settings, "teamwork_api_key", "super-secret-key")
    monkeypatch.setattr(
        status.client,
        "request_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(TeamworkAuthError("Teamwork 401: unauthorized")),
    )

    payload = status.connection_status()

    assert payload["connected"] is False
    assert payload["reason"] == "Teamwork 401: unauthorized"
    assert "super-secret-key" not in str(payload)
