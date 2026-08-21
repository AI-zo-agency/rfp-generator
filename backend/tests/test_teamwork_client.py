"""Teamwork V3 HTTP client — auth, pagination, errors, rate limits."""

from __future__ import annotations

import base64
import json
import logging

import httpx
import pytest

from app.financial.teamwork import client as tw
from app.financial.teamwork.errors import (
    TeamworkAuthError,
    TeamworkError,
    TeamworkForbiddenError,
    TeamworkNotFoundError,
    TeamworkRateLimitError,
    TeamworkResponseError,
    TeamworkServerError,
    TeamworkTimeoutError,
)


def _response(
    payload: dict | str,
    status: int = 200,
    headers: dict | None = None,
    url: str = "https://zoagency.teamwork.com/projects/api/v3/projects.json",
) -> httpx.Response:
    request = httpx.Request("GET", url)
    if isinstance(payload, str):
        return httpx.Response(status, text=payload, headers=headers or {}, request=request)
    return httpx.Response(status, json=payload, headers=headers or {}, request=request)


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(tw.settings, "teamwork_base_url", "https://zoagency.teamwork.com")
    monkeypatch.setattr(tw.settings, "teamwork_api_key", "test-api-key")
    monkeypatch.setattr(tw, "_sleep", lambda _seconds: None)
    return None


def test_not_configured_raises_before_http(monkeypatch):
    monkeypatch.setattr(tw.settings, "teamwork_base_url", "")
    monkeypatch.setattr(tw.settings, "teamwork_api_key", "")
    called = []
    monkeypatch.setattr(tw.httpx, "get", lambda *a, **k: called.append(True))
    with pytest.raises(TeamworkError, match="not configured"):
        tw.request_json("/projects/api/v3/projects.json")
    assert called == []


