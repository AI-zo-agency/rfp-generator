"""Live Google Sheets API service for iWorker timesheet ingestion across all contractor tabs."""

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
    """Fetches raw rows dynamically from Google Sheets API live."""
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

def parse_sheet_rows_to_timesheets(rows: List[List[Any]], contractor_name: str = "iWorker Contractor", default_rate: float = 12.50) -> List[Dict[str, Any]]:
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

            if amt_clean == 0.0 and hrs > 0:
                amt_clean = round(hrs * default_rate, 2)

            parsed.append({
                "id": f"sheet-row-{entry_idx}",
                "contractor": contractor_name,
                "day": day,
                "date": date_str,
                "start_time": start_time,
                "end_time": end_time,
                "duration": duration,
                "hours": round(hrs, 2),
                "rate": default_rate,
                "amount": round(amt_clean, 2),
                "task": task or ("Off / No hours logged" if hrs == 0 else "iWorker Deliverable Task"),
                "week_ending": current_week
            })
            entry_idx += 1

    return parsed


def fetch_all_tabs_and_timesheets(spreadsheet_id: Optional[str] = None) -> Dict[str, Any]:
    """Dynamically fetches all contractor tabs, extracts hourly rate per tab,
    and parses all timesheet entries across all tabs in the workbook.
    """
    target_id = spreadsheet_id or find_sonja_sheet_id()
    if not target_id:
        return {"tabs": [], "timesheets": []}

    try:
        sheets_service = build_sheets_service()
        meta = (
            sheets_service.spreadsheets()
            .get(spreadsheetId=target_id)
            .execute()
        )
    except Exception as e:
        logger.error(f"Failed to fetch metadata from Google Sheet {target_id}: {e}")
        return {"tabs": [], "timesheets": []}

    sheet_items = meta.get("sheets", [])
    ignore_tabs = {"summary", "[weekly totals for formulas]", "payments", "stats", "invoicing"}

    # Regex to detect placeholder/generic tab names — skip them
    _placeholder_pattern = re.compile(
        r"^employee\s*#?\s*\d+$|^contractor\s*#?\s*\d+$|^worker\s*#?\s*\d+$|^placeholder|^untitled",
        re.IGNORECASE,
    )

    tabs_summary: List[Dict[str, Any]] = []
    all_timesheets: List[Dict[str, Any]] = []
    entry_global_idx = 1
    valid_days = {"Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"}

    for sheet_prop in sheet_items:
        props = sheet_prop.get("properties", {})
        title = props.get("title", "").strip()
        if not title or title.lower() in ignore_tabs:
            continue
        # Skip hidden sheets — the API returns ALL sheets including hidden ones;
        # only visible tabs (what the user sees at the bottom of Google Sheets) should be shown.
        if props.get("hidden", False):
            logger.info(f"Skipping hidden tab: '{title}'")
            continue
        # Skip generic placeholder tabs (e.g. "Employee #6", "Employee#7")
        if _placeholder_pattern.match(title):
            logger.info(f"Skipping placeholder tab: '{title}'")
            continue

        try:
            res = (
                sheets_service.spreadsheets()
                .values()
                .get(spreadsheetId=target_id, range=f"'{title}'!A1:I5000")
                .execute()
            )
            rows = res.get("values", [])
        except Exception as exc:
            logger.warning(f"Could not fetch rows for tab '{title}': {exc}")
            continue

        if not rows:
            continue

        # Extract Rate per hour for this tab
        tab_rate = 12.50
        for r in rows[:6]:
            for cell in r:
                cell_str = str(cell).strip()
                if "$" in cell_str or re.search(r"^\d+(?:\.\d+)?$", cell_str):
                    try:
                        cr = float(cell_str.replace("$", "").replace(",", "").strip())
                        if cr > 0 and cr < 500:
                            tab_rate = cr
                            break
                    except ValueError:
                        pass

        tab_hours = 0.0
        tab_spend = 0.0
        tab_entries_count = 0
        current_week = "Active Period"

        for row in rows:
            if not row or len(row) < 2:
                continue

            day = str(row[1]).strip() if len(row) > 1 else ""

            if "Total week ending" in day or (len(row) > 2 and "Total week ending" in str(row[2])):
                current_week = str(row[3]).strip() if len(row) > 3 else day
                continue

            if day in valid_days:
                date_str = str(row[3]).strip() if len(row) > 3 else ""
                start_time = str(row[4]).strip() if len(row) > 4 else ""
                end_time = str(row[5]).strip() if len(row) > 5 else ""
                duration = str(row[6]).strip() if len(row) > 6 else "00:00:00"
                amount_str = str(row[7]).strip() if len(row) > 7 else "$0.00"
                task = str(row[8]).strip() if len(row) > 8 else ""

                hrs = 0.0
                if ":" in duration:
                    try:
                        parts = duration.split(":")
                        hrs = float(parts[0]) + float(parts[1]) / 60.0
                    except ValueError:
                        hrs = 0.0

                amt_clean = 0.0
                try:
                    amt_clean = float(amount_str.replace("$", "").replace(",", "").strip() or 0.0)
                except ValueError:
                    amt_clean = 0.0

                if amt_clean == 0.0 and hrs > 0:
                    amt_clean = round(hrs * tab_rate, 2)

                if hrs > 0 or task:
                    tab_hours += hrs
                    tab_spend += amt_clean
                    if hrs > 0:
                        tab_entries_count += 1

                all_timesheets.append({
                    "id": f"sheet-{re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-')}-{entry_global_idx}",
                    "contractor": title,
                    "day": day,
                    "date": date_str,
                    "start_time": start_time,
                    "end_time": end_time,
                    "duration": duration,
                    "hours": round(hrs, 2),
                    "rate": tab_rate,
                    "amount": round(amt_clean, 2),
                    "task": task or ("Off / No hours logged" if hrs == 0 else "iWorker Deliverable Task"),
                    "week_ending": current_week
                })
                entry_global_idx += 1

        # Only surface tabs that have actual logged time (skip empty/unused sheets)
        if tab_hours > 0:
            tabs_summary.append({
                "name": title,
                "rate": round(tab_rate, 2),
                "total_hours": round(tab_hours, 2),
                "total_spend": round(tab_spend, 2),
                "active_entries": tab_entries_count
            })

    return {
        "tabs": tabs_summary,
        "timesheets": all_timesheets
    }
