"""Live Google Sheets API service for iWorker timesheet ingestion."""

from __future__ import annotations
import logging
import re
from typing import Any, Dict, List, Optional
from googleapiclient.discovery import build
from app.services import google_oauth

logger = logging.getLogger(__name__)

# Exact Google Sheet ID for SONJA ANDERSON time tracker
DEFAULT_REAL_SPREADSHEET_ID = "1KXV3SxEinnxJU6wLMb-QHlQXHhcMQwpJmD5cRFyG-74"

def build_sheets_service():
    """Build Google Sheets API v4 service using OAuth credentials."""
    credentials = google_oauth.get_credentials()
    return build("sheets", "v4", credentials=credentials, cache_discovery=False)

def find_sonja_sheet_id() -> Optional[str]:
    """Search Google Drive for the time tracker Google Sheet or return default real ID."""
    try:
        drive_service = google_oauth.build_drive_service()
        response = (
            drive_service.files()
            .list(
                q="mimeType = 'application/vnd.google-apps.spreadsheet' and (name contains 'SONJA ANDERSON' or name contains 'Sonja' or name contains 'time tracker')",
                fields="files(id, name, webViewLink)",
                pageSize=10,
            )
            .execute()
        )
        files = response.get("files", [])
        if files:
            logger.info(f"Found Google Sheet: {files[0]['name']} (ID: {files[0]['id']})")
            return files[0]["id"]
    except Exception as e:
        logger.warning(f"Could not search Google Drive for time tracker sheet: {e}")
    return DEFAULT_REAL_SPREADSHEET_ID

def extract_spreadsheet_id(url_or_id: str) -> str:
    """Extract 44-char spreadsheet ID from full URL or return ID as-is."""
    match = re.search(r"/d/([a-zA-Z0-9-_]+)", url_or_id)
    if match:
        return match.group(1)
    return url_or_id.strip()

def fetch_live_sheet_rows(spreadsheet_id: Optional[str] = None, range_name: str = "A3500:I4500") -> Optional[List[List[Any]]]:
    """Fetches raw rows dynamically from Google Sheets API live (A3500:I4500)."""
    target_id = spreadsheet_id or find_sonja_sheet_id()
    if not target_id:
        return None

    try:
        sheets_service = build_sheets_service()
        result = (
            sheets_service.spreadsheets()
            .values()
            .get(spreadsheetId=target_id, range=range_name)
            .execute()
        )
        return result.get("values", [])
    except Exception as e:
        logger.error(f"Failed to fetch live values from Google Sheet {target_id}: {e}")
        return None

def parse_sheet_rows_to_timesheets(rows: List[List[Any]]) -> List[Dict[str, Any]]:
    """Dynamically parses raw Google Sheet matrix into structured timesheet records across all months and years."""
    parsed = []
    current_week = "Active Period"
    entry_idx = 1

    valid_days = {"Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"}

    for row in rows:
        if not row or len(row) < 2:
            continue

        day = str(row[1]).strip() if len(row) > 1 else ""

        # Check for week ending row markers
        if "Total week ending" in day or (len(row) > 2 and "Total week ending" in str(row[2])):
            current_week = str(row[3]).strip() if len(row) > 3 else day
            continue

        # Check if row is a valid day entry
        if day in valid_days:
            date_str = str(row[3]).strip() if len(row) > 3 else ""
            start_time = str(row[4]).strip() if len(row) > 4 else ""
            end_time = str(row[5]).strip() if len(row) > 5 else ""
            duration = str(row[6]).strip() if len(row) > 6 else "00:00:00"
            amount_str = str(row[7]).strip() if len(row) > 7 else "$0.00"
            task = str(row[8]).strip() if len(row) > 8 else ""

            # Parse amount
            amt_clean = 0.0
            try:
                amt_clean = float(amount_str.replace("$", "").replace(",", "").strip() or 0.0)
            except ValueError:
                amt_clean = 0.0

            # Parse hours from duration
            hrs = 0.0
            if ":" in duration:
                try:
                    parts = duration.split(":")
                    hrs = float(parts[0]) + float(parts[1]) / 60.0
                except ValueError:
                    hrs = 0.0

            parsed.append({
                "id": f"sheet-row-{entry_idx}",
                "day": day,
                "date": date_str,
                "start_time": start_time,
                "end_time": end_time,
                "duration": duration,
                "hours": round(hrs, 2),
                "amount": round(amt_clean, 2),
                "task": task or ("Off / No hours logged" if hrs == 0 else "iWorker Deliverable Task"),
                "week_ending": current_week
            })
            entry_idx += 1

    return parsed
