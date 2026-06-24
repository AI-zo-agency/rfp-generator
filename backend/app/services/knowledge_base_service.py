import uuid
from datetime import datetime, timezone
from typing import Any

from app.services.knowledge_base_document_types import category_title, is_valid_category
from app.services import supermemory


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
