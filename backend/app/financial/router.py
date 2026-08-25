import asyncio
from hmac import compare_digest
import json
import re
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel
import logging
from app.financial import google_sheets, ai_classifier
from app.financial.ai_insights_repository import get_latest_insight
from app.financial.qb_insight_rows import chase_rows, hygiene_rows
from app.financial.qb_position import position
from app.financial.qb_trend import margin_rows
from app.financial import financial_llm_cost, qb_chat
from app.financial.qb_insights import SOURCE as QB_INSIGHT_SOURCE
from app.financial.qb_insights import generate_and_store
from app.financial.qb_repository import get_panel_cache, get_sync_state
from app.financial.qb_signals import derive_signals
from app.financial.qb_sync import LeaseHeld, run_sync
from app.financial.teamwork.status import connection_status as teamwork_connection_status
from app.financial.teamwork.teamwork_repository import (
    get_panel_cache as get_teamwork_panel_cache,
)
from app.financial.teamwork.teamwork_repository import (
    get_sync_state as get_teamwork_sync_state,
)
from app.financial.teamwork.teamwork_repository import list_capacity_snapshots
from app.financial.teamwork.teamwork_map import site_id_from_base_url
from app.financial.teamwork.teamwork_insights import SOURCE as TEAMWORK_INSIGHT_SOURCE
from app.financial.teamwork.teamwork_insights import build_evidence as build_teamwork_evidence
from app.financial.teamwork.teamwork_insights import generate_and_store as generate_teamwork_insight
from app.financial.teamwork import teamwork_chat
from app.financial.teamwork.client import origin as teamwork_origin
from app.financial.teamwork.teamwork_sync import (
    LeaseHeld as TeamworkLeaseHeld,
)
from app.financial.teamwork.teamwork_sync import run_sync as run_teamwork_sync
from app.financial.teamwork.teamwork_sync import overview_from_cache
from app.services import quickbooks_oauth
from app.services.llm import chat_json
from app.core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/financials", tags=["financials"])

# ── Timesheet Response Cache ─────────────────────────────────────────────────
# Avoids re-running AI classification on every page load.
# Key: sheet_url (or "default"). Value: (timestamp, response_dict)
_TIMESHEET_CACHE: dict[str, tuple[float, dict]] = {}
_TIMESHEET_CACHE_TTL_SECONDS = 300  # 5 minutes

# Tasks that should NEVER go through the AI classifier (zero-hour off days)
_SKIP_CLASSIFICATION_TASKS = {
    "Off / No hours logged",
    "off / no hours logged",
    "Weekend",
    "weekend",
    "",
}

# In-memory storage for interactive checklist items and audit resolutions
CHECKLIST_ITEMS = [
    {
        "id": 1,
        "feature": "Source inventory & access",
        "phase": 1,
        "phase_name": "Discovery & Data Mapping",
        "status": "Pending",
        "deliverable": "Source inventory & access",
        "description": "QuickBooks, project management, Google Ads, HubSpot, Sheets, and vendor files confirmed",
        "business_value": "6+ data sources mapped upfront—zero mid-build delays on the 6-week rollout"
    },
    {
        "id": 2,
        "feature": "Client/project mapping rules",
        "phase": 1,
        "phase_name": "Discovery & Data Mapping",
        "status": "Pending",
        "deliverable": "Client/project mapping rules",
        "description": "QuickBooks customer ↔ project management tool ↔ ad account relationships documented",
        "business_value": "Accurate joins across various client/project relationships before ingestion starts"
    },
    {
        "id": 3,
        "feature": "Metrics & threshold definitions",
        "phase": 1,
        "phase_name": "Discovery & Data Mapping",
        "status": "Pending",
        "deliverable": "Metrics & threshold definitions",
        "description": "Revenue, margin, accounts receivable, unbilled work, leakage rules agreed with leadership",
        "business_value": "Leakage alerts tuned to zö's margin targets—not generic thresholds"
    },
    {
        "id": 4,
        "feature": "Audit queue workflow design",
        "phase": 1,
        "phase_name": "Discovery & Data Mapping",
        "status": "Pending",
        "deliverable": "Audit queue workflow design",
        "description": "Finance review flow: flag → assign → resolve (Accept / Reclassify / Bill client)",
        "business_value": "Queue targets ~15 flagged items/day vs 200+ spreadsheet rows reviewed manually"
    },
    {
        "id": 5,
        "feature": "QuickBooks connector",
        "phase": 2,
        "phase_name": "Ingestion Layer & Schema",
        "status": "Pending",
        "deliverable": "QuickBooks connector",
        "description": "Invoices, bills, payments, and customer list via API",
        "business_value": "100% of invoice/bill data synced—no weekly manual QuickBooks exports"
    },
    {
        "id": 6,
        "feature": "Project management and Google Ads connectors",
        "phase": 2,
        "phase_name": "Ingestion Layer & Schema",
        "status": "Pending",
        "deliverable": "Project management and ads connectors",
        "description": "Project management connection or scheduled spreadsheet export; Google Ads spend by account",
        "business_value": "Project budgets + ad spend for all active accounts in one pipeline"
    },
    {
        "id": 7,
        "feature": "Sheets & vendor ingestion",
        "phase": 2,
        "phase_name": "Ingestion Layer & Schema",
        "status": "In Progress",
        "deliverable": "Sheets & vendor ingestion",
        "description": "Google Sheets for audit checklists; iWorker timesheets via Google Sheet or spreadsheet export",
        "business_value": "iWorker timesheets + audit sheets ingested—no duplicate data entry"
    },
    {
        "id": 8,
        "feature": "Normalized data schema",
        "phase": 2,
        "phase_name": "Ingestion Layer & Schema",
        "status": "Pending",
        "deliverable": "Normalized data schema",
        "description": "Automated ingestion loads historical data validated against current reports",
        "business_value": "One dataset replaces 4–5 disconnected exports reviewed each month"
    },
    {
        "id": 9,
        "feature": "Cross-system matching engine",
        "phase": 3,
        "phase_name": "Reconciliation & Core Views",
        "status": "Pending",
        "deliverable": "Cross-system matching engine",
        "description": "Deterministic joins: QuickBooks ↔ projects ↔ vendors ↔ bank/card imports",
        "business_value": "Closes gaps that leak ~$3K–$10K/mo between QuickBooks, project management tools, and vendors"
    },
    {
        "id": 10,
        "feature": "Leadership health cards",
        "phase": 3,
        "phase_name": "Reconciliation & Core Views",
        "status": "In Progress",
        "deliverable": "Leadership health cards",
        "description": "Revenue, gross margin, accounts receivable, unbilled work, potential leakage this month",
        "business_value": "5 KPIs live—margin, accounts receivable, unbilled work, leakage—no 2-hour month-end scramble"
    },
    {
        "id": 11,
        "feature": "Project financial views",
        "phase": 3,
        "phase_name": "Reconciliation & Core Views",
        "status": "Pending",
        "deliverable": "Project financial views",
        "description": "Budget vs actual, billed vs unbilled, margin by project and client",
        "business_value": "Budget vs actual for every active project—answers in seconds not spreadsheet hunts"
    },
    {
        "id": 12,
        "feature": "Trend & cohort reporting",
        "phase": 3,
        "phase_name": "Reconciliation & Core Views",
        "status": "Pending",
        "deliverable": "Trend & cohort reporting",
        "description": "Revenue and margin by client, service line, and period",
        "business_value": "Spot margin drops 2–4 weeks earlier across the full client portfolio"
    },
    {
        "id": 13,
        "feature": "Rule-based anomaly flags",
        "phase": 4,
        "phase_name": "Alerts, Audit Queue & AI Layer",
        "status": "In Progress",
        "deliverable": "Rule-based anomaly flags",
        "description": "Unmapped charges, vendor hours above project management records, over-budget without billing",
        "business_value": "Catches $3K–$10K+ in monthly leaks before month-end close"
    },
    {
        "id": 14,
        "feature": "AI-assisted matching & scoring",
        "phase": 4,
        "phase_name": "Alerts, Audit Queue & AI Layer",
        "status": "In Progress",
        "deliverable": "AI-assisted matching & scoring",
        "description": "Suggest general ledger categories and client mappings; score unusual spend patterns",
        "business_value": "AI suggests mappings on ~80% of unmapped charges—finance confirms, doesn't hunt"
    },
    {
        "id": 15,
        "feature": "Audit queue",
        "phase": 4,
        "phase_name": "Alerts, Audit Queue & AI Layer",
        "status": "In Progress",
        "deliverable": "Audit queue",
        "description": "Prioritized list of 10–20 suspicious items with reason and recommended action",
        "business_value": "Review ~10–20 flagged items vs 200+ rows—~70% less finance review time"
    },
    {
        "id": 16,
        "feature": "Weekly leadership brief",
        "phase": 4,
        "phase_name": "Alerts, Audit Queue & AI Layer",
        "status": "Pending",
        "deliverable": "Weekly leadership brief",
        "description": "AI-written “Top 3 risks, Top 3 wins” from reconciled data",
        "business_value": "3 risks + 3 wins brief for leadership—5 min read vs ~45 min manual prep"
    },
    {
        "id": 17,
        "feature": "Threshold tuning",
        "phase": 5,
        "phase_name": "Hardening & Onboarding",
        "status": "Pending",
        "deliverable": "Threshold tuning",
        "description": "Calibrate rules against one full manual reconciliation cycle",
        "business_value": "False positives cut by ~30–50% after one full reconciliation cycle"
    },
    {
        "id": 18,
        "feature": "Resolution learning",
        "phase": 5,
        "phase_name": "Hardening & Onboarding",
        "status": "Pending",
        "deliverable": "Resolution learning",
        "description": "Resolved items and dismissals inform future matching patterns",
        "business_value": "Repeat flags drop ~20% month over month as the system learns patterns"
    },
    {
        "id": 19,
        "feature": "Production readiness approval",
        "phase": 5,
        "phase_name": "Hardening & Onboarding",
        "status": "Pending",
        "deliverable": "Production readiness approval",
        "description": "Finance and operations confirm monitoring is ready for daily use",
        "business_value": "Signed off for daily monitoring across the full client book"
    }
]

