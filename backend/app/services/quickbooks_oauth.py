"""QuickBooks Online OAuth — access-token refresh with refresh-token rotation.

Intuit returns a refresh_token on every token response and invalidates the old
one. If the rotated value is not persisted the integration dies silently weeks
later, so persistence is the whole point of this module.
"""

from __future__ import annotations

import base64
import logging
import threading
import time
from typing import Any

import httpx

from app.core.config import settings
from app.financial import qb_repository

logger = logging.getLogger(__name__)

TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
# Access tokens last 3600s; refresh a little early so in-flight calls don't expire.
_ACCESS_TTL_MARGIN = 300

_lock = threading.Lock()
_access_token: str | None = None
_access_expires_at: float = 0.0


def _read_refresh_token() -> str:
    row = qb_repository.get_oauth_tokens(settings.quickbooks_realm_id)
    if row and row.get("refresh_token"):
        return str(row["refresh_token"])
    return settings.quickbooks_refresh_token


def _write_refresh_token(token: str) -> None:
    qb_repository.upsert_oauth_tokens(
        settings.quickbooks_realm_id,
        {"refresh_token": token},
    )


def _basic_auth_header() -> str:
    raw = f"{settings.quickbooks_client_id}:{settings.quickbooks_client_secret}".encode()
    return base64.b64encode(raw).decode()


def refresh() -> dict[str, Any]:
    """Exchange the stored refresh token for a new access token, persisting rotation."""
    refresh_token = _read_refresh_token()
    if not refresh_token:
        raise RuntimeError("No QuickBooks refresh token configured")

    response = httpx.post(
        TOKEN_URL,
        data={"grant_type": "refresh_token", "refresh_token": refresh_token},
        headers={
            "Authorization": f"Basic {_basic_auth_header()}",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
        timeout=30.0,
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"QuickBooks token refresh failed ({response.status_code}): {response.text[:300]}"
        )

    payload = response.json()
    rotated = payload.get("refresh_token")
    if rotated and rotated != refresh_token:
        _write_refresh_token(rotated)
        logger.info("QuickBooks refresh token rotated and persisted")
    return payload


def get_access_token(force: bool = False) -> str:
    """Cached access token. Refreshes when expired or `force` is set."""
    global _access_token, _access_expires_at

    with _lock:
        if not force and _access_token and time.monotonic() < _access_expires_at:
            return _access_token

        payload = refresh()
        _access_token = payload["access_token"]
        ttl = int(payload.get("expires_in", 3600))
        _access_expires_at = time.monotonic() + max(ttl - _ACCESS_TTL_MARGIN, 60)
        return _access_token


def connection_status() -> dict[str, Any]:
    """Cheap health probe for the dashboard — never raises."""
    if not settings.quickbooks_configured:
        return {"connected": False, "reason": "credentials not configured"}
    try:
        payload = refresh()
        days_left = int(payload.get("x_refresh_token_expires_in", 0)) // 86400
        return {
            "connected": True,
            "realm_id": settings.quickbooks_realm_id,
            "environment": settings.quickbooks_environment,
            "refresh_token_days_remaining": days_left,
        }
    except Exception as exc:  # noqa: BLE001 — status probe must not propagate
        return {"connected": False, "reason": str(exc)[:200]}
