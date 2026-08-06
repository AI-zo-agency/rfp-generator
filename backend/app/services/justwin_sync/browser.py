from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright

from app.core.config import settings


def session_path() -> Path:
    raw = (settings.justwin_session_path or "").strip()
    if raw:
        return Path(raw)
    return Path(__file__).resolve().parents[3] / "data" / "justwin-session.json"


def get_justwin_base_url() -> str:
    return (settings.justwin_base_url or "https://app.justwin.ai").rstrip("/")


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


def get_authenticated_context() -> AuthContext:
    """Launch Chromium, restore session if present, login when needed."""
    headless = settings.justwin_headless
    path = session_path()

    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=headless)

    if path.is_file():
        storage = json.loads(path.read_text(encoding="utf-8"))
        context = browser.new_context(storage_state=storage)
    else:
        context = browser.new_context()

    page = context.new_page()
    page.goto(f"{get_justwin_base_url()}/leads", wait_until="domcontentloaded", timeout=60_000)

    if _is_login_page(page):
        _perform_login(page)
        path.parent.mkdir(parents=True, exist_ok=True)
        context.storage_state(path=str(path))

    if "/login" in page.url:
        browser.close()
        pw.stop()
        raise RuntimeError("JustWin login failed — still on login page")

    page.close()
    return AuthContext(browser=browser, context=context, _playwright=pw)


def close_auth(auth: AuthContext) -> None:
    try:
        auth.context.close()
    finally:
        try:
            auth.browser.close()
        finally:
            auth._playwright.stop()  # type: ignore[attr-defined]
