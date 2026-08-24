import logging
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from app.core.config import settings
from app.models.knowledge_base import (
    ConnectDriveResponse,
    KnowledgeBaseDocument,
    KnowledgeBaseDocumentsResponse,
    KnowledgeBaseFoldersResponse,
    KnowledgeBaseStatus,
)
from app.services.knowledge_base_document_types import (
    document_type_options,
    is_valid_category,
)
from app.services import google_drive, supermemory
from app.services import kb_corrections
from app.services import knowledge_base_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/knowledge-base", tags=["knowledge-base"])

ALLOWED_EXTENSIONS = {
    ".pdf",
    ".doc",
    ".docx",
    ".md",
    ".txt",
    ".xls",
    ".xlsx",
}


def _validate_upload_file(filename: str, size: int) -> None:
    extension = Path(filename).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail="Upload PDF, Word, Excel, Markdown, or plain text files.",
        )
    if size > 25 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File must be 25 MB or smaller.")


def _require_supermemory() -> None:
    if not supermemory.is_configured():
        raise HTTPException(
            status_code=503,
            detail="SUPERMEMORY_API_KEY is required. Documents are stored in Supermemory only.",
        )


class CorrectionRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    title: str = Field(default="", max_length=200)
    note: str = Field(..., max_length=2000)


async def _create_upload_note(
    *,
    notes: str,
    title: str,
    linked_document_id: str | None,
) -> str | None:
    """Create the correction filed with an upload. Returns an error string, never raises."""
    if not notes.strip():
        return None
    try:
        await kb_corrections.create_correction(
            title=f"Note on {title}",
            note=notes,
            linked_document_id=linked_document_id,
        )
    except (supermemory.SupermemoryError, ValueError) as exc:
        logger.warning("upload note failed for %s: %s", title, exc)
        return str(exc)
    return None


@router.get("/corrections")
async def list_knowledge_base_corrections() -> dict[str, object]:
    _require_supermemory()
    try:
        corrections = await kb_corrections.list_corrections()
    except supermemory.SupermemoryError as exc:
        raise HTTPException(status_code=exc.status_code or 502, detail=str(exc)) from exc
    return {"corrections": corrections}