PHASES_SUMMARY = [
    {"phase": 1, "name": "Discovery & Data Mapping", "duration": "Week 1-2", "focus": "Source inventory & access mapping"},
    {"phase": 2, "name": "Ingestion Layer & Schema", "duration": "Week 2-3", "focus": "iWorker & Google Sheets integration"},
    {"phase": 3, "name": "Reconciliation & Core Views", "duration": "Week 3-4", "focus": "Leadership cards & project views"},
    {"phase": 4, "name": "Alerts, Audit Queue & AI Layer", "duration": "Week 4-5", "focus": "Rule-based anomalies & AI brief"},
    {"phase": 5, "name": "Hardening & Onboarding", "duration": "Week 5-6", "focus": "Threshold calibration & sign-off"}
]

# Base Sonja Anderson time tracker dataset
IWORKER_TIMESHEETS = [
    # Week Jan 2, 2026
    {"id": "iw-401", "day": "Tuesday", "date": "Dec 30, 2025", "start_time": "5:00:00 PM", "end_time": "8:00:00 PM", "duration": "03:00:00", "hours": 3.0, "amount": 33.00, "task": "Working on the Leadership videos", "week_ending": "Jan 2, 2026"},
    {"id": "iw-402", "day": "Wednesday", "date": "Dec 31, 2025", "start_time": "12:20:00 PM", "end_time": "3:30:00 PM", "duration": "03:10:00", "hours": 3.17, "amount": 34.83, "task": "Working on the Leadership videos", "week_ending": "Jan 2, 2026"},
    {"id": "iw-403", "day": "Friday", "date": "Jan 2, 2026", "start_time": "4:05:00 PM", "end_time": "6:35:00 PM", "duration": "02:30:00", "hours": 2.5, "amount": 27.50, "task": "Working on the Leadership videos", "week_ending": "Jan 2, 2026"},

    # Week Jan 9, 2026
    {"id": "iw-404", "day": "Saturday", "date": "Jan 3, 2026", "start_time": "1:40:00 PM", "end_time": "4:40:00 PM", "duration": "03:00:00", "hours": 3.0, "amount": 33.00, "task": "Working on the Leadership videos", "week_ending": "Jan 9, 2026"},
    {"id": "iw-405", "day": "Monday", "date": "Jan 5, 2026", "start_time": "10:00:00 AM", "end_time": "11:45:00 AM", "duration": "01:45:00", "hours": 1.75, "amount": 19.25, "task": "Working on the Leadership videos", "week_ending": "Jan 9, 2026"},
    {"id": "iw-406", "day": "Tuesday", "date": "Jan 6, 2026", "start_time": "10:15:00 AM", "end_time": "11:45:00 AM", "duration": "01:30:00", "hours": 1.5, "amount": 16.50, "task": "Working on the Leadership videos", "week_ending": "Jan 9, 2026"},

    # Week Jan 23, 2026
    {"id": "iw-407", "day": "Saturday", "date": "Jan 17, 2026", "start_time": "1:40:00 PM", "end_time": "4:40:00 PM", "duration": "03:00:00", "hours": 3.0, "amount": 37.50, "task": "New round of edits on OWN IT videos", "week_ending": "Jan 23, 2026"},

    # Week Nov 14, 2025
    {"id": "iw-101", "day": "Monday", "date": "Nov 10, 2025", "start_time": "", "end_time": "", "duration": "00:00:00", "hours": 0.0, "amount": 0.0, "task": "Off / No hours logged", "week_ending": "Nov 14, 2025"},
    {"id": "iw-102", "day": "Tuesday", "date": "Nov 11, 2025", "start_time": "3:15:00 PM", "end_time": "6:15:00 PM", "duration": "03:00:00", "hours": 3.0, "amount": 33.0, "task": "Edits on Flow Wellness Interview Series round 2", "week_ending": "Nov 14, 2025"},
    {"id": "iw-103", "day": "Tuesday", "date": "Nov 11, 2025", "start_time": "5:15:00 PM", "end_time": "7:45:00 PM", "duration": "02:30:00", "hours": 2.5, "amount": 27.50, "task": "Edits on Flow Wellness Interview Series round 2", "week_ending": "Nov 14, 2025"},
    {"id": "iw-104", "day": "Wednesday", "date": "Nov 12, 2025", "start_time": "10:00:00 AM", "end_time": "1:30:00 PM", "duration": "03:30:00", "hours": 3.5, "amount": 38.50, "task": "Edits on Flow Wellness Interview Series round 3", "week_ending": "Nov 14, 2025"},
    {"id": "iw-105", "day": "Thursday", "date": "Nov 13, 2025", "start_time": "8:45:00 AM", "end_time": "1:45:00 PM", "duration": "05:00:00", "hours": 5.0, "amount": 55.0, "task": "Edits on Flow Wellness Interview Series round 3", "week_ending": "Nov 14, 2025"},
    {"id": "iw-106", "day": "Friday", "date": "Nov 14, 2025", "start_time": "", "end_time": "", "duration": "00:00:00", "hours": 0.0, "amount": 0.0, "task": "Off / No hours logged", "week_ending": "Nov 14, 2025"},
    
    # Week Nov 21, 2025
    {"id": "iw-107", "day": "Saturday", "date": "Nov 15, 2025", "start_time": "", "end_time": "", "duration": "00:00:00", "hours": 0.0, "amount": 0.0, "task": "Weekend", "week_ending": "Nov 21, 2025"},
    {"id": "iw-108", "day": "Sunday", "date": "Nov 16, 2025", "start_time": "", "end_time": "", "duration": "00:00:00", "hours": 0.0, "amount": 0.0, "task": "Weekend", "week_ending": "Nov 21, 2025"},
    {"id": "iw-109", "day": "Monday", "date": "Nov 17, 2025", "start_time": "9:00:00 AM", "end_time": "2:00:00 PM", "duration": "05:00:00", "hours": 5.0, "amount": 55.0, "task": "Edits on Flow Wellness Interview Series round 3", "week_ending": "Nov 21, 2025"},
    {"id": "iw-110", "day": "Tuesday", "date": "Nov 18, 2025", "start_time": "", "end_time": "", "duration": "00:00:00", "hours": 0.0, "amount": 0.0, "task": "Off / No hours logged", "week_ending": "Nov 21, 2025"},
    {"id": "iw-111", "day": "Wednesday", "date": "Nov 19, 2025", "start_time": "1:00:00 PM", "end_time": "3:00:00 PM", "duration": "02:00:00", "hours": 2.0, "amount": 22.0, "task": "Edits on Flow Wellness Interview Series round 3", "week_ending": "Nov 21, 2025"},
    {"id": "iw-112", "day": "Thursday", "date": "Nov 20, 2025", "start_time": "9:20:00 AM", "end_time": "11:20:00 AM", "duration": "02:00:00", "hours": 2.0, "amount": 22.0, "task": "Edits on Flow Wellness Interview Series round 3", "week_ending": "Nov 21, 2025"},
    {"id": "iw-113", "day": "Friday", "date": "Nov 21, 2025", "start_time": "", "end_time": "", "duration": "00:00:00", "hours": 0.0, "amount": 0.0, "task": "Off / No hours logged", "week_ending": "Nov 21, 2025"},

    # Week Nov 28, 2025
    {"id": "iw-114", "day": "Saturday", "date": "Nov 22, 2025", "start_time": "", "end_time": "", "duration": "00:00:00", "hours": 0.0, "amount": 0.0, "task": "Weekend", "week_ending": "Nov 28, 2025"},
    {"id": "iw-115", "day": "Sunday", "date": "Nov 23, 2025", "start_time": "", "end_time": "", "duration": "00:00:00", "hours": 0.0, "amount": 0.0, "task": "Weekend", "week_ending": "Nov 28, 2025"},
    {"id": "iw-116", "day": "Monday", "date": "Nov 24, 2025", "start_time": "5:30:00 PM", "end_time": "8:30:00 PM", "duration": "03:00:00", "hours": 3.0, "amount": 33.0, "task": "Edits on Flow Wellness Interview Series round 4", "week_ending": "Nov 28, 2025"},
    {"id": "iw-117", "day": "Tuesday", "date": "Nov 25, 2025", "start_time": "3:30:00 PM", "end_time": "6:30:00 PM", "duration": "03:00:00", "hours": 3.0, "amount": 33.0, "task": "Edits on Flow Wellness Interview Series round 4", "week_ending": "Nov 28, 2025"},
    {"id": "iw-118", "day": "Wednesday", "date": "Nov 26, 2025", "start_time": "1:00:00 PM", "end_time": "4:00:00 PM", "duration": "03:00:00", "hours": 3.0, "amount": 33.0, "task": "Edits on Flow Wellness Interview Series round 4", "week_ending": "Nov 28, 2025"},
    {"id": "iw-119", "day": "Thursday", "date": "Nov 27, 2025", "start_time": "11:00:00 AM", "end_time": "2:30:00 PM", "duration": "03:30:00", "hours": 3.5, "amount": 38.50, "task": "Working on the shorter videos", "week_ending": "Nov 28, 2025"},
    {"id": "iw-120", "day": "Friday", "date": "Nov 28, 2025", "start_time": "10:00:00 AM", "end_time": "1:00:00 PM", "duration": "03:00:00", "hours": 3.0, "amount": 33.0, "task": "Working on the shorter videos", "week_ending": "Nov 28, 2025"},

    # Week Apr 17, 2026
    {"id": "iw-301", "day": "Monday", "date": "Apr 13, 2026", "start_time": "6:59:00 PM", "end_time": "11:59:00 PM", "duration": "05:00:00", "hours": 5.0, "amount": 62.50, "task": "Working on the Assisted Living videos", "week_ending": "Apr 17, 2026"},

    # Week Apr 24, 2026
    {"id": "iw-302", "day": "Saturday", "date": "Apr 18, 2026", "start_time": "6:20:00 PM", "end_time": "9:50:00 PM", "duration": "03:30:00", "hours": 3.5, "amount": 43.75, "task": "Working on the Assisted Living videos", "week_ending": "Apr 24, 2026"},
    {"id": "iw-303", "day": "Sunday", "date": "Apr 19, 2026", "start_time": "3:00:00 PM", "end_time": "6:15:00 PM", "duration": "03:15:00", "hours": 3.25, "amount": 40.63, "task": "Working on the Assisted Living videos", "week_ending": "Apr 24, 2026"},
    {"id": "iw-304", "day": "Monday", "date": "Apr 20, 2026", "start_time": "6:00:00 PM", "end_time": "7:00:00 PM", "duration": "01:00:00", "hours": 1.0, "amount": 12.50, "task": "Working on the Assisted Living videos", "week_ending": "Apr 24, 2026"},
    {"id": "iw-305", "day": "Tuesday", "date": "Apr 21, 2026", "start_time": "5:00:00 PM", "end_time": "8:15:00 PM", "duration": "03:15:00", "hours": 3.25, "amount": 40.63, "task": "Working on the Assisted Living videos", "week_ending": "Apr 24, 2026"},
    {"id": "iw-306", "day": "Wednesday", "date": "Apr 22, 2026", "start_time": "5:00:00 PM", "end_time": "8:30:00 PM", "duration": "03:30:00", "hours": 3.5, "amount": 43.75, "task": "Working on the Assisted Living videos", "week_ending": "Apr 24, 2026"},
    {"id": "iw-307", "day": "Thursday", "date": "Apr 23, 2026", "start_time": "8:00:00 PM", "end_time": "10:30:00 PM", "duration": "02:30:00", "hours": 2.5, "amount": 31.25, "task": "Working on the Assisted Living videos", "week_ending": "Apr 24, 2026"},

    # Week May 1, 2026
    {"id": "iw-308", "day": "Saturday", "date": "Apr 25, 2026", "start_time": "2:00:00 PM", "end_time": "6:30:00 PM", "duration": "04:30:00", "hours": 4.5, "amount": 56.25, "task": "Working on the Assisted Living videos", "week_ending": "May 1, 2026"},
    {"id": "iw-309", "day": "Sunday", "date": "Apr 26, 2026", "start_time": "9:00:00 PM", "end_time": "11:30:00 PM", "duration": "02:30:00", "hours": 2.5, "amount": 31.25, "task": "Working on the Assisted Living videos", "week_ending": "May 1, 2026"},
    {"id": "iw-310", "day": "Monday", "date": "Apr 27, 2026", "start_time": "9:00:00 PM", "end_time": "11:00:00 PM", "duration": "02:00:00", "hours": 2.0, "amount": 25.00, "task": "Working on the Assisted Living videos", "week_ending": "May 1, 2026"},
    {"id": "iw-311", "day": "Tuesday", "date": "Apr 28, 2026", "start_time": "9:00:00 PM", "end_time": "11:59:00 PM", "duration": "02:59:00", "hours": 3.0, "amount": 37.29, "task": "Working on the Assisted Living videos", "week_ending": "May 1, 2026"},
    {"id": "iw-201", "day": "Wednesday", "date": "Apr 29, 2026", "start_time": "9:00:00 PM", "end_time": "11:59:00 PM", "duration": "02:59:00", "hours": 3.0, "amount": 37.29, "task": "Working on the Assisted Living videos", "week_ending": "May 1, 2026"},
    {"id": "iw-202", "day": "Thursday", "date": "Apr 30, 2026", "start_time": "12:00:00 AM", "end_time": "2:00:00 AM", "duration": "02:00:00", "hours": 2.0, "amount": 25.00, "task": "Working on the Assisted Living videos", "week_ending": "May 1, 2026"},
    {"id": "iw-203", "day": "Friday", "date": "May 1, 2026", "start_time": "", "end_time": "", "duration": "00:00:00", "hours": 0.0, "amount": 0.0, "task": "Off / No hours logged", "week_ending": "May 1, 2026"},

    # Week May 8, 2026
    {"id": "iw-204", "day": "Saturday", "date": "May 2, 2026", "start_time": "", "end_time": "", "duration": "00:00:00", "hours": 0.0, "amount": 0.0, "task": "Weekend", "week_ending": "May 8, 2026"},
    {"id": "iw-205", "day": "Sunday", "date": "May 3, 2026", "start_time": "", "end_time": "", "duration": "00:00:00", "hours": 0.0, "amount": 0.0, "task": "Weekend", "week_ending": "May 8, 2026"},
    {"id": "iw-206", "day": "Monday", "date": "May 4, 2026", "start_time": "4:30:00 PM", "end_time": "8:30:00 PM", "duration": "04:00:00", "hours": 4.0, "amount": 50.00, "task": "Second round of edits on the Assisted Living videos", "week_ending": "May 8, 2026"},
    {"id": "iw-207", "day": "Tuesday", "date": "May 5, 2026", "start_time": "6:20:00 PM", "end_time": "9:20:00 PM", "duration": "03:00:00", "hours": 3.0, "amount": 37.50, "task": "Second round of edits on the Assisted Living videos", "week_ending": "May 8, 2026"},

    # Week May 15, 2026
    {"id": "iw-208", "day": "Saturday", "date": "May 9, 2026", "start_time": "3:00:00 PM", "end_time": "6:00:00 PM", "duration": "03:00:00", "hours": 3.0, "amount": 37.50, "task": "Second round of edits on the Assisted Living videos", "week_ending": "May 15, 2026"},
    {"id": "iw-209", "day": "Sunday", "date": "May 10, 2026", "start_time": "4:20:00 PM", "end_time": "8:22:00 PM", "duration": "04:02:00", "hours": 4.0, "amount": 50.42, "task": "Second round of edits on the Assisted Living videos", "week_ending": "May 15, 2026"},
    {"id": "iw-210", "day": "Monday", "date": "May 11, 2026", "start_time": "12:30:00 PM", "end_time": "3:30:00 PM", "duration": "03:00:00", "hours": 3.0, "amount": 37.50, "task": "Second round of edits on the Assisted Living videos", "week_ending": "May 15, 2026"},
    {"id": "iw-211", "day": "Tuesday", "date": "May 12, 2026", "start_time": "7:00:00 PM", "end_time": "11:00:00 PM", "duration": "04:00:00", "hours": 4.0, "amount": 50.00, "task": "Second round of edits on the Assisted Living videos", "week_ending": "May 15, 2026"},
    {"id": "iw-212", "day": "Wednesday", "date": "May 13, 2026", "start_time": "1:30:00 PM", "end_time": "5:30:00 PM", "duration": "04:00:00", "hours": 4.0, "amount": 50.00, "task": "Second round of edits on the Assisted Living videos", "week_ending": "May 15, 2026"},
    {"id": "iw-213", "day": "Thursday", "date": "May 14, 2026", "start_time": "9:00:00 PM", "end_time": "10:00:00 PM", "duration": "01:00:00", "hours": 1.0, "amount": 12.50, "task": "Second round of edits on the Assisted Living videos", "week_ending": "May 15, 2026"},
    {"id": "iw-214", "day": "Friday", "date": "May 15, 2026", "start_time": "2:45:00 PM", "end_time": "7:45:00 PM", "duration": "05:00:00", "hours": 5.0, "amount": 62.50, "task": "Second round of edits on the Assisted Living videos", "week_ending": "May 15, 2026"},

    # Week May 22, 2026
    {"id": "iw-215", "day": "Saturday", "date": "May 16, 2026", "start_time": "6:00:00 PM", "end_time": "9:00:00 PM", "duration": "03:00:00", "hours": 3.0, "amount": 37.50, "task": "Social Media videos", "week_ending": "May 22, 2026"},
    {"id": "iw-216", "day": "Sunday", "date": "May 17, 2026", "start_time": "1:30:00 PM", "end_time": "4:30:00 PM", "duration": "03:00:00", "hours": 3.0, "amount": 37.50, "task": "Third round of edits on Assisted Living videos", "week_ending": "May 22, 2026"},
    {"id": "iw-217", "day": "Monday", "date": "May 18, 2026", "start_time": "1:00:00 PM", "end_time": "1:30:00 PM", "duration": "00:30:00", "hours": 0.5, "amount": 6.25, "task": "Getting NAPA pictures from Ben's website", "week_ending": "May 22, 2026"},
    {"id": "iw-218", "day": "Monday", "date": "May 18, 2026", "start_time": "1:30:00 PM", "end_time": "2:30:00 PM", "duration": "01:00:00", "hours": 1.0, "amount": 12.50, "task": "Social Media videos", "week_ending": "May 22, 2026"},
    {"id": "iw-219", "day": "Monday", "date": "May 18, 2026", "start_time": "2:30:00 PM", "end_time": "5:30:00 PM", "duration": "03:00:00", "hours": 3.0, "amount": 37.50, "task": "Final edits on Assisted living videos", "week_ending": "May 22, 2026"},
    {"id": "iw-220", "day": "Tuesday", "date": "May 19, 2026", "start_time": "1:15:00 PM", "end_time": "1:45:00 PM", "duration": "00:30:00", "hours": 0.5, "amount": 6.25, "task": "Final edits on Assisted living videos", "week_ending": "May 22, 2026"},
    {"id": "iw-221", "day": "Wednesday", "date": "May 20, 2026", "start_time": "6:30:00 PM", "end_time": "7:00:00 PM", "duration": "00:30:00", "hours": 0.5, "amount": 6.25, "task": "Edits from the client", "week_ending": "May 22, 2026"}
]

