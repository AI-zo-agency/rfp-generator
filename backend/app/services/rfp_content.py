"""Read uploaded RFP content from Supabase Storage or local disk (not Supermemory)."""

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


def resolve_rfp_pdf_path(rfp_id: str, pdf_path: str | None = None) -> Path | None:
    """Local filesystem path only — None when PDF is in Supabase."""
    recorded = (pdf_path or get_rfp_pdf_path(rfp_id) or "").strip()
    if is_supabase_path(recorded):
        return None
    return resolve_local_pdf_path(rfp_id, recorded or None)


def load_local_rfp_text(
    rfp: RfpRecord,
    *,
    max_chars: int = 120_000,
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

    if not pdf_text and pdf_path_recorded and not is_supabase_path(pdf_path_recorded):
        resolved = resolve_rfp_pdf_path(rfp.id, pdf_path_recorded)
        if resolved:
            pdf_text = extract_pdf_text(str(resolved), max_chars=max_chars)
            pdf_exists = True
            if not pdf_bytes and resolved.is_file():
                pdf_bytes = resolved.read_bytes()

    pdf_file_missing = bool(pdf_path_recorded and not pdf_exists)
    page_count = pdf_page_count(pdf_bytes) if pdf_bytes else 0
    image_only = bool(pdf_bytes and is_image_only_pdf(pdf_bytes, extracted_text=pdf_text))
    return description, pdf_text, pdf_exists, pdf_file_missing, page_count, image_only


def combine_rfp_text(description: str, pdf_text: str, *, max_chars: int = 120_000) -> str:
    parts = [part for part in (description.strip(), pdf_text.strip()) if part]
    return "\n\n".join(parts).strip()[:max_chars]
