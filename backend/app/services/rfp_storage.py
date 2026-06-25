"""RFP PDF storage — Supabase bucket when configured, else local disk (legacy)."""

from __future__ import annotations

import shutil
from pathlib import Path

from app.core.config import settings
from app.services import supabase_storage

SUPABASE_PATH_PREFIX = "supabase:"


def rfp_object_key(rfp_id: str) -> str:
    return f"{rfp_id}/rfp.pdf"


def is_supabase_path(pdf_path: str | None) -> bool:
    return bool(pdf_path and pdf_path.startswith(SUPABASE_PATH_PREFIX))


def supabase_object_key_from_path(pdf_path: str) -> str:
    return pdf_path.removeprefix(SUPABASE_PATH_PREFIX)


def to_supabase_path(object_key: str) -> str:
    return f"{SUPABASE_PATH_PREFIX}{object_key}"


def resolve_pdf_view_url(rfp_id: str, pdf_path: str | None, *, sign: bool = False) -> str | None:
    """Signed Supabase URL only when sign=True (single PDF view). Lists omit pdfUrl."""
    if not pdf_path:
        return None
    if sign and is_supabase_path(pdf_path) and use_supabase():
        try:
            return supabase_storage.create_signed_url(
                supabase_object_key_from_path(pdf_path),
                expires_in=3600,
            )
        except supabase_storage.SupabaseStorageError:
            return None
    return None


def use_supabase() -> bool:
    return supabase_storage.is_configured()


def save_rfp_pdf(rfp_id: str, content: bytes) -> str:
    if len(content) < 500 or not content.startswith(b"%PDF"):
        raise ValueError("Uploaded file is not a valid PDF")

    if use_supabase():
        key = rfp_object_key(rfp_id)
        supabase_storage.upload_pdf(object_key=key, content=content)
        return to_supabase_path(key)

    pdf_dir = settings.pdf_storage_path / rfp_id
    pdf_dir.mkdir(parents=True, exist_ok=True)
    target = pdf_dir / "rfp.pdf"
    target.write_bytes(content)
    return str(target)


def load_rfp_pdf_bytes(rfp_id: str, pdf_path: str | None) -> bytes | None:
    if pdf_path and is_supabase_path(pdf_path):
        if not use_supabase():
            return None
        try:
            return supabase_storage.download_pdf(supabase_object_key_from_path(pdf_path))
        except supabase_storage.SupabaseStorageError:
            return None

    resolved = resolve_local_pdf_path(rfp_id, pdf_path)
    if resolved and resolved.is_file():
        return resolved.read_bytes()
    return None


def delete_rfp_pdf(rfp_id: str, pdf_path: str | None) -> None:
    if pdf_path and is_supabase_path(pdf_path):
        if use_supabase():
            supabase_storage.delete_pdf(supabase_object_key_from_path(pdf_path))
        return

    pdf_root = settings.pdf_storage_path / rfp_id
    if pdf_root.is_dir():
        shutil.rmtree(pdf_root, ignore_errors=True)

    if not pdf_path or is_supabase_path(pdf_path):
        return

    recorded = Path(pdf_path)
    if not recorded.is_absolute():
        recorded = (settings.database_path.parent.parent / recorded).resolve()
    if recorded.is_file():
        recorded.unlink(missing_ok=True)
    if recorded.parent.is_dir() and recorded.parent.name == rfp_id:
        shutil.rmtree(recorded.parent, ignore_errors=True)


def resolve_local_pdf_path(rfp_id: str, pdf_path: str | None) -> Path | None:
    from app.core.config import _BACKEND_ROOT

    candidates: list[Path] = [settings.pdf_storage_path / rfp_id / "rfp.pdf"]
    if pdf_path and not is_supabase_path(pdf_path):
        path = Path(pdf_path)
        dashboard_root = settings.database_path.parent.parent
        candidates.extend(
            [path, Path.cwd() / path, _BACKEND_ROOT / path, dashboard_root / path]
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