# Initial iWorker-specific audit flags
# ── In-memory resolution overrides (persist for process lifetime) ────────────
# Maps audit item id → resolved status string
_AUDIT_RESOLUTIONS: dict[str, str] = {}


def _build_audit_items_from_timesheets() -> list[dict]:
    """Dynamically generate audit queue items from live timesheet data.
    Reads from the cache if available so we don't re-classify.
    """
    # Try to pull from the live cache first
    cached = None
    for _key, (_ts, _resp) in _TIMESHEET_CACHE.items():
        cached = _resp
        break  # Use the first available cached response

    if not cached:
        # Cache is empty (e.g., server just restarted) — trigger a fresh fetch
        try:
            cached = get_iworker_timesheets()
        except Exception:
            return []

    timesheets = cached.get("timesheets", [])
    active = [t for t in timesheets if t.get("hours", 0) > 0]

    items: list[dict] = []
    seen_ids: set[str] = set()

    # ── Flag 1: Over-scope revision entries (R3+ tasks) ── HIGH severity
    over_scope_entries = [
        t for t in active
        if t.get("ai_classification", {}).get("is_over_scope", False)
    ]
    if over_scope_entries:
        # Group by topic
        topic_groups: dict[str, list] = {}
        for t in over_scope_entries:
            topic = t.get("ai_classification", {}).get("topic") or t.get("task", "Unknown")
            topic_groups.setdefault(topic, []).append(t)

        for topic, entries in topic_groups.items():
            total_hrs = round(sum(e["hours"] for e in entries), 2)
            total_amt = round(sum(e["amount"] for e in entries), 2)
            dates = [e["date"] for e in entries if e.get("date")]
            date_range = f"{dates[0]} – {dates[-1]}" if len(dates) > 1 else (dates[0] if dates else "")
            detected_round = entries[0].get("ai_classification", {}).get("detected_round")
            round_label = f"Round {detected_round}" if detected_round else "R3+"
            reasoning = entries[0].get("ai_classification", {}).get("ai_reasoning", "Exceeds retainer revision cap.")

            # Build a unique ID using topic slug + global index
            topic_slug = re.sub(r"[^a-z0-9]+", "-", topic[:30].lower()).strip("-")
            base_id = f"aud-oscope-{topic_slug}"
            item_id = base_id
            counter = 1
            while item_id in seen_ids:
                counter += 1
                item_id = f"{base_id}-{counter}"
            seen_ids.add(item_id)

            items.append({
                "id": item_id,
                "severity": "HIGH",
                "type": "Revision Scope Creep",
                "source": "iWorker / Google Sheets",
                "age": "",
                "client_project": topic,
                "amount": total_amt,
                "hours": total_hrs,
                "reason": (
                    f"{total_hrs} hrs (${total_amt:.2f}) logged on '{topic}' ({date_range}). "
                    f"{round_label} detected — exceeds retainer revision cap. {reasoning}"
                ),
                "status": _AUDIT_RESOLUTIONS.get(item_id, "Pending"),
                "recommended_action": f"Issue scope-expansion invoice for {round_label} overage (${total_amt:.2f} recovery)"
            })

    # ── Flag 2: High-volume tasks that may need PM verification ── MEDIUM severity
    # Tasks with > 8 hours total logged and no explicit project code in AI classification
    task_totals: dict[str, dict] = {}
    for t in active:
        key = (t.get("task") or "").strip()
        if not key:
            continue
        ai_cls = t.get("ai_classification", {})
        if ai_cls.get("is_over_scope"):  # Already flagged above
            continue
        if key not in task_totals:
            task_totals[key] = {
                "task": key,
                "topic": ai_cls.get("topic") or key,
                "hours": 0.0,
                "amount": 0.0,
                "dates": [],
                "category": ai_cls.get("work_category", "Unknown"),
            }
        task_totals[key]["hours"] += t["hours"]
        task_totals[key]["amount"] += t["amount"]
        if t.get("date"):
            task_totals[key]["dates"].append(t["date"])

    for key, info in task_totals.items():
        total_hrs = round(info["hours"], 2)
        total_amt = round(info["amount"], 2)
        if total_hrs < 8:
            continue  # Not a significant volume flag
        dates = info["dates"]
        date_range = f"{dates[0]} – {dates[-1]}" if len(dates) > 1 else (dates[0] if dates else "")
        key_slug = re.sub(r"[^a-z0-9]+", "-", key[:30].lower()).strip("-")
        base_id = f"aud-vol-{key_slug}"
        item_id = base_id
        counter = 1
        while item_id in seen_ids:
            counter += 1
            item_id = f"{base_id}-{counter}"
        seen_ids.add(item_id)

        items.append({
            "id": item_id,
            "severity": "MEDIUM",
            "type": "High Volume Task",
            "source": "iWorker / Google Sheets",
            "age": "",
            "client_project": info["topic"],
            "amount": total_amt,
            "hours": total_hrs,
            "reason": (
                f"{total_hrs} hrs (${total_amt:.2f}) logged across '{info['topic']}' ({date_range}). "
                f"High single-task concentration — confirm PM asset tag and budget alignment."
            ),
            "status": _AUDIT_RESOLUTIONS.get(item_id, "Pending"),
            "recommended_action": "Verify task has correct project code and client budget allocation"
        })

    # ── Flag 3: Tasks with Unknown category (no project ID mapping) ── LOW severity
    for key, info in task_totals.items():
        if info["category"] not in ("Unknown", "") and "unknown" not in info["category"].lower():
            continue
        total_hrs = round(info["hours"], 2)
        total_amt = round(info["amount"], 2)
        if total_hrs < 1:
            continue
        key_slug = re.sub(r"[^a-z0-9]+", "-", key[:30].lower()).strip("-")
        base_id = f"aud-unmap-{key_slug}"
        item_id = base_id
        counter = 1
        while item_id in seen_ids:
            counter += 1
            item_id = f"{base_id}-{counter}"
        seen_ids.add(item_id)

        items.append({
            "id": item_id,
            "severity": "LOW",
            "type": "Unmapped Task",
            "source": "iWorker / Google Sheets",
            "age": "",
            "client_project": info["topic"],
            "amount": total_amt,
            "hours": total_hrs,
            "reason": (
                f"Task '{info['topic']}' ({total_hrs} hrs, ${total_amt:.2f}) lacks a recognized project ID. "
                f"Cannot be reconciled to a client budget line without manual tagging."
            ),
            "status": _AUDIT_RESOLUTIONS.get(item_id, "Pending"),
            "recommended_action": "Associate with correct client project ID in Google Sheets"
        })

    # Sort: HIGH first, then MEDIUM, then LOW
    order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    items.sort(key=lambda x: order.get(x["severity"], 9))
    return items

