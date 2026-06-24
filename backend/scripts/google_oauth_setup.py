#!/usr/bin/env python3
"""
One-time setup: authorize your Google account and print a refresh token.

Add these to backend/.env first:
  GOOGLE_CLIENT_ID=....apps.googleusercontent.com
  GOOGLE_CLIENT_SECRET=...

Then run:
  cd backend
  source .venv/bin/activate
  pip install google-auth-oauthlib   # if not installed
  python scripts/google_oauth_setup.py

A browser window opens. Sign in with the Google account that has access to
your Drive folders. Copy the printed GOOGLE_REFRESH_TOKEN into backend/.env.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from google_auth_oauthlib.flow import InstalledAppFlow

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_ENV_FILE = _BACKEND_ROOT / ".env"

DRIVE_READONLY_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
DRIVE_SCOPES = [DRIVE_READONLY_SCOPE]
REDIRECT_URI = "http://localhost:8080/"


def _load_env() -> None:
    if not _ENV_FILE.is_file():
        raise SystemExit(f"Missing {_ENV_FILE} — create it from .env.example")
    load_dotenv(_ENV_FILE, override=True)


def main() -> None:
    _load_env()

    client_id = os.getenv("GOOGLE_CLIENT_ID", "").strip()
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        raise SystemExit(
            f"Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in {_ENV_FILE}"
        )

    client_config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [REDIRECT_URI, "http://localhost"],
        }
    }

    flow = InstalledAppFlow.from_client_config(client_config, scopes=DRIVE_SCOPES)
    # prompt=consent forces a refresh token even if you authorized this app before
    credentials = flow.run_local_server(
        port=8080,
        open_browser=True,
        prompt="consent",
        access_type="offline",
    )

    if not credentials.refresh_token:
        raise SystemExit(
            "No refresh token returned. Try:\n"
            "  1. Google Cloud Console → OAuth consent screen → Testing → add your email\n"
            "  2. https://myaccount.google.com/permissions → remove this app → run this script again\n"
            "  3. Use an incognito window when the browser opens"
        )

    print(f"\nAdd this line to {_ENV_FILE}:\n")
    print(f"GOOGLE_REFRESH_TOKEN={credentials.refresh_token}")
    print()

    _write_refresh_token_to_env(credentials.refresh_token)


def _write_refresh_token_to_env(refresh_token: str) -> None:
    lines = _ENV_FILE.read_text(encoding="utf-8").splitlines()
    key = "GOOGLE_REFRESH_TOKEN="
    updated = False
    for i, line in enumerate(lines):
        if line.startswith(key):
            lines[i] = f"{key}{refresh_token}"
            updated = True
            break
    if not updated:
        lines.append(f"{key}{refresh_token}")
    _ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Updated {key.rstrip('=')} in {_ENV_FILE}")


if __name__ == "__main__":
    main()