def test_basic_auth_header_is_api_key_with_empty_password(configured, monkeypatch):
    captured = {}

    def fake_get(url, headers=None, params=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["params"] = params
        captured["timeout"] = timeout
        return _response({"projects": [], "meta": {"page": {"hasMore": False, "pageSize": 50}}})

    monkeypatch.setattr(tw.httpx, "get", fake_get)
    tw.request_json("/projects/api/v3/projects.json")
    expected = "Basic " + base64.b64encode(b"test-api-key:").decode("ascii")
    assert captured["headers"]["Authorization"] == expected
    assert captured["headers"]["Accept"] == "application/json"
    assert captured["url"] == "https://zoagency.teamwork.com/projects/api/v3/projects.json"
    assert captured["timeout"] == tw.TIMEOUT_SECONDS


def test_origin_strips_api_path(configured, monkeypatch):
    monkeypatch.setattr(
        tw.settings,
        "teamwork_base_url",
        "https://zoagency.teamwork.com/projects/api/v3",
    )
    assert tw.origin() == "https://zoagency.teamwork.com"


def test_does_not_log_api_key(configured, monkeypatch, caplog):
    def fake_get(url, headers=None, params=None, timeout=None):
        return _response({"projects": []})

    monkeypatch.setattr(tw.httpx, "get", fake_get)
    with caplog.at_level(logging.DEBUG):
        tw.request_json("/projects/api/v3/projects.json")
    joined = " ".join(record.getMessage() for record in caplog.records)
    assert "test-api-key" not in joined
    assert "Authorization" not in joined


def test_401_raises_auth_error(configured, monkeypatch):
    monkeypatch.setattr(
        tw.httpx,
        "get",
        lambda *a, **k: _response({"errors": [{"title": "unauthorized"}]}, status=401),
    )
    with pytest.raises(TeamworkAuthError, match="401"):
        tw.request_json("/projects/api/v3/projects.json")


def test_403_raises_forbidden(configured, monkeypatch):
    monkeypatch.setattr(
        tw.httpx,
        "get",
        lambda *a, **k: _response({"errors": [{"title": "forbidden"}]}, status=403),
    )
    with pytest.raises(TeamworkForbiddenError, match="403"):
        tw.request_json("/projects/api/v3/projects.json")


def test_404_raises_not_found(configured, monkeypatch):
    monkeypatch.setattr(
        tw.httpx,
        "get",
        lambda *a, **k: _response("", status=404),
    )
    with pytest.raises(TeamworkNotFoundError, match="404"):
        tw.request_json("/projects/api/v3/projects.json")


def test_429_retries_then_succeeds(configured, monkeypatch):
    attempts = {"n": 0}

    def fake_get(url, headers=None, params=None, timeout=None):
        attempts["n"] += 1
        if attempts["n"] < 3:
            return _response("rate limited", status=429, headers={"X-Rate-Limit-Reset": "1"})
        return _response({"projects": [{"id": 1, "name": "A"}]})

    sleeps = []
    monkeypatch.setattr(tw, "_sleep", lambda seconds: sleeps.append(seconds))
    monkeypatch.setattr(tw.httpx, "get", fake_get)
    payload = tw.request_json("/projects/api/v3/projects.json")
    assert payload["projects"][0]["name"] == "A"
    assert attempts["n"] == 3
    assert sleeps


def test_429_exhausted_raises(configured, monkeypatch):
    monkeypatch.setattr(
        tw.httpx,
        "get",
        lambda *a, **k: _response("rate limited", status=429),
    )
    with pytest.raises(TeamworkRateLimitError, match="429"):
        tw.request_json("/projects/api/v3/projects.json")


def test_500_retries_then_raises(configured, monkeypatch):
    monkeypatch.setattr(
        tw.httpx,
        "get",
        lambda *a, **k: _response("oops", status=503),
    )
    with pytest.raises(TeamworkServerError, match="503"):
        tw.request_json("/projects/api/v3/projects.json")


def test_timeout_raises(configured, monkeypatch):
    def fake_get(*a, **k):
        raise httpx.TimeoutException("timed out")

    monkeypatch.setattr(tw.httpx, "get", fake_get)
    with pytest.raises(TeamworkTimeoutError, match="timed out"):
        tw.request_json("/projects/api/v3/projects.json")


def test_malformed_json_raises(configured, monkeypatch):
    monkeypatch.setattr(
        tw.httpx,
        "get",
        lambda *a, **k: _response("not-json {", status=200),
    )
    with pytest.raises(TeamworkResponseError, match="malformed"):
        tw.request_json("/projects/api/v3/projects.json")


def test_non_object_json_raises(configured, monkeypatch):
    request = httpx.Request("GET", "https://zoagency.teamwork.com/projects/api/v3/projects.json")
    monkeypatch.setattr(
        tw.httpx,
        "get",
        lambda *a, **k: httpx.Response(200, json=["nope"], request=request),
    )
    with pytest.raises(TeamworkResponseError, match="malformed"):
        tw.request_json("/projects/api/v3/projects.json")


def test_paginate_follows_has_more(configured, monkeypatch):
    pages = {
        1: {
            "projects": [{"id": 1, "name": "One"}],
            "meta": {"page": {"hasMore": True, "pageSize": 1, "pageOffset": 0}},
        },
        2: {
            "projects": [{"id": 2, "name": "Two"}],
            "meta": {"page": {"hasMore": False, "pageSize": 1, "pageOffset": 1}},
        },
    }

    def fake_get(url, headers=None, params=None, timeout=None):
        page = int((params or {}).get("page") or 1)
        return _response(pages[page])

    monkeypatch.setattr(tw.httpx, "get", fake_get)
    rows = tw.paginate("/projects/api/v3/projects.json", "projects", params={"pageSize": 1})
    assert [r["id"] for r in rows] == [1, 2]


def test_paginate_empty_list(configured, monkeypatch):
    monkeypatch.setattr(
        tw.httpx,
        "get",
        lambda *a, **k: _response({"projects": [], "meta": {"page": {"hasMore": False}}}),
    )
    assert tw.paginate("/projects/api/v3/projects.json", "projects") == []


def test_paginate_stops_when_short_page_without_meta(configured, monkeypatch):
    monkeypatch.setattr(
        tw.httpx,
        "get",
        lambda *a, **k: _response({"projects": [{"id": 9}]}),
    )
    rows = tw.paginate("/projects/api/v3/projects.json", "projects", params={"pageSize": 500})
    assert len(rows) == 1


def test_list_tasks_sends_overdue_filter(configured, monkeypatch):
    captured = {}

    def fake_get(url, headers=None, params=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        return _response({"tasks": [], "meta": {"page": {"hasMore": False}}})

    monkeypatch.setattr(tw.httpx, "get", fake_get)
    tw.list_tasks({"taskFilter": "overdue"})
    assert captured["url"].endswith("/projects/api/v3/tasks.json")
    assert captured["params"]["taskFilter"] == "overdue"


def test_list_projects_requests_open_operational_statuses(configured, monkeypatch):
    """`active` is Teamwork's not-deleted lifecycle and includes completed projects."""
    captured = {}

    def fake_get(url, headers=None, params=None, timeout=None):
        captured["params"] = params
        return _response({"projects": [], "meta": {"page": {"hasMore": False}}})

    monkeypatch.setattr(tw.httpx, "get", fake_get)
    tw.list_projects()
    assert captured["params"]["projectStatuses"] == "current,late,upcoming"
    assert "active" not in captured["params"]["projectStatuses"].split(",")
    assert "completed" not in captured["params"]["projectStatuses"].split(",")


def test_list_projects_empty(configured, monkeypatch):
    monkeypatch.setattr(
        tw.httpx,
        "get",
        lambda *a, **k: _response({"projects": [], "meta": {"page": {"hasMore": False}}}),
    )
    rows, included = tw.list_projects()
    assert rows == []
    assert included == {}