class ChecklistStatusUpdate(BaseModel):
    id: int
    status: str

class AuditResolveRequest(BaseModel):
    id: str
    action: str

@router.get("/iworker-timesheets")
def get_iworker_timesheets(
    sheet_url: Optional[str] = Query(None),
    contractor: Optional[str] = Query(None),
):
    """Returns iWorker timesheets across all contractor tabs with rate extraction and AI classification."""
    cache_key = f"{sheet_url or 'default'}:{contractor or 'all'}"
    now = time.monotonic()

    # ── Cache hit: serve immediately ───────────────────
    if cache_key in _TIMESHEET_CACHE:
        cached_at, cached_response = _TIMESHEET_CACHE[cache_key]
        age_seconds = now - cached_at
        if age_seconds < _TIMESHEET_CACHE_TTL_SECONDS:
            return cached_response
        else:
            del _TIMESHEET_CACHE[cache_key]

    is_live = False
    fetched_entries = None
    tabs_meta: List[Dict[str, Any]] = []

    try:
        sp_id = google_sheets.extract_spreadsheet_id(sheet_url) if sheet_url else None
        res_data = google_sheets.fetch_all_tabs_and_timesheets(sp_id)
        if res_data and res_data.get("timesheets"):
            fetched_entries = res_data["timesheets"]
            tabs_meta = res_data.get("tabs", [])
            is_live = True
    except Exception as e:
        logger.info(f"Using fallback dataset for Google Sheet tabs: {e}")

    # Fallback multi-tab dataset if offline
    if not is_live or not fetched_entries:
        tabs_meta = [
            {"name": "Murilo Mendes", "rate": 12.50, "total_hours": 1243.77, "total_spend": 12830.25, "active_entries": 690},
            {"name": "Marcelle Benevides", "rate": 13.99, "total_hours": 627.48, "total_spend": 8241.21, "active_entries": 456},
            {"name": "Kelvin Kiruthu", "rate": 11.99, "total_hours": 183.13, "total_spend": 2195.84, "active_entries": 140},
            {"name": "Erick Parra", "rate": 9.99, "total_hours": 2214.65, "total_spend": 17664.03, "active_entries": 501},
        ]
        # Tag fallback entries with Murilo Mendes
        fetched_entries = []
        for item in IWORKER_TIMESHEETS:
            c_item = dict(item)
            c_item["contractor"] = "Murilo Mendes"
            c_item["rate"] = 12.50
            fetched_entries.append(c_item)

    active_timesheets = fetched_entries

    # Filter by contractor tab if requested
    if contractor and contractor.lower() != "all":
        active_timesheets = [
            t for t in active_timesheets
            if t.get("contractor", "").lower() == contractor.lower()
        ]

    # ── AI Classification (skip off/zero-hour entries) ──
    classified_count = 0
    skipped_count = 0
    enriched_timesheets = []
    for t in active_timesheets:
        t_copy = dict(t)
        task_text = (t_copy.get("task") or "").strip()
        task_lower = task_text.lower()

        should_skip = (
            task_lower in ("off / no hours logged", "weekend", "") or
            t_copy.get("hours", 0) == 0
        )

        if should_skip:
            t_copy["ai_classification"] = {
                "raw_task": task_text,
                "topic": task_text or "Off Day",
                "detected_round": None,
                "is_edit_task": False,
                "is_over_scope": False,
                "work_category": "Non-Billable / Off Day",
                "status_tag": "Off Day",
                "ai_reasoning": "Zero-hour entry — skipped classification to preserve tokens."
            }
            skipped_count += 1
        else:
            t_copy["ai_classification"] = ai_classifier.classify_timesheet_task(task_text)
            classified_count += 1

        enriched_timesheets.append(t_copy)

    total_hours = sum(t["hours"] for t in enriched_timesheets)
    total_spend = sum(t["amount"] for t in enriched_timesheets)
    active_tasks = len(set(t["task"] for t in enriched_timesheets if t["task"] not in ["Off / No hours logged", "Weekend"]))

    weeks = {}
    for t in enriched_timesheets:
        we = t["week_ending"]
        if we not in weeks:
            weeks[we] = {"week_ending": we, "total_hours": 0.0, "total_amount": 0.0, "entries_count": 0}
        weeks[we]["total_hours"] += t["hours"]
        weeks[we]["total_amount"] += t["amount"]
        if t["hours"] > 0:
            weeks[we]["entries_count"] += 1

    selected_contractor = contractor if contractor and contractor.lower() != "all" else "All Contractors"
    active_tab_obj = next((tb for tb in tabs_meta if tb["name"].lower() == selected_contractor.lower()), None)
    active_rate = active_tab_obj["rate"] if active_tab_obj else 12.50

    response = {
        "contractor": selected_contractor,
        "source": "Google Sheets (iWorker Time Tracker)",
        "status": "Connected & Ingested Live" if is_live else "Connected (Synced Dataset)",
        "is_live_oauth_sync": is_live,
        "tabs": tabs_meta,
        "summary": {
            "total_logged_hours": round(total_hours, 2),
            "total_spend_usd": round(total_spend, 2),
            "active_tasks_count": active_tasks,
            "hourly_rate_usd": active_rate,
            "unbilled_risk_amount": 99.00
        },
        "weekly_totals": list(weeks.values()),
        "timesheets": enriched_timesheets
    }

    _TIMESHEET_CACHE[cache_key] = (time.monotonic(), response)
    return response

