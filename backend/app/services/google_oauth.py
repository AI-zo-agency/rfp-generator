"""Google Drive OAuth (client ID + secret + refresh token)."""

from __future__ import annotations

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from app.core.config import settings

DRIVE_READONLY_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
DRIVE_SCOPES = [DRIVE_READONLY_SCOPE]


class GoogleOAuthError(Exception):
    pass


def is_oauth_configured() -> bool:
    return bool(
        settings.google_client_id.strip()
        and settings.google_client_secret.strip()
        and settings.google_refresh_token.strip()
    )


def get_credentials() -> Credentials:
    if not is_oauth_configured():
        raise GoogleOAuthError(
            "Google OAuth is not configured. Set GOOGLE_CLIENT_ID, "
            "GOOGLE_CLIENT_SECRET, and GOOGLE_REFRESH_TOKEN in backend/.env. "
            "Run: python scripts/google_oauth_setup.py"
        )

    credentials = Credentials(
        token=None,
        refresh_token=settings.google_refresh_token.strip(),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=settings.google_client_id.strip(),
        client_secret=settings.google_client_secret.strip(),
        scopes=DRIVE_SCOPES,
    )

    if not credentials.valid:
        credentials.refresh(Request())

    return credentials


def build_drive_service():
    credentials = get_credentials()
    return build("drive", "v3", credentials=credentials, cache_discovery=False)
