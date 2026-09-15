"""Pull full solicitation PDFs from public buyer portals linked by JustWin.

JustWin's ``/targets/{id}/view`` often points at a thin portal *invitation*
packet (Ebid 5-pager, Bonfire checklist). The real RFP body lives as Bid
Attachments on the buyer's public page (``originating_url``). Ionwave exposes
direct ``extract.aspx`` links — download and merge those into the package.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlparse

logger = logging.getLogger(__name__)

# Prefer these filenames as the front of the merged package (intelligence reads
# the head of the PDF text first).
_CORE_NAME_HINTS = (
    "request for proposal final",
    "request for proposal",
    "rfp final",
    "solicitation",
    "scope of work",
    "statement of work",
)


@dataclass(frozen=True)
class PortalPdf:
    name: str
    data: bytes


def _score_attachment_name(name: str) -> int:
    low = (name or "").casefold()
    score = 0
    for i, hint in enumerate(_CORE_NAME_HINTS):
        if hint in low:
            score = max(score, 100 - i * 10)
    if low.endswith(".pdf"):
        score += 1
    # Deprioritize the thin invitation wrapper when a FINAL exists.
    if "invitation" in low or "bid invitation" in low:
        score -= 40
    if "insurance" in low or "citizenship" in low or "adversary" in low:
        score -= 5
    return score


def sort_portal_pdfs(pdfs: list[PortalPdf]) -> list[PortalPdf]:
    return sorted(
        pdfs,
        key=lambda p: (_score_attachment_name(p.name), len(p.data)),
        reverse=True,
    )


def merge_pdf_bytes(parts: list[bytes]) -> bytes | None:
    """Concatenate PDF pages with pypdf. Returns None when nothing usable."""
    usable = [p for p in parts if p and p[:4] == b"%PDF" and len(p) >= 500]
    if not usable:
        return None
    if len(usable) == 1:
        return usable[0]
    try:
        from io import BytesIO

        from pypdf import PdfReader, PdfWriter

        writer = PdfWriter()
        for blob in usable:
            try:
                reader = PdfReader(BytesIO(blob))
                for page in reader.pages:
                    writer.add_page(page)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[justwin-sync] skip unreadable PDF part: %s", exc)
        if len(writer.pages) == 0:
            return usable[0]
        out = BytesIO()
        writer.write(out)
        return out.getvalue()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[justwin-sync] PDF merge failed, using first part: %s", exc)
        return usable[0]


def _is_ionwave_url(url: str) -> bool:
    host = urlparse(url or "").netloc.casefold()
    return "ionwave.net" in host


# Headless scrape hangs or hits a bot wall on these hosts. Ionwave public
# Bid Attachments still work; everything else must fail fast.
_SKIP_PORTAL_HOST_MARKERS = (
    "bonfirehub.com",
    "bonfire",
    "opengov.com",
    "bidnetdirect.com",
    "bidnet",
    "planetbids.com",
    "procure.org",  # hudsoncountynjprocure.org and similar BidNet skins
)

# Playwright's 90s goto did not abort Chrome TCP hangs (~4 min on dead portals).
PORTAL_GOTO_TIMEOUT_MS = 12_000
PORTAL_PDF_TIMEOUT_MS = 20_000


def should_skip_portal_scrape(url: str) -> bool:
    host = urlparse(url or "").netloc.casefold()
    return any(marker in host for marker in _SKIP_PORTAL_HOST_MARKERS)


def package_looks_thin(pdf_bytes: bytes | None = None, *, text: str = "") -> bool:
    """Heuristic: invitation / checklist packet without the full RFP body."""
    body = text or ""
    if not body and pdf_bytes:
        try:
            from app.services.pdf_text import extract_pdf_text_from_bytes

            body = extract_pdf_text_from_bytes(pdf_bytes, max_chars=40_000)
        except Exception:  # noqa: BLE001
            body = ""
    if not body and not pdf_bytes:
        return True
    chars = len(body)
    if chars >= 20_000:
        return False
    low = body.casefold()
    wrapper_signals = (
        "bid attachments" in low
        or "requested attachments" in low
        or "page 5 of 5" in low
        or "page 4 of 5" in low
        or ("vendor file upload" in low and chars < 12_000)
        or ("proposal submission requirements" in low and chars < 12_000)
    )
    missing_core = (
        "exhibit 1" not in low
        and "glossary of terms" not in low
        and "scope of work" not in low
    )
    # Full RFP body markers beat the short-packet heuristic even when the
    # extract is still under the char floor (partial downloads / tests).
    if not missing_core:
        return False
    if wrapper_signals:
        return True
    return chars < 10_000


def pdf_anchor_candidates(rows: list[dict[str, str]]) -> list[tuple[str, str]]:
    """Pure helper: filter scraped anchors down to downloadable PDF links."""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for row in rows or []:
        href = str(row.get("href") or "").strip()
        text = str(row.get("text") or "").strip()
        if not href or href.startswith("javascript:"):
            continue
        blob = f"{text} {href}".casefold()
        if not (
            ".pdf" in blob
            or "extract.aspx" in blob
            or "/download" in blob
            or "attachment" in blob
        ):
            continue
        if ".pdf" not in text.casefold() and "extract.aspx" not in href.casefold():
            if ".pdf" not in href.casefold():
                continue
        name = text if ".pdf" in text.casefold() else (
            href.rsplit("/", 1)[-1].split("?", 1)[0] or "attachment.pdf"
        )
        if href in seen:
            continue
        seen.add(href)
        out.append((name, href))
    return out


def _collect_pdf_anchors(page: Any) -> list[tuple[str, str]]:
    """Return (label, absolute_href) for PDF-like anchors on the page."""
    try:
        rows = page.locator("a").evaluate_all(
            """els => els.map(a => ({
              text: (a.textContent || '').trim().slice(0, 200),
              href: a.href || a.getAttribute('href') || ''
            }))"""
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[justwin-sync] portal anchor scrape failed: %s", exc)
        return []
    return pdf_anchor_candidates(
        [r for r in (rows or []) if isinstance(r, dict)]
    )


def fetch_portal_attachment_pdfs(
    page: Any,
    originating_url: str,
    *,
    max_files: int = 12,
) -> list[PortalPdf]:
    """Download Bid Attachment PDFs from a public buyer portal page."""
    url = (originating_url or "").strip()
    if not url.startswith("http"):
        return []

    if should_skip_portal_scrape(url):
        host = urlparse(url).netloc.casefold()
        logger.info("[justwin-sync] skip portal scrape for host %s", host)
        return []

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=PORTAL_GOTO_TIMEOUT_MS)
        page.wait_for_timeout(400)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[justwin-sync] portal goto failed (%s): %s", url, exc)
        return []

    # Cloudflare / login interstitial — give up quietly.
    try:
        title = (page.title() or "").casefold()
        body_head = ((page.inner_text("body") or "")[:400]).casefold()
    except Exception:  # noqa: BLE001
        title, body_head = "", ""
    if "just a moment" in title or "security verification" in body_head:
        logger.info("[justwin-sync] portal blocked by bot challenge: %s", url)
        return []

    anchors = _collect_pdf_anchors(page)
    if not anchors and _is_ionwave_url(url):
        # Retry once after a short wait — ASP.NET grids can hydrate late.
        page.wait_for_timeout(800)
        anchors = _collect_pdf_anchors(page)

    pdfs: list[PortalPdf] = []
    for name, href in anchors[:max_files]:
        abs_url = href if href.startswith("http") else urljoin(url, href)
        try:
            res = page.request.get(abs_url, timeout=PORTAL_PDF_TIMEOUT_MS)
            if not res.ok:
                logger.warning(
                    "[justwin-sync] portal PDF HTTP %s for %s",
                    res.status,
                    name,
                )
                continue
            data = res.body()
            if not data or data[:4] != b"%PDF" or len(data) < 500:
                continue
            pdfs.append(PortalPdf(name=name or "attachment.pdf", data=data))
            logger.info(
                "[justwin-sync] portal attachment %s (%d bytes)",
                name,
                len(data),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[justwin-sync] portal PDF download failed for %s: %s",
                name,
                exc,
            )
    return pdfs
