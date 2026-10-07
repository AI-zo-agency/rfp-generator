"""Vision OCR fallback for image-only RFP PDFs.

Flow: pypdf text layer first (caller), then cache lookup, then OpenRouter
Gemini with the full PDF as a native file input (no page-to-image split,
no page cap). Results are cached next to the PDF.
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
_NATIVE_PDF_PLUGIN: list[dict[str, Any]] = [
    {"id": "file-parser", "pdf": {"engine": "native"}},
]
# Large municipal RFPs (50+ pages) need headroom; OpenRouter Gemini allows high caps.
_OCR_MAX_TOKENS = 65_536
_OCR_SYNC_TIMEOUT_S = 900.0


def pdf_content_sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _encode_pdf_data_url(content: bytes) -> str:
    b64 = base64.standard_b64encode(content).decode("ascii")
    return f"data:application/pdf;base64,{b64}"


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


async def ocr_pdf_via_vision(
    content: bytes,
    *,
    rfp_id: str | None = None,
    max_chars: int = 120_000,
    filename: str = "rfp.pdf",
) -> str:
    """Transcribe a full PDF with OpenRouter Gemini native file input."""
    if not content or not content.startswith(b"%PDF"):
        return ""
    from app.services.llm import LlmError, chat_text_vision

    model = (settings.openrouter_model_ocr or "~google/gemini-flash-latest").strip()
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": _OCR_PROMPT},
                {
                    "type": "file",
                    "file": {
                        "filename": filename or "rfp.pdf",
                        "file_data": _encode_pdf_data_url(content),
                    },
                },
            ],
        }
    ]
    started = time.perf_counter()
    try:
        raw, provider = await chat_text_vision(
            messages,
            model=model,
            max_tokens=_OCR_MAX_TOKENS,
            temperature=0.1,
            node_name="rfp_pdf_ocr",
            rfp_id=rfp_id,
            plugins=_NATIVE_PDF_PLUGIN,
        )
    except LlmError as exc:
        logger.warning(
            "pdf_ocr vision failed rfp_id=%s model=%s err=%s pdf_bytes=%s",
            rfp_id,
            model,
            str(exc)[:240],
            len(content),
        )
        return ""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:\w+)?\s*", "", text)
        text = re.sub(r"\s*```\s*$", "", text).strip()
    if len(text) > max_chars:
        text = text[:max_chars]
    logger.info(
        "pdf_ocr vision done rfp_id=%s provider=%s model=%s pdf_bytes=%s chars=%s "
        "duration_ms=%s",
        rfp_id,
        provider,
        model,
        len(content),
        len(text),
        int((time.perf_counter() - started) * 1000),
    )
    return text


# Back-compat alias for older imports/tests.
async def ocr_pdf_pages_via_vision(
    _page_pngs: list[bytes] | None = None,
    *,
    rfp_id: str | None = None,
    max_chars: int = 120_000,
    pdf_bytes: bytes | None = None,
) -> str:
    """Deprecated: use ocr_pdf_via_vision(pdf_bytes)."""
    if pdf_bytes:
        return await ocr_pdf_via_vision(
            pdf_bytes, rfp_id=rfp_id, max_chars=max_chars
        )
    return ""


def _run_coro_sync(factory: Any, *, timeout_s: float = _OCR_SYNC_TIMEOUT_S) -> Any:
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
    if not content or not content.startswith(b"%PDF"):
        return "", ""
    if not getattr(settings, "rfp_ocr_enabled", True):
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
    text = _run_coro_sync(
        lambda: ocr_pdf_via_vision(
            content, rfp_id=rfp_id, max_chars=max_chars, filename=f"{rfp_id}.pdf"
        )
    )
    text = (text or "").strip()
    if len(text) < IMAGE_ONLY_TEXT_THRESHOLD:
        logger.warning(
            "pdf_ocr insufficient text rfp_id=%s chars=%s pdf_bytes=%s duration_ms=%s",
            rfp_id,
            len(text),
            len(content),
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
        "pdf_ocr complete rfp_id=%s source=ocr pdf_bytes=%s chars=%s duration_ms=%s",
        rfp_id,
        len(content),
        len(text),
        int((time.perf_counter() - started) * 1000),
    )
    return text[:max_chars], "ocr"
