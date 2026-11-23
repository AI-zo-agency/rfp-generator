"""Read uploaded RFP content from Supabase Storage or local disk (not Supermemory)."""

from __future__ import annotations

import logging
from pathlib import Path

from app.models.rfp import RfpRecord
from app.services.pdf_text import (
    extract_pdf_text,
    extract_pdf_text_from_bytes,
    is_image_only_pdf,
    pdf_page_count,
)
from app.services.rfp_repository import get_rfp_pdf_path
from app.services.rfp_storage import is_supabase_path, load_rfp_pdf_bytes, resolve_local_pdf_path

logger = logging.getLogger(__name__)


def assemble_packet_pdf_text(
    primary: str,
    supplements: list[tuple[str, str, str]],
    *,
    max_chars: int,
) -> str:
    """Join primary solicitation text with labeled cite-linked packet files.

    Truncation prefers keeping labeled supplements intact: shrink primary
    first; only clip supplements when they alone exceed max_chars.
    """
    labeled_blocks: list[str] = []
    for cite_target, filename, text in supplements:
        body = (text or "").strip()
        if not body:
            continue
        cite = (cite_target or "exhibit").strip() or "exhibit"
        name = (filename or "packet.pdf").strip() or "packet.pdf"
        labeled_blocks.append(f"--- PACKET FILE: {cite} ({name}) ---\n{body}")

    supplements_blob = "\n\n".join(labeled_blocks)
    primary_text = (primary or "").strip()
    if max_chars <= 0:
        return ""
    if not supplements_blob:
        return primary_text[:max_chars]
    if not primary_text:
        return supplements_blob[:max_chars]

    sep = "\n\n"
    room_for_primary = max_chars - len(supplements_blob) - len(sep)
    if room_for_primary <= 0:
        return supplements_blob[:max_chars]
    return primary_text[:room_for_primary] + sep + supplements_blob


def _load_packet_supplement_texts(
    rfp_id: str,
    *,
    max_chars: int,
) -> list[tuple[str, str, str]]:
    """Return (cite_target, filename, extracted_text) for each packet file."""
    try:
        from app.services.rfp_packet_files import list_packet_files
    except Exception as exc:  # noqa: BLE001
        logger.warning("packet file list import failed rfp_id=%s err=%s", rfp_id, exc)
        return []

    try:
        files = list_packet_files(rfp_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("packet file list failed rfp_id=%s err=%s", rfp_id, exc)
        return []

    out: list[tuple[str, str, str]] = []
    # Per-file budget: leave room for primary + labels; still extract generously.
    per_file_cap = max(8_000, max_chars // max(1, len(files) or 1))
    for item in files:
        raw = load_rfp_pdf_bytes(rfp_id, item.storage_path)
        if not raw:
            continue
        text = extract_pdf_text_from_bytes(raw, max_chars=per_file_cap)
        if not text.strip() and is_image_only_pdf(raw, extracted_text=text):
            try:
                from app.services.pdf_ocr import extract_text_via_ocr

                ocr_text, _src = extract_text_via_ocr(
                    raw, rfp_id=rfp_id, max_chars=per_file_cap
                )
                text = ocr_text or text
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "packet OCR failed rfp_id=%s file_id=%s err=%s",
                    rfp_id,
                    item.id,
                    str(exc)[:200],
                )
        out.append((item.cite_target, item.filename, text))
    return out


def resolve_rfp_pdf_path(rfp_id: str, pdf_path: str | None = None) -> Path | None:
    """Local filesystem path only — None when PDF is in Supabase."""
    recorded = (pdf_path or get_rfp_pdf_path(rfp_id) or "").strip()
    if is_supabase_path(recorded):
        return None
    return resolve_local_pdf_path(rfp_id, recorded or None)


def load_local_rfp_text(
    rfp: RfpRecord,
    *,
    max_chars: int = 400_000,
) -> tuple[str, str, bool, bool, int, bool]:
    """Return description, pdf_text, pdf_exists, pdf_file_missing, page_count, image_only."""
    description = (rfp.description or "").strip()
    pdf_path_recorded = rfp.pdf_path or get_rfp_pdf_path(rfp.id)

    pdf_bytes = load_rfp_pdf_bytes(rfp.id, pdf_path_recorded)
    pdf_exists = pdf_bytes is not None
    pdf_text = (
        extract_pdf_text_from_bytes(pdf_bytes, max_chars=max_chars)
        if pdf_bytes
        else ""
    )
    pdf_text_source = "pypdf" if pdf_text.strip() else ""

    if not pdf_text and pdf_path_recorded and not is_supabase_path(pdf_path_recorded):
        resolved = resolve_rfp_pdf_path(rfp.id, pdf_path_recorded)
        if resolved:
            pdf_text = extract_pdf_text(str(resolved), max_chars=max_chars)
            pdf_exists = True
            if not pdf_bytes and resolved.is_file():
                pdf_bytes = resolved.read_bytes()
            if pdf_text.strip():
                pdf_text_source = "pypdf"

    pdf_file_missing = bool(pdf_path_recorded and not pdf_exists)
    page_count = pdf_page_count(pdf_bytes) if pdf_bytes else 0
    image_only = bool(pdf_bytes and is_image_only_pdf(pdf_bytes, extracted_text=pdf_text))

    if image_only and pdf_bytes and page_count > 0:
        try:
            from app.services.pdf_ocr import extract_text_via_ocr

            ocr_text, ocr_source = extract_text_via_ocr(
                pdf_bytes, rfp_id=rfp.id, max_chars=max_chars
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "pdf_ocr fallback failed rfp_id=%s err=%s",
                rfp.id,
                str(exc)[:240],
            )
            ocr_text, ocr_source = "", ""
        if ocr_text.strip():
            pdf_text = ocr_text.strip()[:max_chars]
            pdf_text_source = ocr_source or "ocr"
            image_only = is_image_only_pdf(pdf_bytes, extracted_text=pdf_text)

    supplements = _load_packet_supplement_texts(rfp.id, max_chars=max_chars)
    if supplements:
        before = len(pdf_text or "")
        pdf_text = assemble_packet_pdf_text(
            pdf_text or "", supplements, max_chars=max_chars
        )
        logger.info(
            "rfp_packet_text_merged rfp_id=%s supplements=%s primary_chars=%s "
            "merged_chars=%s",
            rfp.id,
            len(supplements),
            before,
            len(pdf_text or ""),
        )

    logger.info(
        "rfp_text_load rfp_id=%s pdf_exists=%s pages=%s chars=%s image_only=%s "
        "pdf_text_source=%s",
        rfp.id,
        pdf_exists,
        page_count,
        len(pdf_text or ""),
        image_only,
        pdf_text_source or "none",
    )
    return description, pdf_text, pdf_exists, pdf_file_missing, page_count, image_only


def combine_rfp_text(description: str, pdf_text: str, *, max_chars: int = 120_000) -> str:
    parts = [part for part in (description.strip(), pdf_text.strip()) if part]
    return "\n\n".join(parts).strip()[:max_chars]
