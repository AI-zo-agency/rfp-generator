import json
import logging
from typing import Any

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

# v4 hybrid search does not match type=knowledge_base filters; exclude intake RFP docs instead.
KNOWLEDGE_BASE_SEARCH_FILTERS: dict[str, Any] = {
    "AND": [{"key": "type", "value": "rfp", "negate": True}]
}


def is_knowledge_base_hit(hit: dict[str, Any]) -> bool:
    metadata = hit.get("metadata") if isinstance(hit.get("metadata"), dict) else {}
    doc_type = metadata.get("type")
    return doc_type != "rfp"


class SupermemoryError(Exception):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def container_tag() -> str:
    return settings.resolved_container_tag


def _auth_headers(*, json_request: bool = True) -> dict[str, str]:
    if not settings.supermemory_api_key:
        raise SupermemoryError("SUPERMEMORY_API_KEY is not configured", status_code=503)
    headers = {"Authorization": f"Bearer {settings.supermemory_api_key}"}
    if json_request:
        headers["Content-Type"] = "application/json"
    return headers


async def _request(
    method: str,
    path: str,
    *,
    json_body: dict[str, Any] | None = None,
) -> Any:
    url = f"{settings.supermemory_base_url.rstrip('/')}{path}"
    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.request(
            method,
            url,
            headers=_auth_headers(),
            json=json_body,
        )

    if response.status_code >= 400:
        detail = response.text.strip() or response.reason_phrase
        raise SupermemoryError(
            f"Supermemory API error ({response.status_code}): {detail}",
            status_code=response.status_code,
        )

    if not response.content:
        return {}
    return response.json()


def is_configured() -> bool:
    return bool(settings.supermemory_api_key.strip())


async def list_container_memories(*, limit: int = 100) -> list[dict[str, Any]]:
    body = {"containerTag": container_tag(), "limit": limit}
    data = await _request("POST", "/v3/documents/list", json_body=body)
    if isinstance(data, dict):
        memories = data.get("memories") or data.get("documents") or data.get("items")
        if isinstance(memories, list):
            return [item for item in memories if isinstance(item, dict)]
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return []


async def list_connections() -> list[dict[str, Any]]:
    body: dict[str, Any] = {
        "provider": "google-drive",
        "containerTag": container_tag(),
    }
    data = await _request("POST", "/v3/connections/list", json_body=body)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("connections", "items", "data"):
            value = data.get(key)
            if isinstance(value, list):
                return value
    return []


async def has_google_drive_connection() -> bool:
    try:
        connections = await list_connections()
    except SupermemoryError:
        return False
    return any(
        (conn.get("provider") or conn.get("type") or "google-drive") == "google-drive"
        for conn in connections
    )


async def create_google_drive_connection() -> dict[str, Any]:
    redirect_url = f"{settings.app_url.rstrip('/')}/knowledge-base?drive_connected=1"
    body = {
        "redirectUrl": redirect_url,
        "containerTag": container_tag(),
        "metadata": {
            "source": "google-drive",
            "syncScope": "selected",
            "sharedDrive": settings.google_drive_shared_drive_name,
        },
    }
    return await _request("POST", "/v3/connections/google-drive", json_body=body)


async def list_google_drive_documents() -> list[dict[str, Any]]:
    body = {"containerTag": container_tag()}
    data = await _request(
        "POST",
        "/v3/connections/google-drive/documents",
        json_body=body,
    )
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("documents", "items", "data"):
            value = data.get(key)
            if isinstance(value, list):
                return value
    return []


async def trigger_google_drive_sync() -> dict[str, Any]:
    return await _request(
        "POST",
        "/v3/connections/google-drive/sync",
        json_body={"containerTag": container_tag()},
    )


async def add_text_document(
    *,
    content: str,
    custom_id: str,
    metadata: dict[str, str | int | bool] | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "content": content,
        "containerTag": container_tag(),
        "customId": custom_id,
    }
    if metadata:
        body["metadata"] = metadata
    result = await _request("POST", "/v3/documents", json_body=body)
    return result if isinstance(result, dict) else {"ok": True}