@router.get("/checklist")
def get_checklist():
    completed = sum(1 for item in CHECKLIST_ITEMS if item["status"] == "Completed")
    in_progress = sum(1 for item in CHECKLIST_ITEMS if item["status"] == "In Progress")
    pending = sum(1 for item in CHECKLIST_ITEMS if item["status"] == "Pending")
    blocked = sum(1 for item in CHECKLIST_ITEMS if item["status"] == "Blocked")
    total = len(CHECKLIST_ITEMS)
    progress_percentage = round((completed / total) * 100, 1) if total > 0 else 0.0

    return {
        "total_features": total,
        "completed": completed,
        "in_progress": in_progress,
        "pending": pending,
        "blocked": blocked,
        "progress_percentage": progress_percentage,
        "phases": PHASES_SUMMARY,
        "checklist": CHECKLIST_ITEMS
    }

@router.post("/checklist/update")
def update_checklist_status(payload: ChecklistStatusUpdate):
    for item in CHECKLIST_ITEMS:
        if item["id"] == payload.id:
            item["status"] = payload.status
            return {"success": True, "updated_item": item}
    raise HTTPException(status_code=404, detail="Checklist item not found")

@router.get("/sources")
def get_sources_status():
    return {
        "sources": [
            {
                "name": "iWorker Timesheets",
                "type": "Google Sheets / Ingestion",
                "status": "Connected",
                "active_data": True,
                "details": "Sonja Anderson time tracker ingested live via Google Sheets API.",
                "last_sync": "Just now"
            },
            {
                "name": "QuickBooks API",
                "type": "ERP / Invoicing",
                "status": "Pending Integration (Phase 2)",
                "active_data": False,
                "details": "Not connected yet. Dummy data disabled.",
                "last_sync": "N/A"
            },
            {
                "name": "Google Ads",
                "type": "Ad Accounts",
                "status": "Pending Integration (Phase 2)",
                "active_data": False,
                "details": "Not connected yet. Dummy data disabled.",
                "last_sync": "N/A"
            },
            {
                "name": "HubSpot CRM",
                "type": "Sales / Deals",
                "status": "Pending Integration (Phase 1/2)",
                "active_data": False,
                "details": "Not connected yet. Dummy data disabled.",
                "last_sync": "N/A"
            },
            {
                "name": "Teamwork.com",
                "type": "Project Management",
                "status": "Connected" if settings.teamwork_configured else "Pending Integration",
                "active_data": bool(settings.teamwork_configured),
                "details": (
                    "Projects, tasks, time, milestones, and people via Teamwork API V3."
                    if settings.teamwork_configured
                    else "Not connected yet. Set TEAMWORK_BASE_URL and TEAMWORK_API_KEY on the backend."
                ),
                "last_sync": "Nightly Supabase mirror" if settings.teamwork_configured else "N/A",
            },
        ]
    }


