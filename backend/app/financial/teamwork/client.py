"""Read-only Teamwork.com V3 HTTP client.

Auth is HTTP Basic with `API_KEY:` (empty password), matching
https://apidocs.teamwork.com/guides/teamwork/authentication

Every call is GET. Credentials never appear in logs.
"""

from __future__ import annotations

import base64
import logging
import time
from typing import Any
from urllib.parse import urlparse

import httpx

from app.core.config import settings
from app.financial.teamwork.errors import (
    TeamworkAuthError,
    TeamworkError,
    TeamworkForbiddenError,
    TeamworkNotFoundError,
    TeamworkRateLimitError,
    TeamworkResponseError,
    TeamworkServerError,
    TeamworkTimeoutError,
    NOT_CONFIGURED_MESSAGE,
)

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 30.0
PAGE_SIZE = 500
MAX_PAGES = 20
MAX_ATTEMPTS = 4

_sleep = time.sleep

PROJECTS_PATH = "/projects/api/v3/projects.json"
TASKS_PATH = "/projects/api/v3/tasks.json"
PEOPLE_PATH = "/projects/api/v3/people.json"
TIME_PATH = "/projects/api/v3/time.json"
MILESTONES_PATH = "/projects/api/v3/milestones.json"


def origin() -> str:
    raw = (settings.teamwork_base_url or "").strip().rstrip("/")
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    if parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return raw


def _basic_auth_header(api_key: str) -> str:
    token = base64.b64encode(f"{api_key}:".encode("utf-8")).decode("ascii")
    return f"Basic {token}"


def _retry_delay(attempt: int, response: httpx.Response | None) -> float:
    if response is not None:
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return min(max(float(retry_after), 0.0), 60.0)
            except ValueError:
                pass
        reset = response.headers.get("X-Rate-Limit-Reset")
        if reset:
            try:
                wait = int(reset) - int(time.time())
                if wait > 0:
                    return min(float(wait), 60.0)
            except ValueError:
                pass
    return min(float(2**attempt), 60.0)


def _raise_for_status(status: int, body: str) -> None:
    snippet = (body or "").replace("\n", " ")[:200]
    if status == 401:
        raise TeamworkAuthError(f"Teamwork 401: {snippet or 'unauthorized'}", status)
    if status == 403:
        raise TeamworkForbiddenError(f"Teamwork 403: {snippet or 'forbidden'}", status)
    if status == 404:
        raise TeamworkNotFoundError(f"Teamwork 404: {snippet or 'not found'}", status)
    if status == 429:
        raise TeamworkRateLimitError(f"Teamwork 429: {snippet or 'rate limited'}", status)
    if status >= 500:
        raise TeamworkServerError(f"Teamwork {status}: {snippet or 'server error'}", status)
    raise TeamworkError(f"Teamwork {status}: {snippet or 'request failed'}", status)


