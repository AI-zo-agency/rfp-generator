"""Vision OCR fallback for image-only RFP PDFs.

Flow: pypdf text layer first (caller), then cache lookup, then OpenRouter
vision over PyMuPDF page renders. Results are cached next to the PDF.
"""

from __future__ import annotations

import asyncio
import base64
import concurrent.futures
import hashlib
import logging
import re
import time
from typing import Any

from app.core.config import settings
from app.services.pdf_text import IMAGE_ONLY_TEXT_THRESHOLD

logger = logging.getLogger(__name__)

OCR_CACHE_FILENAME = "rfp.ocr.txt"
_HASH_HEADER_RE = re.compile(r"^#\s*sha256=([0-9a-f]{64})\s*$", re.IGNORECASE)
_OCR_PROMPT = (
    "You are OCR for a government RFP PDF scan. Transcribe every page of readable "
    "text in reading order. Preserve headings, numbered lists, and table rows as "
    "plain text (use | between columns when obvious). Do not summarize, translate, "
    "or invent missing text. Output plain text only — no markdown fences."
)


def pdf_content_sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def render_pdf_pages_png(
    content: bytes,
    *,
    max_pages: int | None = None,
    scale: float = 2.0,
) -> list[bytes]:
    """Render PDF pages to PNG bytes via PyMuPDF. Empty list on failure."""
    if not content or not content.startswith(b"%PDF"):
        return []
    try:
        import pymupdf
    except ImportError:
        logger.warning("pymupdf not installed — cannot render pages for OCR")
        return []

    limit = max_pages if max_pages is not None else int(settings.rfp_ocr_max_pages or 20)
    limit = max(1, min(limit, 40))
    images: list[bytes] = []
    try:
        doc = pymupdf.open(stream=content, filetype="pdf")
    except Exception as exc:  # noqa: BLE001
        logger.warning("pdf_ocr open failed: %s", str(exc)[:200])
        return []
    try:
        if getattr(doc, "is_encrypted", False):
            try:
                # Empty password unlocks some restriction-only files; never prompt.
                if doc.authenticate("") == 0:
                    logger.info("pdf_ocr skipped encrypted PDF")
                    return []
            except Exception as exc:  # noqa: BLE001
                logger.info("pdf_ocr encrypted auth failed: %s", str(exc)[:160])
                return []
        matrix = pymupdf.Matrix(scale, scale)
        page_count = doc.page_count
        for i in range(min(page_count, limit)):
            try:
                page = doc.load_page(i)
                pix = page.get_pixmap(matrix=matrix, alpha=False)
                images.append(pix.tobytes("png"))
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "pdf_ocr page render failed page=%s err=%s",
                    i + 1,
                    str(exc)[:160],
                )
    except Exception as exc:  # noqa: BLE001 — encrypted / corrupt mid-render
        logger.warning("pdf_ocr render failed: %s", str(exc)[:200])
        return []
    finally:
        try:
            doc.close()
        except Exception:  # noqa: BLE001
            pass
    return images


def _encode_data_url(png: bytes) -> str:
    b64 = base64.standard_b64encode(png).decode("ascii")
    return f"data:image/png;base64,{b64}"


def format_ocr_cache_blob(sha256: str, text: str) -> bytes:
    body = (text or "").strip()
    return f"# sha256={sha256}\n{body}\n".encode("utf-8")


def parse_ocr_cache_blob(raw: bytes, *, expected_sha256: str) -> str | None:
    if not raw:
        return None
    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    lines = decoded.splitlines()
    if not lines:
        return None
    match = _HASH_HEADER_RE.match(lines[0].strip())
    if not match or match.group(1).lower() != expected_sha256.lower():
        return None
    text = "\n".join(lines[1:]).strip()
    return text or None


def ocr_cache_object_key(rfp_id: str) -> str:
    return f"{rfp_id}/{OCR_CACHE_FILENAME}"


def load_cached_ocr_text(rfp_id: str, pdf_sha256: str) -> str | None:
    """Return cached OCR text when hash matches, else None."""
    from app.services import rfp_storage

    raw = rfp_storage.load_rfp_ocr_cache_bytes(rfp_id)
    if not raw:
        return None
    return parse_ocr_cache_blob(raw, expected_sha256=pdf_sha256)


def save_ocr_cache_text(rfp_id: str, pdf_sha256: str, text: str) -> None:
    from app.services import rfp_storage

    rfp_storage.save_rfp_ocr_cache_bytes(
        rfp_id, format_ocr_cache_blob(pdf_sha256, text)
    )


