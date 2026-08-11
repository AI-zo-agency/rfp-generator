"""Unit tests for Drive → Supermemory ingest helpers (no live APIs)."""

from __future__ import annotations

import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

_SCRIPTS_ROOT = _BACKEND_ROOT / "scripts"
if str(_SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_ROOT))

import ingest_drive_folder_to_supermemory as ingest  # noqa: E402


def test_parse_filename_kpi_style_case_study():
    parsed = ingest.parse_filename("03_CS_TravelOregon_SocialCampaign_2022.pdf")
    assert parsed.category == "case_study"
    assert parsed.client == "TravelOregon"
    assert parsed.year == "2022"


def test_parse_filename_bio():
    parsed = ingest.parse_filename("04_Bio_SonjaAnderson.pdf")
    assert parsed.category == "team_bio"
    assert "Sonja" in parsed.title


def test_looks_like_pending_file_url():
    url = "https://files.supermemory.ai/ETAAm37w4ZS8eTUNPc4BUa.pdf"
    assert ingest.looks_like_pending_file_url(url) is True
    assert ingest.looks_like_ingest_stub(url) is False


def test_looks_like_ingest_stub_detects_loading_page():
    stub = (
        "The provided document appears to be a broken link or a placeholder "
        "for a Google Drive file, displaying a 'Loading' state."
    )
    assert ingest.looks_like_ingest_stub(stub) is True


def test_looks_like_ingest_stub_accepts_real_content():
    body = "Sonja Anderson has 20+ years of experience in public sector marketing. " * 5
    assert ingest.looks_like_ingest_stub(body) is False


def test_drive_is_newer_than_supermemory():
    drive_file = ingest.DriveFile(
        id="abc",
        name="04_Bio_SonjaAnderson.pdf",
        mime_type="application/pdf",
        modified_time="2026-08-11T10:00:00.000Z",
        web_view_link=None,
    )
    older_doc = {"updatedAt": "2026-08-01T10:00:00.000Z"}
    newer_doc = {"updatedAt": "2026-08-12T10:00:00.000Z"}

    assert ingest._drive_is_newer_than_supermemory(drive_file, older_doc) is True
    assert ingest._drive_is_newer_than_supermemory(drive_file, newer_doc) is False