def request_json(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    if not settings.teamwork_configured:
        raise TeamworkError(NOT_CONFIGURED_MESSAGE)

    url = f"{origin()}{path if path.startswith('/') else '/' + path}"
    headers = {
        "Authorization": _basic_auth_header(settings.teamwork_api_key),
        "Accept": "application/json",
    }
    last_response: httpx.Response | None = None
    started = time.perf_counter()

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = httpx.get(url, headers=headers, params=params, timeout=TIMEOUT_SECONDS)
        except httpx.TimeoutException as exc:
            logger.warning(
                "operation=teamwork_get path=%s attempt=%s error=timeout",
                path,
                attempt,
            )
            raise TeamworkTimeoutError("Teamwork request timed out") from exc
        except httpx.RequestError as exc:
            logger.warning(
                "operation=teamwork_get path=%s attempt=%s error=%s",
                path,
                attempt,
                type(exc).__name__,
            )
            raise TeamworkError(f"Teamwork network error: {type(exc).__name__}") from exc

        last_response = response
        duration_ms = int((time.perf_counter() - started) * 1000)
        remaining = response.headers.get("X-Rate-Limit-Remaining")
        logger.info(
            "operation=teamwork_get path=%s status=%s attempt=%s duration_ms=%s rate_remaining=%s",
            path,
            response.status_code,
            attempt,
            duration_ms,
            remaining,
        )

        if response.status_code in (429,) and attempt < MAX_ATTEMPTS:
            delay = _retry_delay(attempt, response)
            logger.warning(
                "operation=teamwork_get path=%s status=429 attempt=%s delay_s=%s",
                path,
                attempt,
                delay,
            )
            _sleep(delay)
            continue
        if response.status_code >= 500 and attempt < MAX_ATTEMPTS:
            delay = _retry_delay(attempt, response)
            logger.warning(
                "operation=teamwork_get path=%s status=%s attempt=%s delay_s=%s",
                path,
                response.status_code,
                attempt,
                delay,
            )
            _sleep(delay)
            continue
        if response.status_code != 200:
            _raise_for_status(response.status_code, response.text)
        try:
            payload = response.json()
        except ValueError as exc:
            raise TeamworkResponseError("Teamwork returned a malformed JSON response") from exc
        if not isinstance(payload, dict):
            raise TeamworkResponseError("Teamwork returned a malformed JSON response")
        return payload

    if last_response is not None:
        _raise_for_status(last_response.status_code, last_response.text)
    raise TeamworkRateLimitError("Teamwork 429: rate limited after retries", 429)


def _collection(payload: dict[str, Any], key: str) -> list[dict[str, Any]]:
    rows = payload.get(key)
    if rows is None and key == "people":
        rows = payload.get("users")
    if rows is None:
        return []
    if not isinstance(rows, list):
        raise TeamworkResponseError(f"Teamwork {key} collection was malformed")
    return [row for row in rows if isinstance(row, dict)]


def _merge_included(target: dict[str, Any], incoming: dict[str, Any]) -> None:
    for group, values in incoming.items():
        if not isinstance(values, dict):
            continue
        bucket = target.setdefault(group, {})
        if isinstance(bucket, dict):
            bucket.update(values)


def _has_more(payload: dict[str, Any], page_len: int, page_size: int) -> bool:
    meta_page = (payload.get("meta") or {}).get("page") if isinstance(payload.get("meta"), dict) else None
    if isinstance(meta_page, dict) and "hasMore" in meta_page:
        return bool(meta_page.get("hasMore"))
    return page_len >= page_size


def paginate_with_included(
    path: str,
    collection_key: str,
    params: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    query = dict(params or {})
    page_size = int(query.get("pageSize") or PAGE_SIZE)
    query.setdefault("pageSize", page_size)
    query.setdefault("skipCounts", True)
    items: list[dict[str, Any]] = []
    included: dict[str, Any] = {}
    page = 1
    while page <= MAX_PAGES:
        query["page"] = page
        payload = request_json(path, params=query)
        page_items = _collection(payload, collection_key)
        items.extend(page_items)
        incoming = payload.get("included")
        if isinstance(incoming, dict):
            _merge_included(included, incoming)
        if not _has_more(payload, len(page_items), page_size):
            break
        if not page_items:
            break
        page += 1
    logger.info(
        "operation=teamwork_paginate path=%s key=%s pages=%s rows=%s",
        path,
        collection_key,
        page,
        len(items),
    )
    return items, included


def paginate(
    path: str,
    collection_key: str,
    params: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    items, _included = paginate_with_included(path, collection_key, params=params)
    return items


def list_projects(extra: dict[str, Any] | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    params = {
        "pageSize": PAGE_SIZE,
        "includeStats": True,
        "include": "companies,projectBudgets",
        # `active` is Teamwork's not-deleted lifecycle and includes completed
        # projects. Operational states are current / late / upcoming.
        "projectStatuses": "current,late,upcoming",
        "skipCounts": True,
    }
    if extra:
        params.update(extra)
    return paginate_with_included(PROJECTS_PATH, "projects", params=params)


def list_tasks(extra: dict[str, Any] | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    params = {
        "pageSize": PAGE_SIZE,
        "include": "users,projects",
        "includeCompletedTasks": False,
        "skipCounts": True,
    }
    if extra:
        params.update(extra)
    return paginate_with_included(TASKS_PATH, "tasks", params=params)


def list_people(extra: dict[str, Any] | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    params = {
        "pageSize": PAGE_SIZE,
        "onlyOwnerCompany": True,
        "includeClients": False,
        "include": "companies",
        "skipCounts": True,
    }
    if extra:
        params.update(extra)
    return paginate_with_included(PEOPLE_PATH, "people", params=params)


def list_timelogs(extra: dict[str, Any] | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    params = {
        "pageSize": PAGE_SIZE,
        "include": "users,projects",
        "includeTotals": True,
        "skipCounts": True,
    }
    if extra:
        params.update(extra)
    return paginate_with_included(TIME_PATH, "timelogs", params=params)


def list_milestones(extra: dict[str, Any] | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    params = {
        "pageSize": PAGE_SIZE,
        "status": "late,upcoming,today",
        "includePercentageComplete": True,
        "include": "projects,users",
        "skipCounts": True,
    }
    if extra:
        params.update(extra)
    return paginate_with_included(MILESTONES_PATH, "milestones", params=params)