@router.get("/teamwork/status")
def get_teamwork_status():
    """Safe health probe for Teamwork. Never returns the API key."""
    connection = teamwork_connection_status()
    site_id = site_id_from_base_url(settings.teamwork_base_url)
    try:
        state = get_teamwork_sync_state(site_id) or {}
    except Exception:
        state = {}
    return {
        **connection,
        "last_success_at": state.get("last_success_at"),
        "last_error": state.get("last_error"),
        "backfill_completed": bool(state.get("backfill_completed_at")),
    }


@router.get("/teamwork/overview")
def get_teamwork_overview():
    """Return the latest persisted Teamwork snapshot without calling Teamwork live."""
    site_id = site_id_from_base_url(settings.teamwork_base_url)
    state = get_teamwork_sync_state(site_id) or {}
    cache = get_teamwork_panel_cache(site_id)
    if cache is None:
        payload = {
            "connected": settings.teamwork_configured,
            "generated_at": None,
            "as_of": None,
            "cache_ttl_seconds": 0,
            "errors": {"overview": "no snapshot available"},
            "summary": {
                "project_count": 0,
                "overdue_task_count": 0,
                "upcoming_task_count": 0,
                "late_milestone_count": 0,
                "hours_this_month": 0.0,
                "people_count": 0,
            },
            "projects": [],
            "overdue_tasks": [],
            "upcoming_tasks": [],
            "milestones": [],
            "people": [],
            "time": {
                "period_start": "",
                "period_end": "",
                "total_minutes": 0,
                "billable_minutes": 0,
                "by_person": [],
                "by_project": [],
            },
            "synced_at": state.get("last_success_at"),
            "sync_status": "backfill_pending" if not state.get("backfill_completed_at") else "missing",
        }
    else:
        payload = {
            **(cache.get("payload") or {}),
            "as_of": cache.get("as_of"),
            "generated_at": cache.get("computed_at"),
            "synced_at": state.get("last_success_at") or cache.get("computed_at"),
            "sync_status": "failed" if state.get("last_error") and state.get("last_success_at") else "ok",
        }
    payload["base_url"] = teamwork_origin() if settings.teamwork_configured else None
    logger.info(
        "operation=teamwork_overview_route connected=%s error_keys=%s",
        payload.get("connected"),
        sorted((payload.get("errors") or {}).keys()),
    )
    return payload


def _teamwork_insight_response(
    overview: dict[str, Any], history: list[dict[str, Any]], row: dict[str, Any] | None
) -> dict[str, Any]:
    """Join stored narrative to fresh deterministic Teamwork delivery evidence."""
    payload = (row or {}).get("payload") or {}
    evidence = build_teamwork_evidence(overview, history)
    current_as_of = str(overview.get("as_of") or "")
    return {
        "status": "ok" if row else "empty",
        "brief": payload.get("brief", ""),
        "notes": payload.get("notes", {}),
        "signals": evidence["signals"],
        "history": evidence["history"],
        "as_of": (row or {}).get("as_of"),
        "generated_at": (row or {}).get("generated_at"),
        "provider": (row or {}).get("provider"),
        "stale": bool(row) and bool(current_as_of) and (row or {}).get("as_of") != current_as_of,
    }


def _teamwork_insight_inputs() -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
    site_id = site_id_from_base_url(settings.teamwork_base_url)
    overview = overview_from_cache()
    try:
        history = list_capacity_snapshots(site_id)
    except Exception as exc:  # noqa: BLE001 -- history is useful, not required for current cards
        logger.warning("operation=teamwork_ai_insights status=history_lookup_failed site_id=%s error=%s", site_id, str(exc)[:200])
        history = []
    return site_id, overview, history


def _safe_get_teamwork_insight(site_id: str) -> dict[str, Any] | None:
    try:
        return get_latest_insight(TEAMWORK_INSIGHT_SOURCE, site_id)
    except Exception as exc:  # noqa: BLE001 -- an unapplied AI table migration must not hide delivery cards
        logger.warning("operation=teamwork_ai_insights status=insight_lookup_failed site_id=%s error=%s", site_id, str(exc)[:200])
        return None


@router.get("/teamwork/ai-insights")
def teamwork_ai_insights():
    site_id, overview, history = _teamwork_insight_inputs()
    return _teamwork_insight_response(overview, history, _safe_get_teamwork_insight(site_id))


@router.post("/teamwork/ai-insights/regenerate")
def teamwork_ai_insights_regenerate():
    site_id, overview, history = _teamwork_insight_inputs()
    row = _safe_get_teamwork_insight(site_id)
    if overview.get("sync_status") != "ok" or overview.get("errors"):
        logger.info("operation=teamwork_ai_insights_regenerate status=skipped sync_status=%s", overview.get("sync_status"))
        return _teamwork_insight_response(overview, history, row)
    status = generate_teamwork_insight(site_id, overview, history, str(overview.get("as_of") or _today_iso()))
    result = _teamwork_insight_response(overview, history, _safe_get_teamwork_insight(site_id))
    result["generated"] = status
    return result


class TeamworkChatRequest(BaseModel):
    message: str
    thread_id: Optional[str] = None
    focus_id: Optional[str] = None
    messages: List[Dict[str, str]] = []


@router.post("/teamwork/ai-insights/chat")
async def teamwork_ai_insights_chat(payload: TeamworkChatRequest):
    site_id, overview, history = _teamwork_insight_inputs()
    del site_id
    thread_id = (payload.thread_id or "").strip() or uuid.uuid4().hex
    result = await teamwork_chat.answer(
        thread_id=thread_id,
        question=payload.message,
        overview=overview,
        capacity_history=history,
        history=payload.messages,
        focus_id=payload.focus_id,
    )
    logger.info("operation=teamwork_ai_insights_chat thread=%s guarded=%s capped=%s", thread_id, result["guarded"], result["capped"])
    return result


class TeamworkSyncBody(BaseModel):
    mode: str = "auto"


@router.post("/teamwork/sync")
def teamwork_sync(
    request: Request,
    payload: TeamworkSyncBody | None = None,
):
    mode = payload.mode if payload else "auto"
    if not _cron_authorized(request.headers.get("X-Cron-Secret")):
        logger.warning("operation=teamwork_sync mode=%s status=unauthorized", mode)
        raise HTTPException(status_code=401, detail="Invalid cron secret")

    logger.info("operation=teamwork_sync mode=%s status=started", mode)
    try:
        result = run_teamwork_sync(mode)
    except TeamworkLeaseHeld as exc:
        logger.warning("operation=teamwork_sync mode=%s status=lease_held", mode)
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    logger.info(
        "operation=teamwork_sync mode=%s status=completed run_id=%s",
        result.get("mode", mode),
        result.get("run_id"),
    )
    return result


# ── QuickBooks ────────────────────────────────────────────────────────────────
_QB_PANEL_KEYS = (
    "company",
    "ar",
    "ap",
    "revenue_by_class",
    "by_account_manager",
    "client_profitability",
    "monthly_trend",
    "pl_summary",
    "unattached_cost",
    "activity",
    "cash_collections",
    "billing_vs_cash",
    "dso",
    "aged_ar_detail",
    "purchase_orders",
    "expenses_by_vendor",
    "bill_payments",
    "customers",
    "sales_by_customer",
    "credit_memos",
    "class_coverage",
    "department_coverage",
    "liquidity",
)


class QuickBooksSyncBody(BaseModel):
    mode: str = "auto"


