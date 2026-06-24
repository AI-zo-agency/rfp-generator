"""Read uploaded RFP content from local storage only (not Supermemory)."""

from pathlib import Path

from app.core.config import _BACKEND_ROOT, settings
from app.models.rfp import RfpRecord
from app.services.pdf_text import extract_pdf_text
from app.services.rfp_repository import get_rfp_pdf_path


def resolve_rfp_pdf_path(rfp_id: str, pdf_path: str | None = None) -> Path | None:
    """Find the on-disk PDF for an RFP (handles legacy relative paths in SQLite)."""
    recorded = (pdf_path or get_rfp_pdf_path(rfp_id) or "").strip()
    candidates: list[Path] = [
        settings.pdf_storage_path / rfp_id / "rfp.pdf",
    ]
    if recorded:
        path = Path(recorded)
        dashboard_root = settings.database_path.parent.parent
        candidates.extend(
            [
                path,
                Path.cwd() / path,
                _BACKEND_ROOT / path,
                dashboard_root / path,
            ]
        )

    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        if candidate.is_file():
            return candidate
    return None


def load_local_rfp_text(
    rfp: RfpRecord,
    *,
    max_chars: int = 120_000,
) -> tuple[str, str, bool, bool]:
    """Return description, pdf_text, pdf_exists, pdf_file_missing."""
    description = (rfp.description or "").strip()
    pdf_path_recorded = rfp.pdf_path or get_rfp_pdf_path(rfp.id)
    resolved = resolve_rfp_pdf_path(rfp.id, pdf_path_recorded)
    pdf_exists = resolved is not None
    pdf_text = (
        extract_pdf_text(str(resolved), max_chars=max_chars) if resolved else ""
    )
    pdf_file_missing = bool(pdf_path_recorded and not pdf_exists)
    return description, pdf_text, pdf_exists, pdf_file_missing


def combine_rfp_text(description: str, pdf_text: str, *, max_chars: int = 120_000) -> str:
    parts = [part for part in (description.strip(), pdf_text.strip()) if part]
    return "\n\n".join(parts).strip()[:max_chars]
