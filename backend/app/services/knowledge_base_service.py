import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from app.services.knowledge_base_document_types import category_title, is_valid_category
from app.services import kb_corrections, supermemory

logger = logging.getLogger(__name__)


def _memory_metadata(memory: dict[str, Any]) -> dict[str, Any]:
    raw = memory.get("metadata")
    return raw if isinstance(raw, dict) else {}


def _is_knowledge_base_memory(memory: dict[str, Any]) -> bool:
    metadata = _memory_metadata(memory)
    if metadata.get("type") == "knowledge_base":
        return True
    custom_id = str(memory.get("customId") or "")
    return custom_id.startswith("kb:")


def memory_to_document(memory: dict[str, Any]) -> dict[str, object]:
    metadata = _memory_metadata(memory)
    category = str(metadata.get("category") or "reference")
    file_name = str(
        metadata.get("fileName")
        or memory.get("filepath")
        or memory.get("title")
        or "document"
    )
    title = str(metadata.get("title") or memory.get("title") or file_name)
    status = str(memory.get("status") or "")
    created_at = str(memory.get("createdAt") or "")
    custom_id = memory.get("customId")

    return {
        "id": str(memory.get("id") or ""),
        "title": title,
        "category": category,
        "categoryTitle": str(metadata.get("categoryTitle") or category_title(category)),
        "fileName": file_name,
        "mimeType": "application/octet-stream",
        "fileSize": 0,
        "uploadedAt": created_at,
        "supermemoryCustomId": str(custom_id) if custom_id else None,
        "supermemorySyncedAt": created_at if status else None,
        "supermemoryError": None if status not in {"failed", "error"} else status,
        "supermemoryStatus": status or None,
        "supermemoryUrl": memory.get("url"),
    }


async def list_documents() -> list[dict[str, object]]:
    memories = await supermemory.list_container_memories()
    kb_memories = [memory for memory in memories if _is_knowledge_base_memory(memory)]
    documents = [memory_to_document(memory) for memory in kb_memories]
    return [doc for doc in documents if doc["id"]]


def _matches_kb_document(
    memory: dict[str, Any],
    *,
    document_id: str,
    custom_id: str | None,
) -> bool:
    mem_id = str(memory.get("id") or "")
    mem_custom = str(memory.get("customId") or "")
    if custom_id and mem_custom == custom_id:
        return True
    if document_id and mem_id == document_id:
        return True
    if document_id and mem_custom == document_id:
        return True
    return False


async def find_kb_document(
    *,
    document_id: str,
    custom_id: str | None = None,
) -> dict[str, Any] | None:
    clean_id = document_id.strip()
    clean_custom = (custom_id or "").strip() or None
    if not clean_id and not clean_custom:
        return None

    memories = await supermemory.list_all_container_documents(force_refresh=True)
    for memory in memories:
        if not _is_knowledge_base_memory(memory):
            continue
        if _matches_kb_document(memory, document_id=clean_id, custom_id=clean_custom):
            return memory
    return None


async def _delete_linked_corrections(linked_ids: set[str]) -> None:
    if not linked_ids:
        return
    try:
        corrections = await kb_corrections.list_corrections()
    except supermemory.SupermemoryError as exc:
        logger.warning("could not list corrections while deleting KB doc: %s", exc)
        return

    for correction in corrections:
        linked = str(correction.get("linkedDocumentId") or "")
        if not linked or linked not in linked_ids:
            continue
        try:
            await kb_corrections.delete_correction(
                custom_id=str(correction["customId"]),
                document_id=str(correction["id"]),
            )
        except supermemory.SupermemoryError as exc:
            logger.warning(
                "could not delete linked correction %s: %s",
                correction.get("customId"),
                exc,
            )


async def delete_document(
    *,
    document_id: str,
    custom_id: str | None = None,
) -> None:
    """Hard-delete a KB upload from Supermemory (and any upload notes linked to it)."""
    memory = await find_kb_document(document_id=document_id, custom_id=custom_id)
    if memory is None:
        raise LookupError("Knowledge base document not found")

    delete_keys: list[str] = []
    mem_custom = str(memory.get("customId") or "").strip()
    mem_id = str(memory.get("id") or "").strip()
    if mem_custom:
        delete_keys.append(mem_custom)
    if mem_id and mem_id not in delete_keys:
        delete_keys.append(mem_id)

    deleted = False
    for key in delete_keys:
        if await supermemory.delete_document(key):
            deleted = True
            break

    if not deleted:
        raise supermemory.SupermemoryError(
            "Supermemory could not delete this document",
            status_code=502,
        )

    await _delete_linked_corrections(set(delete_keys))


async def resolve_open_url(
    *,
    document_id: str,
    custom_id: str | None = None,
) -> str:
    """Best URL to open/download a KB document (list url, file-url API, or Drive link)."""
    memory = await find_kb_document(document_id=document_id, custom_id=custom_id)
    if memory is None:
        raise LookupError("Knowledge base document not found")

    direct = str(memory.get("url") or "").strip()
    if direct:
        return direct

    metadata = _memory_metadata(memory)
    for key in ("webViewLink", "driveUrl", "sourceUrl"):
        drive_link = str(metadata.get(key) or "").strip()
        if drive_link:
            return drive_link

    keys: list[str] = []
    mem_custom = str(memory.get("customId") or "").strip()
    mem_id = str(memory.get("id") or "").strip()
    if mem_custom:
        keys.append(mem_custom)
    if mem_id and mem_id not in keys:
        keys.append(mem_id)

    for key in keys:
        try:
            file_url = await supermemory.get_document_file_url(document_key=key)
        except supermemory.SupermemoryError:
            continue
        if file_url:
            return file_url

    raise LookupError("This document has no downloadable file URL in Supermemory")


async def upload_document(
    *,
    title: str,
    category: str,
    file_name: str,
    file_bytes: bytes,
) -> dict[str, object]:
    if not is_valid_category(category):
        raise ValueError("Invalid document type")

    local_ref = str(uuid.uuid4())
    category_label = category_title(category)

    result = await supermemory.ingest_knowledge_base_file(
        document_id=local_ref,
        title=title,
        category=category,
        category_title=category_label,
        file_name=file_name,
        file_bytes=file_bytes,
    )

    memory_id = str(result.get("id") or "")
    status = str(result.get("status") or "queued")
    custom_id = f"kb:{local_ref}"

    return {
        "id": memory_id or local_ref,
        "title": title,
        "category": category,
        "categoryTitle": category_label,
        "fileName": file_name,
        "mimeType": "application/octet-stream",
        "fileSize": len(file_bytes),
        "uploadedAt": result.get("createdAt") or datetime.now(timezone.utc).isoformat(),
        "supermemoryCustomId": custom_id,
        "supermemorySyncedAt": result.get("createdAt") or datetime.now(timezone.utc).isoformat(),
        "supermemoryError": None,
        "supermemoryStatus": status,
        "supermemoryUrl": result.get("url"),
    }