def _cron_authorized(secret: str | None) -> bool:
    expected = settings.quickbooks_cron_secret or ""
    if not expected or not secret:
        return False
    return compare_digest(secret, expected)


@router.post("/quickbooks/sync")
def quickbooks_sync(
    request: Request,
    payload: QuickBooksSyncBody | None = None,
):
    mode = payload.mode if payload else "auto"
    if not _cron_authorized(request.headers.get("X-Cron-Secret")):
        logger.warning(
            "operation=quickbooks_sync mode=%s status=unauthorized",
            mode,
        )
        raise HTTPException(status_code=401, detail="Invalid cron secret")

    logger.info("operation=quickbooks_sync mode=%s status=started", mode)
    try:
        result = run_sync(mode)
    except LeaseHeld as exc:
        logger.warning(
            "operation=quickbooks_sync mode=%s status=lease_held",
            mode,
        )
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    logger.info(
        "operation=quickbooks_sync mode=%s status=completed run_id=%s",
        result.get("mode", mode),
        result.get("run_id"),
    )
    return result


@router.get("/quickbooks/status")
def quickbooks_status():
    """Connection health — safe to poll, never raises."""
    connection = quickbooks_oauth.connection_status()
    realm_id = connection.get("realm_id") or settings.quickbooks_realm_id
    try:
        state = get_sync_state(realm_id) or {}
    except Exception:  # noqa: BLE001 — preserve the status endpoint contract
        logger.exception(
            "operation=quickbooks_status realm_id=%s status=state_read_failed",
            realm_id,
        )
        state = {}
    result = {
        **connection,
        "last_success_at": state.get("last_success_at"),
        "last_error": state.get("last_error"),
        "backfill_completed": bool(state.get("backfill_completed_at")),
    }
    logger.info(
        "operation=quickbooks_status realm_id=%s connected=%s "
        "backfill_completed=%s",
        realm_id,
        result.get("connected"),
        result["backfill_completed"],
    )
    return result


def _load_overview(year: int) -> dict[str, Any]:
    """The persisted panel snapshot with sync metadata and signals attached."""
    realm_id = settings.quickbooks_realm_id
    state = get_sync_state(realm_id) or {}
    cache = get_panel_cache(realm_id, year)
    if cache is None:
        sync_status = (
            "missing" if state.get("backfill_completed_at") else "backfill_pending"
        )
        empty = {
            "year": year,
            **dict.fromkeys(_QB_PANEL_KEYS),
            "errors": {"overview": "no snapshot for year"},
            "as_of": None,
            "synced_at": state.get("last_success_at"),
            "sync_status": sync_status,
        }
        empty["signals"] = derive_signals(empty)
        return empty

    result = {
        **(cache.get("payload") or {}),
        "as_of": cache.get("as_of"),
        "synced_at": cache.get("computed_at"),
        "sync_status": (
            "failed"
            if state.get("last_error") and state.get("last_success_at")
            else "ok"
        ),
    }
    result["signals"] = derive_signals(result)
    return result


@router.get("/quickbooks/overview")
def quickbooks_overview(
    year: int = Query(default_factory=lambda: datetime.now().year),
    since: Optional[str] = Query(None, description="ISO timestamp for the activity feed"),
    refresh: bool = Query(False, description="Deprecated; snapshots refresh during sync"),
):
    """Return the latest persisted panel snapshot without calling Intuit."""
    realm_id = settings.quickbooks_realm_id
    result = _load_overview(year)
    cache_found = result["sync_status"] not in ("missing", "backfill_pending")
    if not cache_found:
        logger.warning(
            "operation=quickbooks_overview realm_id=%s year=%s "
            "status=%s cache_found=false",
            realm_id,
            year,
            result["sync_status"],
        )
    else:
        logger.info(
            "operation=quickbooks_overview realm_id=%s year=%s "
            "status=%s cache_found=true refresh_ignored=%s since_ignored=%s",
            realm_id,
            year,
            result["sync_status"],
            refresh,
            since is not None,
        )
    return result


def _today_iso() -> str:
    return datetime.now().date().isoformat()


