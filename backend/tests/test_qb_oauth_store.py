from unittest.mock import patch

from app.services import quickbooks_oauth as oauth


def test_write_refresh_token_does_not_touch_json_file(tmp_path, monkeypatch):
    monkeypatch.setattr(oauth.settings, "quickbooks_realm_id", "realm-1")
    monkeypatch.setattr(oauth.settings, "database_path", tmp_path / "rfps.db")
    with patch("app.financial.qb_repository.upsert_oauth_tokens") as upsert:
        oauth._write_refresh_token("rotated-token")
    upsert.assert_called_once()
    assert upsert.call_args[0][0] == "realm-1"
    assert upsert.call_args[0][1]["refresh_token"] == "rotated-token"
    assert not (tmp_path / "quickbooks_token.json").exists()


def test_read_prefers_database_over_env(monkeypatch):
    monkeypatch.setattr(oauth.settings, "quickbooks_realm_id", "realm-1")
    monkeypatch.setattr(oauth.settings, "quickbooks_refresh_token", "env-seed")
    with patch(
        "app.financial.qb_repository.get_oauth_tokens",
        return_value={"refresh_token": "db-token"},
    ):
        assert oauth._read_refresh_token() == "db-token"


def test_read_falls_back_to_env_when_db_empty(monkeypatch):
    monkeypatch.setattr(oauth.settings, "quickbooks_realm_id", "realm-1")
    monkeypatch.setattr(oauth.settings, "quickbooks_refresh_token", "env-seed")
    with patch("app.financial.qb_repository.get_oauth_tokens", return_value=None):
        assert oauth._read_refresh_token() == "env-seed"
