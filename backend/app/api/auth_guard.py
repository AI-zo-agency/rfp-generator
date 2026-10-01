"""Every /api/v1 route needs a signed-in user of this app's Supabase project.

Callers send the Supabase access token from /api/auth/login as
`Authorization: Bearer <token>`: the browser directly, and the Next.js server
by forwarding the user's own token. Exceptions: the health check, and the
scheduler / CLI jobs, which already authenticate with X-Cron-Secret.

Any account in the project counts. Sign-up is already limited to zo.agency;
other accounts (E2M staff) exist only if someone created them in Supabase.
"""

from __future__ import annotations

import logging
import time
from hmac import compare_digest

from fastapi import HTTPException, Request

from app.core.config import settings

logger = logging.getLogger(__name__)

PUBLIC_PATHS = {"/api/v1/health"}

# ponytail: process-local cache so each token costs one Supabase round trip per
# 5 minutes. A token revoked (sign-out, disabled user) stays usable for up to
# TOKEN_TTL_S on this process. Shorten the TTL if that window matters.
TOKEN_TTL_S = 300
_verified: dict[str, tuple[str, float]] = {}


def _supabase_email(token: str) -> str | None:
    from supabase import create_client

    client = create_client(settings.supabase_url.strip(), settings.supabase_service_role_key.strip())
    response = client.auth.get_user(token)
    user = getattr(response, "user", None)
    return (getattr(user, "email", None) or "").strip().lower() or None


def verified_email(token: str) -> str | None:
    """The email behind a valid Supabase access token, or None."""
    now = time.monotonic()
    cached = _verified.get(token)
    if cached and cached[1] > now:
        return cached[0]
    try:
        email = _supabase_email(token)
    except Exception as exc:  # expired, malformed, or Supabase unreachable
        logger.info("operation=verify_token status=rejected error=%s", type(exc).__name__)
        return None
    if not email:
        return None
    if len(_verified) > 5000:
        _verified.clear()
    _verified[token] = (email, now + TOKEN_TTL_S)
    return email


def _cron_ok(request: Request) -> bool:
    expected = settings.quickbooks_cron_secret or ""
    secret = request.headers.get("x-cron-secret") or ""
    return bool(expected and secret and compare_digest(secret, expected))


def require_user(request: Request) -> None:
    if request.url.path in PUBLIC_PATHS or _cron_ok(request):
        return
    header = request.headers.get("authorization") or ""
    token = header[7:].strip() if header[:7].lower() == "bearer " else ""
    email = verified_email(token) if token else None
    if not email:
        raise HTTPException(
            status_code=401,
            detail="Sign in required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    request.state.user_email = email