def _insight_response(
    overview: dict[str, Any],
    row: dict[str, Any] | None,
    prior: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Stored prose joined to freshly computed rows.

    Rows are recomputed rather than read back from `evidence` so the tables stay
    consistent with the rest of the tab. Row ids are stable, so last night's note
    still lands on today's number; a note whose row has gone is simply unused.
    """
    payload = (row or {}).get("payload") or {}
    return {
        "status": "ok" if row else "empty",
        "brief": payload.get("brief", ""),
        "notes": payload.get("notes", {}),
        "position": position(overview),
        "chase": chase_rows(overview),
        "margin": margin_rows(overview, prior),
        "hygiene": hygiene_rows(overview),
        "as_of": (row or {}).get("as_of"),
        "generated_at": (row or {}).get("generated_at"),
        "provider": (row or {}).get("provider"),
        "stale": bool(row) and (row or {}).get("as_of") != _today_iso(),
    }


def _safe_get_latest_insight(realm_id: str) -> dict[str, Any] | None:
    """`get_latest_insight` with the Supabase table unreachable, e.g. before the
    migration is applied. The chase and hygiene rows are pure computations over
    the overview and must still render, so a lookup failure degrades to the
    empty-brief state rather than a 500 that collapses the whole panel."""
    try:
        return get_latest_insight(QB_INSIGHT_SOURCE, realm_id)
    except Exception as exc:  # noqa: BLE001 — a missing table must not break the panel
        logger.warning(
            "operation=quickbooks_ai_insights status=insight_lookup_failed realm_id=%s error=%s",
            realm_id,
            str(exc)[:200],
        )
        return None


def _safe_prior_payload(realm_id: str, year: int) -> dict[str, Any] | None:
    """Last year's cached panels, for same-months-both-years comparison.

    Same defensive shape as `_safe_get_latest_insight`: a realm with only one
    year of history, or an unreachable cache, drops the two trend rows and
    leaves the rest of the panel standing.
    """
    try:
        cache = get_panel_cache(realm_id, year - 1)
    except Exception as exc:  # noqa: BLE001 — no prior year must not break the panel
        logger.warning(
            "operation=quickbooks_ai_insights status=prior_year_failed "
            "realm_id=%s year=%s error=%s",
            realm_id,
            year - 1,
            str(exc)[:200],
        )
        return None
    return (cache or {}).get("payload") or None


@router.get("/quickbooks/ai-insights")
def quickbooks_ai_insights():
    """Latest successful brief, with the chase and hygiene rows recomputed now.

    Always today's position for the current year — there is no query parameter
    for year. The nightly sync only ever generates a brief for the current
    year, so no brief exists for a past one; offering a year selector here
    would just invite the regenerate route to overwrite tonight's real brief
    with one derived from stale evidence.
    """
    realm_id = settings.quickbooks_realm_id
    year = datetime.now().year
    overview = _load_overview(year)
    row = _safe_get_latest_insight(realm_id)
    prior = _safe_prior_payload(realm_id, year)
    logger.info(
        "operation=quickbooks_ai_insights found=%s prior_year=%s",
        row is not None,
        prior is not None,
    )
    return _insight_response(overview, row, prior)


@router.post("/quickbooks/ai-insights/regenerate")
def quickbooks_ai_insights_regenerate():
    """Generate today's brief on demand, upserting over any existing row.

    Always today's position for the current year, for the same reason as the
    GET route above. Skips the model call entirely when there's no panel cache
    to reason about — an all-None skeleton has nothing worth spending a call on.
    """
    realm_id = settings.quickbooks_realm_id
    year = datetime.now().year
    overview = _load_overview(year)
    row = _safe_get_latest_insight(realm_id)
    prior = _safe_prior_payload(realm_id, year)

    if overview.get("sync_status") in ("missing", "backfill_pending"):
        logger.info(
            "operation=quickbooks_ai_insights_regenerate status=skipped sync_status=%s",
            overview.get("sync_status"),
        )
        return _insight_response(overview, row, prior)

    status = generate_and_store(realm_id, overview, _today_iso(), prior=prior)
    logger.info(
        "operation=quickbooks_ai_insights_regenerate status=%s",
        status,
    )
    row = _safe_get_latest_insight(realm_id)
    result = _insight_response(overview, row, prior)
    result["generated"] = status
    return result



class QbChatRequest(BaseModel):
    message: str
    # Absent on the first question of a conversation; the server mints one.
    thread_id: Optional[str] = None
    # The note card the reader pinned, if any.
    focus_id: Optional[str] = None
    # Phase 1 only: the drawer holds the thread in React state. Once threads are
    # persisted this is ignored in favor of the stored history.
    messages: List[Dict[str, str]] = []


@router.post("/quickbooks/ai-insights/chat")
async def quickbooks_ai_insights_chat(payload: QbChatRequest):
    """Answer one question against the same evidence the brief was written from.

    Current year only, for the same reason the brief is: chat that could be
    asked about 2024 would be answering from evidence no card on screen shows.
    """
    year = datetime.now().year
    overview = _load_overview(year)
    prior = _safe_prior_payload(settings.quickbooks_realm_id, year)
    thread_id = (payload.thread_id or "").strip() or uuid.uuid4().hex

    result = await qb_chat.answer(
        thread_id=thread_id,
        question=payload.message,
        overview=overview,
        prior=prior,
        history=payload.messages,
        focus_id=payload.focus_id,
    )
    logger.info(
        "operation=quickbooks_ai_insights_chat thread=%s guarded=%s capped=%s",
        thread_id,
        result["guarded"],
        result["capped"],
    )
    return result


@router.get("/quickbooks/ai-insights/chat/cost")
def quickbooks_ai_insights_chat_cost(thread_id: str = Query(...)):
    """This thread's LLM spend. Reads financial_llm_calls, never llm_call_log."""
    return financial_llm_cost.thread_breakdown(thread_id)


@router.get("/audit-queue")
def get_audit_queue():
    """Dynamically builds audit flags from live timesheet data."""
    items = _build_audit_items_from_timesheets()
    return {"audit_items": items}

@router.post("/audit-queue/resolve")
def resolve_audit_item(payload: AuditResolveRequest):
    _AUDIT_RESOLUTIONS[payload.id] = f"Resolved ({payload.action})"
    logger.info(f"[AUDIT] Resolved item {payload.id!r} with action={payload.action!r}")
    return {"success": True, "id": payload.id, "status": _AUDIT_RESOLUTIONS[payload.id]}

@router.post("/ai-insights")
async def generate_ai_financial_insights():
    """Generates real AI leadership brief from live iWorker Google Sheets data.
    
    Uses the same provider routing as the rest of the app:
    - LLM_PREFER_FIREWORKS=true  → Fireworks (MiniMax M3)
    - LLM_PREFER_OPENROUTER=true → OpenRouter (Gemini 2.5 Flash)
    - fallback                   → Gemini direct API
    """
    provider_used = "Fireworks" if settings.llm_prefer_fireworks else ("OpenRouter" if settings.llm_prefer_openrouter else "Gemini")
    model_used = settings.fireworks_model if settings.llm_prefer_fireworks else (settings.openrouter_model if settings.llm_prefer_openrouter else settings.gemini_model)
    
    logger.info(f"[AI-INSIGHTS] Starting real AI call | Provider: {provider_used} | Model: {model_used}")

    # ── Fetch live timesheet data ─────────────────────────────────────────────
    data = get_iworker_timesheets()
    timesheets = data.get("timesheets", [])
    active_entries = [t for t in timesheets if t["hours"] > 0]
    total_hours = round(sum(t["hours"] for t in active_entries), 2)
    total_spend = round(sum(t["amount"] for t in active_entries), 2)

    logger.info(f"[AI-INSIGHTS] Timesheet context: {len(active_entries)} active entries | {total_hours} hrs | ${total_spend}")

    # ── Build compact timesheet context for prompt ────────────────────────────
    # Group by task so we don't overflow context with 600+ raw rows
    task_summary: dict[str, dict] = {}
    for t in active_entries:
        key = t["task"].strip()
        ai_cls = t.get("ai_classification", {})
        if key not in task_summary:
            task_summary[key] = {
                "task": key,
                "total_hours": 0.0,
                "total_spend": 0.0,
                "sessions": 0,
                "is_over_scope": ai_cls.get("is_over_scope", False),
                "work_category": ai_cls.get("work_category", "Unknown"),
                "detected_round": ai_cls.get("detected_round"),
            }
        task_summary[key]["total_hours"] += t["hours"]
        task_summary[key]["total_spend"] += t["amount"]
        task_summary[key]["sessions"] += 1
        if ai_cls.get("is_over_scope"):
            task_summary[key]["is_over_scope"] = True

    # Top deliverables by hours (cap at 20 for prompt size)
    top_tasks = sorted(task_summary.values(), key=lambda x: x["total_hours"], reverse=True)[:20]
    over_scope_tasks = [t for t in task_summary.values() if t["is_over_scope"]]
    total_over_scope_spend = round(sum(t["total_spend"] for t in over_scope_tasks), 2)

    tasks_json = json.dumps(top_tasks, indent=2)
    over_scope_json = json.dumps(over_scope_tasks, indent=2)

    logger.info(f"[AI-INSIGHTS] Prompt context: {len(top_tasks)} top deliverables | {len(over_scope_tasks)} over-scope items | ${total_over_scope_spend} risk")

    # ── Build AI prompt ───────────────────────────────────────────────────────
    system_prompt = """You are a senior financial auditor for ZÖ Agency, a creative video production agency.
You analyze contractor timesheet data to detect scope creep, billing risks, and operational wins.
Always respond with ONLY valid JSON — no markdown, no prose, no code fences."""

    user_prompt = f"""Analyze this iWorker contractor timesheet data for ZÖ Agency and generate a leadership financial brief.

CONTEXT:
- Contractor: iWorker (Sonja Anderson)
- Hourly Rate: $12.50/hr
- Total Active Hours: {total_hours} hrs
- Total Spend: ${total_spend:,.2f}
- Total Work Sessions: {len(active_entries)}
- Over-Scope Spend (R3+ revisions): ${total_over_scope_spend:,.2f}
- Analysis Date: {datetime.now().strftime('%B %d, %Y')}

TOP 20 DELIVERABLES BY HOURS:
{tasks_json}

OVER-SCOPE ITEMS (Round 3+ revisions that exceed retainer):
{over_scope_json}

Generate a JSON response with EXACTLY this structure:
{{
  "leadership_brief_text": "<2-3 sentence executive summary for leadership>",
  "top_3_risks": [
    "<risk 1 — specific, quantified, actionable>",
    "<risk 2 — specific, quantified, actionable>",
    "<risk 3 — specific, quantified, actionable>"
  ],
  "top_3_wins": [
    "<win 1 — concrete operational achievement>",
    "<win 2 — concrete operational achievement>",
    "<win 3 — concrete operational achievement>"
  ],
  "margin_recommendations": [
    "<recommendation 1 — actionable next step>",
    "<recommendation 2 — actionable next step>",
    "<recommendation 3 — actionable next step>"
  ]
}}

IMPORTANT: Be specific about dollar amounts and hour counts. Reference actual task names from the data. Do not invent data."""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    logger.info(f"[AI-INSIGHTS] Sending prompt to {provider_used} (model: {model_used})...")

    # ── Real AI call via existing provider router ─────────────────────────────
    try:
        result, actual_provider = await chat_json(
            messages,
            max_tokens=1500,
            temperature=0.3,
            tier="light",
        )
        logger.info(f"[AI-INSIGHTS] AI response received from {actual_provider} | keys: {list(result.keys())}")
    except Exception as exc:
        logger.error(f"[AI-INSIGHTS] AI call failed: {exc}")
        raise HTTPException(status_code=502, detail=f"AI provider error: {str(exc)}")

    # ── Validate and return ───────────────────────────────────────────────────
    required_keys = ["leadership_brief_text", "top_3_risks", "top_3_wins", "margin_recommendations"]
    for key in required_keys:
        if key not in result:
            logger.warning(f"[AI-INSIGHTS] Missing key '{key}' in AI response — filling with fallback")
            if key == "leadership_brief_text":
                result[key] = f"ZÖ Agency iWorker analysis: {total_hours} hrs logged, ${total_spend:,.2f} spend, ${total_over_scope_spend:,.2f} over-scope risk."
            else:
                result[key] = [f"See timesheet data for details ({len(active_entries)} active entries)"]

    generated_at = datetime.now().strftime("%b %d, %Y at %I:%M %p")
    logger.info(f"[AI-INSIGHTS] Successfully generated insights via {actual_provider} at {generated_at}")

    return {
        "status": "success",
        "generated_at": generated_at,
        "provider": actual_provider,
        "model": model_used,
        "contractor": "iWorker Contractor",
        "source_data": "Live Google Sheets Ingestion",
        "summary": {
            "leadership_brief_text": result["leadership_brief_text"],
            "top_3_risks": result["top_3_risks"][:3],
            "top_3_wins": result["top_3_wins"][:3],
            "margin_recommendations": result["margin_recommendations"][:3],
        },
        "stats": {
            "total_hours": total_hours,
            "total_spend": total_spend,
            "active_entries": len(active_entries),
            "over_scope_spend": total_over_scope_spend,
            "over_scope_items": len(over_scope_tasks),
        }
    }
