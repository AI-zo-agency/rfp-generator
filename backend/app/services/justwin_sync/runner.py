from __future__ import annotations

import logging
import shutil
from typing import Any

from app.services import supabase_db as sb
from app.services.justwin_sync.api import (
    collect_leads,
    create_api_client,
    download_solicitation_pdf_bytes,
)
from app.services.justwin_sync.browser import close_auth, get_authenticated_context, get_justwin_base_url
from app.services.justwin_sync.mapper import map_lead_to_rfp
from app.services.rfp_repository import save_manual_pdf, update_rfp_pdf_path, upsert_rfp

logger = logging.getLogger(__name__)


def _playwright_available() -> tuple[bool, str | None]:
    if shutil.which("chromium") or shutil.which("google-chrome"):
        pass
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError:
        return False, (
            "playwright package not installed. "
            "pip install playwright && playwright install chromium"
        )
    return True, None


def run_justwin_sync(
    job_id: str,
    target_date: str,
    tab: str = "all",
) -> dict[str, Any]:
    """
    Run JustWin Playwright sync synchronously (call via asyncio.to_thread).

    target_date: YYYY-MM-DD, or empty string for all dates.
    """
    ok, err = _playwright_available()
    if not ok:
        raise RuntimeError(err)

    logger.info(
        "[justwin-sync] starting job %s (date: %s, tab: %s)",
        job_id,
        target_date or "any",
        tab,
    )

    auth = get_authenticated_context()
    page = auth.context.new_page()
    failure: str | None = None
    rfps_found = 0
    pdfs_downloaded = 0

    try:
        page.goto(
            f"{get_justwin_base_url()}/leads",
            wait_until="domcontentloaded",
            timeout=60_000,
        )
        page.wait_for_timeout(3000)
        if "/login" in page.url:
            raise RuntimeError(
                "Not authenticated — delete JUSTWIN_SESSION_PATH and rerun sync"
            )

        client = create_api_client(page)
        leads = collect_leads(client, target_date or None, tab)
        logger.info("[justwin-sync] found %s matching lead(s)", len(leads))
        rfps_found = len(leads)

        for lead in leads:
            pdf_bytes: bytes | None = None
            try:
                pdf_bytes = download_solicitation_pdf_bytes(client, lead.external_id)
            except Exception as pdf_err:  # noqa: BLE001 — continue other leads
                logger.warning(
                    "[justwin-sync] PDF download warning for %s: %s",
                    lead.external_id,
                    pdf_err,
                )

            if pdf_bytes and not lead.due_date:
                try:
                    from app.services.rfp_due_date import extract_due_date_from_pdf_bytes

                    extracted = extract_due_date_from_pdf_bytes(pdf_bytes)
                    if extracted:
                        lead.due_date = extracted
                except Exception as due_err:  # noqa: BLE001
                    logger.warning(
                        "[justwin-sync] due date extraction skipped: %s", due_err
                    )

            pdf_path_hint = f"pending:{lead.external_id}" if pdf_bytes else None
            record = map_lead_to_rfp(lead, pdf_path_hint)
            upsert_rfp(record)

            if pdf_bytes:
                saved = save_manual_pdf(record.id, pdf_bytes)
                update_rfp_pdf_path(record.id, saved)
                pdfs_downloaded += 1

        if job_id != "manual" and sb.use_supabase_db():
            sb.finish_sync_job(
                job_id,
                status="completed",
                rfps_found=rfps_found,
                pdfs_downloaded=pdfs_downloaded,
                error=None,
            )

        result = {
            "ok": True,
            "jobId": job_id,
            "rfpsFound": rfps_found,
            "pdfsDownloaded": pdfs_downloaded,
            "syncDate": target_date,
            "targetTab": tab,
        }
        logger.info("[justwin-sync] completed: %s", result)
        return result

    except Exception as exc:
        failure = str(exc)
        logger.error("[justwin-sync] failed: %s", failure)
        if job_id != "manual" and sb.use_supabase_db():
            sb.finish_sync_job(
                job_id,
                status="failed",
                rfps_found=0,
                pdfs_downloaded=0,
                error=failure,
            )
        raise
    finally:
        try:
            page.close()
        except Exception:  # noqa: BLE001
            pass
        close_auth(auth)
