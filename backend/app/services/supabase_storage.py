"""Supabase Storage helpers for RFP PDFs."""

from __future__ import annotations

import logging
import time

from app.core.config import settings

logger = logging.getLogger(__name__)

_client = None
_signed_url_cache: dict[str, tuple[str, float]] = {}
_SIGNED_URL_CACHE_BUFFER_SEC = 300  # refresh before token expiry


class SupabaseStorageError(Exception):
    def __init__(self, message: str, *, status_code: int = 502) -> None:
        super().__init__(message)
        self.status_code = status_code


def is_configured() -> bool:
    return bool(settings.supabase_url.strip() and settings.supabase_service_role_key.strip())


def _get_client():
    global _client
    if _client is not None:
        return _client
    if not is_configured():
        raise SupabaseStorageError("Supabase is not configured", status_code=503)
    try:
        from supabase import create_client
    except ImportError as exc:
        raise SupabaseStorageError(
            "supabase package not installed — pip install supabase",
            status_code=503,
        ) from exc

    _client = create_client(
        settings.supabase_url.strip(),
        settings.supabase_service_role_key.strip(),
    )
    return _client


def upload_pdf(*, object_key: str, content: bytes) -> None:
    bucket = settings.supabase_rfp_bucket
    client = _get_client()
    try:
        client.storage.from_(bucket).upload(
            object_key,
            content,
            file_options={"content-type": "application/pdf", "upsert": "true"},
        )
    except Exception as exc:
        logger.exception("Supabase upload failed for %s", object_key)
        raise SupabaseStorageError(f"Supabase upload failed: {exc}") from exc


def download_pdf(object_key: str) -> bytes:
    bucket = settings.supabase_rfp_bucket
    client = _get_client()
    try:
        data = client.storage.from_(bucket).download(object_key)
    except Exception as exc:
        logger.warning("Supabase download failed for %s: %s", object_key, exc)
        raise SupabaseStorageError(f"Supabase download failed: {exc}", status_code=404) from exc
    if not data:
        raise SupabaseStorageError("Empty PDF from Supabase", status_code=404)
    return bytes(data)


def delete_pdf(object_key: str) -> None:
    if not is_configured():
        return
    bucket = settings.supabase_rfp_bucket
    client = _get_client()
    try:
        client.storage.from_(bucket).remove([object_key])
    except Exception as exc:
        logger.warning("Supabase delete failed for %s: %s", object_key, exc)


def create_signed_url(object_key: str, *, expires_in: int = 3600) -> str:
    """Temporary HTTPS URL for private bucket objects (default 1 hour). Cached ~50 min."""
    now = time.time()
    cached = _signed_url_cache.get(object_key)
    if cached and cached[1] > now:
        return cached[0]

    bucket = settings.supabase_rfp_bucket
    client = _get_client()
    try:
        result = client.storage.from_(bucket).create_signed_url(object_key, expires_in)
    except Exception as exc:
        logger.warning("Supabase signed URL failed for %s: %s", object_key, exc)
        raise SupabaseStorageError(f"Signed URL failed: {exc}", status_code=404) from exc

    url: str | None = None
    if isinstance(result, dict):
        for key in ("signedURL", "signedUrl", "signed_url"):
            candidate = result.get(key)
            if isinstance(candidate, str) and candidate.startswith("http"):
                url = candidate
                break
    elif isinstance(result, str) and result.startswith("http"):
        url = result

    if not url:
        raise SupabaseStorageError("Signed URL missing from Supabase response", status_code=502)

    ttl = max(60, expires_in - _SIGNED_URL_CACHE_BUFFER_SEC)
    _signed_url_cache[object_key] = (url, now + ttl)
    return url


def public_url(object_key: str) -> str | None:
    if not is_configured():
        return None
    bucket = settings.supabase_rfp_bucket
    client = _get_client()
    try:
        return client.storage.from_(bucket).get_public_url(object_key)
    except Exception:
        return None