@router.post("/corrections", status_code=201)
async def create_knowledge_base_correction(payload: CorrectionRequest) -> dict[str, object]:
    _require_supermemory()
    try:
        return await kb_corrections.create_correction(
            title=payload.title, note=payload.note
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except supermemory.SupermemoryError as exc:
        raise HTTPException(status_code=exc.status_code or 502, detail=str(exc)) from exc


@router.patch("/corrections/{custom_id:path}")
async def update_knowledge_base_correction(
    custom_id: str, payload: CorrectionRequest
) -> dict[str, object]:
    _require_supermemory()
    try:
        return await kb_corrections.update_correction(
            custom_id=custom_id, title=payload.title, note=payload.note
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except supermemory.SupermemoryError as exc:
        raise HTTPException(status_code=exc.status_code or 502, detail=str(exc)) from exc


@router.delete("/corrections/{custom_id:path}")
async def delete_knowledge_base_correction(
    custom_id: str, document_id: str = ""
) -> dict[str, object]:
    _require_supermemory()
    try:
        await kb_corrections.delete_correction(
            custom_id=custom_id, document_id=document_id
        )
    except supermemory.SupermemoryError as exc:
        raise HTTPException(status_code=exc.status_code or 502, detail=str(exc)) from exc
    return {"deleted": custom_id}


@router.get("/status", response_model=KnowledgeBaseStatus)
async def knowledge_base_status() -> KnowledgeBaseStatus:
    sm_configured = supermemory.is_configured()
    gdrive_configured = google_drive.is_configured()
    drive_connected = False
    shared_drive_id: str | None = None
    folder_count = 0

    if sm_configured:
        try:
            drive_connected = await supermemory.has_google_drive_connection()
        except supermemory.SupermemoryError:
            drive_connected = False

    if gdrive_configured:
        try:
            shared_drive_id = google_drive.find_shared_drive_id()
            if shared_drive_id:
                _, folders = google_drive.list_folders_in_shared_drive(shared_drive_id)
                folder_count = len(folders)
        except (google_drive.GoogleDriveError, Exception):
            shared_drive_id = None
            folder_count = 0

    return KnowledgeBaseStatus(
        supermemory_configured=sm_configured,
        google_drive_configured=gdrive_configured,
        drive_connected=drive_connected,
        shared_drive_name=settings.google_drive_shared_drive_name,
        shared_drive_id=shared_drive_id,
        folder_count=folder_count,
        container_tag=settings.resolved_container_tag,
    )


@router.get("/document-types")
def get_document_types() -> dict[str, object]:
    return {
        "containerTag": settings.resolved_container_tag,
        "types": document_type_options(),
    }


@router.get("/documents", response_model=KnowledgeBaseDocumentsResponse)
async def get_knowledge_base_documents() -> KnowledgeBaseDocumentsResponse:
    _require_supermemory()
    try:
        raw_documents = await knowledge_base_service.list_documents()
    except supermemory.SupermemoryError as exc:
        raise HTTPException(
            status_code=exc.status_code or 502,
            detail=str(exc),
        ) from exc

    documents = [KnowledgeBaseDocument.model_validate(doc) for doc in raw_documents]
    return KnowledgeBaseDocumentsResponse(
        documents=documents,
        container_tag=settings.resolved_container_tag,
    )


@router.post("/documents", status_code=201)
async def upload_knowledge_base_document(
    title: str = Form(...),
    category: str = Form(...),
    notes: str = Form(""),
    file: UploadFile = File(...),
) -> dict[str, object]:
    _require_supermemory()

    clean_title = title.strip()
    clean_category = category.strip()

    if not clean_title:
        raise HTTPException(status_code=400, detail="Title is required.")
    if not is_valid_category(clean_category):
        raise HTTPException(status_code=400, detail="Select a valid document type.")

    if not file.filename:
        raise HTTPException(status_code=400, detail="A document file is required.")

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="A document file is required.")

    _validate_upload_file(file.filename, len(content))

    try:
        doc = await knowledge_base_service.upload_document(
            title=clean_title,
            category=clean_category,
            file_name=file.filename,
            file_bytes=content,
        )
    except supermemory.SupermemoryError as exc:
        raise HTTPException(
            status_code=exc.status_code or 502,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    note_error = await _create_upload_note(
        notes=notes,
        title=clean_title,
        linked_document_id=str(doc.get("supermemoryCustomId") or ""),
    )
    payload = KnowledgeBaseDocument.model_validate(doc).model_dump(by_alias=True)
    if note_error:
        payload["noteError"] = note_error
    return payload


@router.get("/folders", response_model=KnowledgeBaseFoldersResponse)
async def list_rfp_drive_folders() -> KnowledgeBaseFoldersResponse:
    drive_name = settings.google_drive_shared_drive_name

    if google_drive.is_configured():
        try:
            drive_id, folders = google_drive.list_folders_in_shared_drive()
            return KnowledgeBaseFoldersResponse(
                shared_drive_name=drive_name,
                shared_drive_id=drive_id,
                folders=folders,
                source="google-drive-api",
            )
        except google_drive.GoogleDriveError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    if not supermemory.is_configured():
        raise HTTPException(
            status_code=503,
            detail="Supermemory is not configured.",
        )

    try:
        documents = await supermemory.list_google_drive_documents()
        folders = google_drive.folders_from_supermemory_documents(documents)
        return KnowledgeBaseFoldersResponse(
            shared_drive_name=drive_name,
            shared_drive_id=None,
            folders=folders,
            source="supermemory-documents",
        )
    except supermemory.SupermemoryError as exc:
        raise HTTPException(
            status_code=exc.status_code or 502,
            detail=str(exc),
        ) from exc


@router.post("/connect/google-drive", response_model=ConnectDriveResponse)
async def connect_google_drive() -> ConnectDriveResponse:
    try:
        result = await supermemory.create_google_drive_connection()
    except supermemory.SupermemoryError as exc:
        raise HTTPException(
            status_code=exc.status_code or 502,
            detail=str(exc),
        ) from exc

    auth_link = result.get("authLink") or result.get("auth_link")
    if not auth_link:
        raise HTTPException(
            status_code=502,
            detail="Supermemory did not return an auth link",
        )

    return ConnectDriveResponse(
        auth_link=auth_link,
        expires_in=result.get("expiresIn") or result.get("expires_in"),
    )


@router.post("/sync/google-drive")
async def sync_google_drive() -> dict[str, str]:
    try:
        await supermemory.trigger_google_drive_sync()
    except supermemory.SupermemoryError as exc:
        raise HTTPException(
            status_code=exc.status_code or 502,
            detail=str(exc),
        ) from exc
    return {"ok": "true", "message": "Google Drive sync started"}


@router.get("/key-personas")
async def get_key_personas() -> dict[str, object]:
    from app.services import team_personas_service

    personas = await team_personas_service.get_all_key_personas()
    return {
        "total": len(personas),
        "personas": personas,
    }


class RetiredStaffRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    person_id: str = Field(..., alias="personId")
    name: str = ""
    retired: bool = True


@router.patch("/key-personas/retired")
async def set_key_persona_retired(payload: RetiredStaffRequest) -> dict[str, object]:
    """Agency-wide: mark a team member retired so Go/No-Go and proposal agents skip them."""
    from app.services import team_personas_service
    from app.services.retired_staff_store import set_retired

    name = (payload.name or "").strip() or payload.person_id
    retired_rows = set_retired(
        person_id=payload.person_id,
        name=name,
        retired=payload.retired,
    )
    personas = await team_personas_service.get_all_key_personas()
    return {
        "ok": True,
        "retired": retired_rows,
        "personas": personas,
        "total": len(personas),
    }