async def upload_file_document(
    *,
    file_bytes: bytes,
    filename: str,
    custom_id: str,
    metadata: dict[str, str | int | bool] | None = None,
) -> dict[str, Any]:
    url = f"{settings.supermemory_base_url.rstrip('/')}/v3/documents/file"
    form_data: dict[str, str] = {
        "containerTag": container_tag(),
        "customId": custom_id,
    }
    if metadata:
        form_data["metadata"] = json.dumps(metadata)

    async with httpx.AsyncClient(timeout=300.0) as client:
        response = await client.post(
            url,
            headers=_auth_headers(json_request=False),
            data=form_data,
            files={"file": (filename, file_bytes)},
        )

    if response.status_code >= 400:
        detail = response.text.strip() or response.reason_phrase
        raise SupermemoryError(
            f"Supermemory file upload error ({response.status_code}): {detail}",
            status_code=response.status_code,
        )

    if not response.content:
        return {}
    result = response.json()
    return result if isinstance(result, dict) else {"ok": True}


async def search_documents(
    *,
    query: str,
    limit: int = 8,
    include_full_docs: bool = False,
    filters: dict[str, Any] | None = None,
    search_mode: str = "hybrid",
) -> list[dict[str, Any]]:
    body: dict[str, Any] = {
        "q": query,
        "limit": limit,
        "containerTag": container_tag(),
        "searchMode": search_mode,
        "rerank": True,
    }
    if include_full_docs:
        body["include"] = {
            "documents": True,
            "summaries": True,
        }
    if filters:
        body["filters"] = filters

    data = await _request("POST", "/v4/search", json_body=body)
    hits = _normalize_search_results(data)
    logger.info(
        "Supermemory v4 %s search: %d hits for query=%r",
        search_mode,
        len(hits),
        query[:80],
    )
    return hits


def _normalize_search_results(data: Any) -> list[dict[str, Any]]:
    raw = _extract_search_results(data)
    return [_normalize_search_hit(hit) for hit in raw]


def _normalize_search_hit(hit: dict[str, Any]) -> dict[str, Any]:
    """Map v4 memory/chunk results onto the shape used by format_search_hits."""
    normalized = dict(hit)
    content = (
        hit.get("memory")
        or hit.get("chunk")
        or hit.get("content")
        or hit.get("text")
        or hit.get("summary")
        or hit.get("documentSummary")
        or ""
    )
    if content and not normalized.get("content"):
        normalized["content"] = content

    documents = hit.get("documents")
    if isinstance(documents, list) and documents:
        document = documents[0] if isinstance(documents[0], dict) else {}
        normalized.setdefault("customId", document.get("customId"))
        normalized.setdefault("title", document.get("title"))
        if not normalized.get("content"):
            document_content = document.get("content") or document.get("text") or ""
            if document_content:
                normalized["content"] = document_content

    return normalized


def _extract_search_results(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, dict):
        for key in ("results", "documents", "items", "memories", "chunks"):
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return []


def format_search_hits(hits: list[dict[str, Any]], *, max_chars: int = 12_000) -> str:
    parts: list[str] = []
    total = 0

    for index, hit in enumerate(hits, start=1):
        metadata = hit.get("metadata") if isinstance(hit.get("metadata"), dict) else {}
        title = (
            hit.get("title")
            or metadata.get("title")
            or metadata.get("fileName")
            or hit.get("customId")
            or f"Result {index}"
        )
        content = (
            hit.get("content")
            or hit.get("memory")
            or hit.get("chunk")
            or hit.get("text")
            or hit.get("summary")
            or hit.get("documentSummary")
            or ""
        )
        if isinstance(content, list):
            content = "\n".join(str(item) for item in content)
        content = str(content).strip()
        if not content:
            continue

        block = f"### {title}\n{content}"
        remaining = max_chars - total
        if remaining <= 0:
            break
        if len(block) > remaining:
            block = block[:remaining]
        parts.append(block)
        total += len(block)

    return "\n\n".join(parts).strip()


async def ingest_knowledge_base_file(
    *,
    document_id: str,
    title: str,
    category: str,
    category_title: str,
    file_name: str,
    file_bytes: bytes,
) -> dict[str, Any]:
    custom_id = f"kb:{document_id}"
    return await upload_file_document(
        file_bytes=file_bytes,
        filename=file_name,
        custom_id=custom_id,
        metadata={
            "type": "knowledge_base",
            "title": title,
            "category": category,
            "categoryTitle": category_title,
            "fileName": file_name,
        },
    )
