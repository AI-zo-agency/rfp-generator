"""429 wait helpers: Retry-After when present, else jittered exponential backoff."""

from __future__ import annotations

import unittest
from unittest.mock import patch

import httpx

from app.services.llm import (
    _RATE_LIMIT_MAX_WAIT_S,
    _parse_retry_after_seconds,
    _rate_limit_wait_seconds,
)


def _response(
    *,
    status_code: int = 429,
    headers: dict[str, str] | None = None,
    json_body: dict | None = None,
    text: str = "",
) -> httpx.Response:
    content: bytes
    hdrs = dict(headers or {})
    if json_body is not None:
        import json

        content = json.dumps(json_body).encode()
        hdrs.setdefault("Content-Type", "application/json")
    else:
        content = text.encode()
    return httpx.Response(status_code, headers=hdrs, content=content)


class ParseRetryAfterTests(unittest.TestCase):
    def test_seconds_header(self) -> None:
        resp = _response(headers={"Retry-After": "12"})
        self.assertEqual(_parse_retry_after_seconds(resp), 12.0)

    def test_json_body_retry_after(self) -> None:
        resp = _response(json_body={"retry_after": 7})
        self.assertEqual(_parse_retry_after_seconds(resp), 7.0)

    def test_nested_error_retry_after(self) -> None:
        resp = _response(json_body={"error": {"retry_after": 9, "code": "RATE_LIMIT_EXCEEDED"}})
        self.assertEqual(_parse_retry_after_seconds(resp), 9.0)

    def test_missing_returns_none(self) -> None:
        resp = _response(
            json_body={
                "error": {
                    "message": "You have exceeded your rate limit",
                    "code": "RATE_LIMIT_EXCEEDED",
                }
            }
        )
        self.assertIsNone(_parse_retry_after_seconds(resp))

    def test_invalid_header_returns_none(self) -> None:
        resp = _response(headers={"Retry-After": "not-a-date"})
        self.assertIsNone(_parse_retry_after_seconds(resp))


class RateLimitWaitTests(unittest.TestCase):
    def test_uses_retry_after_when_present(self) -> None:
        resp = _response(headers={"Retry-After": "15"})
        with patch("app.services.llm.random.uniform", return_value=0.25):
            wait, source = _rate_limit_wait_seconds(0, resp)
        self.assertEqual(source, "retry_after")
        self.assertAlmostEqual(wait, 15.25)

    def test_retry_after_capped(self) -> None:
        resp = _response(headers={"Retry-After": "999"})
        with patch("app.services.llm.random.uniform", return_value=0.0):
            wait, source = _rate_limit_wait_seconds(0, resp)
        self.assertEqual(source, "retry_after")
        self.assertEqual(wait, _RATE_LIMIT_MAX_WAIT_S)

    def test_exponential_backoff_bases(self) -> None:
        resp = _response(json_body={"error": {"code": "RATE_LIMIT_EXCEEDED"}})
        with patch("app.services.llm.random.uniform", return_value=0.0):
            self.assertEqual(_rate_limit_wait_seconds(0, resp), (2.0, "exponential_backoff"))
            self.assertEqual(_rate_limit_wait_seconds(1, resp), (4.0, "exponential_backoff"))
            self.assertEqual(_rate_limit_wait_seconds(2, resp), (8.0, "exponential_backoff"))

    def test_backoff_includes_jitter(self) -> None:
        resp = _response(json_body={"error": {"code": "RATE_LIMIT_EXCEEDED"}})
        with patch("app.services.llm.random.uniform", return_value=0.6):
            wait, source = _rate_limit_wait_seconds(0, resp)
        self.assertEqual(source, "exponential_backoff")
        self.assertAlmostEqual(wait, 2.6)


if __name__ == "__main__":
    unittest.main()
