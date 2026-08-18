from datetime import datetime
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import httpx

from app.core.config import Settings
from app.scheduler import jobs as scheduler_jobs
from app.scheduler import trigger as scheduler_trigger
from app.scheduler.service import build_scheduler, first_run_time
from app.scheduler.trigger import trigger_job


def test_build_scheduler_registers_quickbooks_job():
    scheduler = build_scheduler(
        _settings(
            scheduler_backend_url="http://127.0.0.1:8001",
            scheduler_timezone="America/Los_Angeles",
            quickbooks_cron_secret="s3cret",
        )
    )
    job = scheduler.get_job("quickbooks_nightly")
    assert job is not None
    assert "23" in str(job.trigger)


def test_build_scheduler_registers_teamwork_job():
    scheduler = build_scheduler(
        _settings(
            scheduler_backend_url="http://127.0.0.1:8001",
            scheduler_timezone="America/Los_Angeles",
            quickbooks_cron_secret="s3cret",
        )
    )
    job = scheduler.get_job("teamwork_nightly")
    assert job is not None
    assert "22" in str(job.trigger) or "45" in str(job.trigger)


def test_first_run_is_now_when_run_on_start():
    pacific = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 8, 14, 12, 39, tzinfo=pacific)
    first = first_run_time(
        _settings(
            scheduler_timezone="America/Los_Angeles",
            scheduler_run_on_start=True,
        ),
        now=now,
    )
    assert first == now


def test_first_run_is_cron_when_startup_disabled():
    pacific = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 8, 14, 12, 39, tzinfo=pacific)
    first = first_run_time(
        _settings(
            scheduler_timezone="America/Los_Angeles",
            scheduler_run_on_start=False,
        ),
        now=now,
    )
    assert first is None


def _settings(**overrides) -> Settings:
    loaded = Settings()
    for key, value in overrides.items():
        object.__setattr__(loaded, key, value)
    return loaded


def test_quickbooks_job_is_11pm_pacific():
    job = scheduler_jobs.job_by_id("quickbooks_nightly")
    assert job is not None
    assert job.cron == "0 23 * * *"
    assert job.timezone == "America/Los_Angeles"
    assert job.method == "POST"
    assert job.path == "/api/v1/financials/quickbooks/sync"
    assert job.body == {"mode": "auto"}
    assert job.timeout_seconds == 600


def test_teamwork_job_is_staggered_before_quickbooks():
    job = scheduler_jobs.job_by_id("teamwork_nightly")
    assert job is not None
    assert job.cron == "45 22 * * *"
    assert job.timezone == "America/Los_Angeles"
    assert job.method == "POST"
    assert job.path == "/api/v1/financials/teamwork/sync"
    assert job.body == {"mode": "auto"}
    assert job.timeout_seconds == 600


def test_scheduler_settings_defaults():
    assert Settings.model_fields["scheduler_backend_url"].default == (
        "http://127.0.0.1:8001"
    )
    assert Settings.model_fields["scheduler_timezone"].default == (
        "America/Los_Angeles"
    )
    assert Settings.model_fields["scheduler_run_on_start"].default is True


def test_trigger_posts_sync_with_cron_secret(monkeypatch):
    captured = {}

    def fake_request(method, url, **kwargs):
        captured["method"] = method
        captured["url"] = url
        captured["headers"] = kwargs["headers"]
        captured["json"] = kwargs["json"]
        captured["timeout"] = kwargs["timeout"]
        return httpx.Response(200, json={"status": "success", "mode": "nightly"})

    monkeypatch.setattr(scheduler_trigger.httpx, "request", fake_request)
    result = trigger_job(
        scheduler_jobs.job_by_id("quickbooks_nightly"),
        settings=_settings(
            scheduler_backend_url="http://backend.internal:8000",
            quickbooks_cron_secret="s3cret",
        ),
    )
    assert result["status"] == "success"
    assert captured["method"] == "POST"
    assert captured["url"] == (
        "http://backend.internal:8000/api/v1/financials/quickbooks/sync"
    )
    assert captured["headers"]["X-Cron-Secret"] == "s3cret"
    assert captured["json"] == {"mode": "auto"}
    assert captured["timeout"] == 600


def test_trigger_lease_held_is_skipped(monkeypatch):
    def fake_request(method, url, **kwargs):
        return httpx.Response(409, json={"detail": "lease held"})

    monkeypatch.setattr(scheduler_trigger.httpx, "request", fake_request)
    result = trigger_job(
        scheduler_jobs.job_by_id("quickbooks_nightly"),
        settings=_settings(
            scheduler_backend_url="http://127.0.0.1:8001",
            quickbooks_cron_secret="s3cret",
        ),
    )
    assert result["status"] == "lease_held"


def test_trigger_skips_when_secret_missing(monkeypatch):
    posted = MagicMock()
    monkeypatch.setattr(scheduler_trigger.httpx, "request", posted)
    result = trigger_job(
        scheduler_jobs.job_by_id("quickbooks_nightly"),
        settings=_settings(
            scheduler_backend_url="http://127.0.0.1:8001",
            quickbooks_cron_secret="",
        ),
    )
    assert result["status"] == "skipped"
    posted.assert_not_called()


def test_trigger_timeout_is_logged(monkeypatch):
    def fake_request(method, url, **kwargs):
        raise httpx.TimeoutException("timed out")

    monkeypatch.setattr(scheduler_trigger.httpx, "request", fake_request)
    result = trigger_job(
        scheduler_jobs.job_by_id("quickbooks_nightly"),
        settings=_settings(
            scheduler_backend_url="http://127.0.0.1:8001",
            quickbooks_cron_secret="s3cret",
        ),
    )
    assert result["status"] == "timeout"


def test_trigger_connect_error_is_connection_refused(monkeypatch):
    def fake_request(method, url, **kwargs):
        raise httpx.ConnectError("[Errno 61] Connection refused")

    monkeypatch.setattr(scheduler_trigger.httpx, "request", fake_request)
    result = trigger_job(
        scheduler_jobs.job_by_id("quickbooks_nightly"),
        settings=_settings(
            scheduler_backend_url="http://127.0.0.1:8001",
            quickbooks_cron_secret="s3cret",
        ),
    )
    assert result["status"] == "connection_refused"
    assert result["url"] == (
        "http://127.0.0.1:8001/api/v1/financials/quickbooks/sync"
    )
