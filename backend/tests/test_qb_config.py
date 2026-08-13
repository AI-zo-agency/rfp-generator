from app.core.config import Settings


def test_cron_secret_defaults_empty(monkeypatch):
    monkeypatch.delenv("QUICKBOOKS_CRON_SECRET", raising=False)
    assert Settings().quickbooks_cron_secret == ""


def test_cron_secret_from_env(monkeypatch):
    monkeypatch.setenv("QUICKBOOKS_CRON_SECRET", "s3cret")
    assert Settings().quickbooks_cron_secret == "s3cret"
