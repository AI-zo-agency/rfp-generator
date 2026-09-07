from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright

from app.core.config import settings

logger = logging.getLogger(__name__)


def session_path() -> Path:
    raw = (settings.justwin_session_path or "").strip()
    if raw:
        return Path(raw)
    return Path(__file__).resolve().parents[3] / "data" / "justwin-session.json"


def get_justwin_base_url() -> str:
    return (settings.justwin_base_url or "https://app.justwin.ai").rstrip("/")


def invalidate_session() -> bool:
    """Delete the saved Playwright storage so the next login is fresh."""
    path = session_path()
    if not path.is_file():
        return False
    try:
        path.unlink()
        logger.info("[justwin-sync] cleared stale session at %s", path)
        return True
    except OSError as exc:
        logger.warning("[justwin-sync] could not clear session %s: %s", path, exc)
        return False


@dataclass
class AuthContext:
    browser: Browser
    context: BrowserContext
    _playwright: object


def _is_login_page(page: Page) -> bool:
    url = page.url
    if "/login" in url or "/sign" in url:
        return True
    email_n = page.locator('input[type="email"], input[name="email"]').count()
    password_n = page.locator('input[type="password"]').count()
    return email_n > 0 and password_n > 0


def _perform_login(page: Page) -> None:
    email = settings.justwin_email
    password = settings.justwin_password
    if not email or not password:
        raise RuntimeError(
            "JUSTWIN_EMAIL and JUSTWIN_PASSWORD are required for first login "
            "(set them in backend/.env locally, or Railway backend Variables)"
        )

    page.locator('input[type="email"], input[name="email"]').first.fill(email)
    page.locator('input[type="password"]').first.fill(password)

    login_btn = page.get_by_role("button", name=re.compile(r"^log in$", re.I))
    if login_btn.count() > 0:
        login_btn.first.click()
    else:
        page.locator('button[type="submit"]').first.click()

    page.wait_for_url(lambda url: "/login" not in str(url), timeout=60_000)
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_timeout(2000)


def _save_session(context: BrowserContext) -> None:
    path = session_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    context.storage_state(path=str(path))
    logger.info("[justwin-sync] saved session to %s", path)


def _open_leads(context: BrowserContext) -> Page:
    page = context.new_page()
    page.goto(
        f"{get_justwin_base_url()}/leads",
        wait_until="domcontentloaded",
        timeout=60_000,
    )
    # SPA auth redirects can lag past domcontentloaded.
    page.wait_for_timeout(2500)
    return page


def get_authenticated_context(*, force_fresh: bool = False) -> AuthContext:
    """Launch Chromium, restore session if present, login when needed.

    Stale cookies are wiped and a fresh email/password login is performed
    automatically — callers should not ask the user to delete the session file.
    """
    headless = settings.justwin_headless
    path = session_path()

    if force_fresh:
        invalidate_session()

    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=headless)

    used_saved_session = path.is_file() and not force_fresh
    if used_saved_session:
        storage = json.loads(path.read_text(encoding="utf-8"))
        context = browser.new_context(storage_state=storage)
    else:
        context = browser.new_context()

    page = _open_leads(context)

    if _is_login_page(page):
        if used_saved_session:
            logger.warning(
                "[justwin-sync] saved session expired — logging in again with credentials"
            )
            page.close()
            context.close()
            invalidate_session()
            context = browser.new_context()
            page = _open_leads(context)
        _perform_login(page)
        _save_session(context)

    if _is_login_page(page) or "/login" in page.url:
        page.close()
        browser.close()
        pw.stop()
        raise RuntimeError(
            "JustWin login failed — still on login page. "
            "Check JUSTWIN_EMAIL / JUSTWIN_PASSWORD."
        )

    page.close()
    return AuthContext(browser=browser, context=context, _playwright=pw)


def ensure_authenticated_page(auth: AuthContext, page: Page) -> tuple[AuthContext, Page]:
    """If ``page`` landed on login, re-auth once and return a fresh leads page."""
    page.wait_for_timeout(1500)
    if not (_is_login_page(page) or "/login" in page.url):
        return auth, page

    logger.warning(
        "[justwin-sync] session lost after open — re-authenticating automatically"
    )
    try:
        page.close()
    except Exception:  # noqa: BLE001
        pass
    close_auth(auth)
    auth = get_authenticated_context(force_fresh=True)
    page = _open_leads(auth.context)
    if _is_login_page(page) or "/login" in page.url:
        raise RuntimeError(
            "JustWin login failed after auto re-auth. "
            "Check JUSTWIN_EMAIL / JUSTWIN_PASSWORD."
        )
    return auth, page


def close_auth(auth: AuthContext) -> None:
    try:
        auth.context.close()
    finally:
        try:
            auth.browser.close()
        finally:
            auth._playwright.stop()  # type: ignore[attr-defined]