def delete_ocr_cache(rfp_id: str) -> None:
    from app.services import rfp_storage

    rfp_storage.delete_rfp_ocr_cache(rfp_id)


async def ocr_pdf_pages_via_vision(
    page_pngs: list[bytes],
    *,
    rfp_id: str | None = None,
    max_chars: int = 120_000,
) -> str:
    """Transcribe page images with OpenRouter vision. Returns plain text."""
    if not page_pngs:
        return ""
    from app.services.llm import LlmError, chat_text_vision

    model = (settings.openrouter_model_ocr or "~google/gemini-flash-latest").strip()
    content_parts: list[dict[str, Any]] = [{"type": "text", "text": _OCR_PROMPT}]
    for i, png in enumerate(page_pngs):
        content_parts.append(
            {"type": "text", "text": f"\n--- Page {i + 1} of {len(page_pngs)} ---\n"}
        )
        content_parts.append(
            {
                "type": "image_url",
                "image_url": {"url": _encode_data_url(png)},
            }
        )
    messages = [{"role": "user", "content": content_parts}]
    # ~800 tokens per page of dense scan text; floor for short RFPs.
    max_tokens = min(16_000, max(2_048, 900 * len(page_pngs)))
    started = time.perf_counter()
    try:
        raw, provider = await chat_text_vision(
            messages,
            model=model,
            max_tokens=max_tokens,
            temperature=0.1,
            node_name="rfp_pdf_ocr",
            rfp_id=rfp_id,
        )
    except LlmError as exc:
        logger.warning(
            "pdf_ocr vision failed rfp_id=%s model=%s err=%s",
            rfp_id,
            model,
            str(exc)[:240],
        )
        return ""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:\w+)?\s*", "", text)
        text = re.sub(r"\s*```\s*$", "", text).strip()
    if len(text) > max_chars:
        text = text[:max_chars]
    logger.info(
        "pdf_ocr vision done rfp_id=%s provider=%s model=%s pages=%s chars=%s duration_ms=%s",
        rfp_id,
        provider,
        model,
        len(page_pngs),
        len(text),
        int((time.perf_counter() - started) * 1000),
    )
    return text


def _run_coro_sync(factory: Any, *, timeout_s: float = 300.0) -> Any:
    """Run an async zero-arg factory from sync code (celery / FastAPI threads)."""

    async def _inner() -> Any:
        return await factory()

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_inner())
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, _inner()).result(timeout=timeout_s)


def extract_text_via_ocr(
    content: bytes,
    *,
    rfp_id: str,
    max_chars: int = 120_000,
) -> tuple[str, str]:
    """OCR an image-only PDF. Returns (text, source) where source is ocr|cache|"".

    source "" means OCR disabled, failed, or yielded too little text.
    """
    if not content or not getattr(settings, "rfp_ocr_enabled", True):
        return "", ""

    sha = pdf_content_sha256(content)
    cached = load_cached_ocr_text(rfp_id, sha)
    if cached is not None and len(cached.strip()) >= IMAGE_ONLY_TEXT_THRESHOLD:
        logger.info(
            "pdf_ocr cache hit rfp_id=%s chars=%s sha256=%s",
            rfp_id,
            len(cached),
            sha[:12],
        )
        return cached[:max_chars], "cache"

    started = time.perf_counter()
    pages = render_pdf_pages_png(content, max_pages=int(settings.rfp_ocr_max_pages or 20))
    if not pages:
        logger.warning(
            "pdf_ocr no pages rendered rfp_id=%s duration_ms=%s",
            rfp_id,
            int((time.perf_counter() - started) * 1000),
        )
        return "", ""

    text = _run_coro_sync(
        lambda: ocr_pdf_pages_via_vision(
            pages, rfp_id=rfp_id, max_chars=max_chars
        )
    )
    text = (text or "").strip()
    if len(text) < IMAGE_ONLY_TEXT_THRESHOLD:
        logger.warning(
            "pdf_ocr insufficient text rfp_id=%s chars=%s pages=%s duration_ms=%s",
            rfp_id,
            len(text),
            len(pages),
            int((time.perf_counter() - started) * 1000),
        )
        return "", ""

    try:
        save_ocr_cache_text(rfp_id, sha, text)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "pdf_ocr cache write failed rfp_id=%s err=%s",
            rfp_id,
            str(exc)[:200],
        )

    logger.info(
        "pdf_ocr complete rfp_id=%s source=ocr pages=%s chars=%s duration_ms=%s",
        rfp_id,
        len(pages),
        len(text),
        int((time.perf_counter() - started) * 1000),
    )
    return text[:max_chars], "ocr"
