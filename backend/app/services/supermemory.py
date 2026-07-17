"""Supermemory client.

API split (Supermemory platform — not our choice):
  - v4/search  → query KB (hybrid = memories + document chunks; use hybrid by default)
  - v3/documents* → ingest only (batch upload, file upload, list) — no v4 write API exists
  - v3/connections* → Google Drive OAuth/sync only

All proposal retrieval uses v4 search. Ingest scripts use v3 batch upload.
"""

import json
import logging
import time
from typing import Any

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_DOC_LIST_CACHE_TTL_SECONDS = 60.0
_doc_list_cache: tuple[float, list[dict[str, Any]]] | None = None

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
    allow_status: set[int] | None = None,
) -> Any:
    url = f"{settings.supermemory_base_url.rstrip('/')}{path}"
    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.request(
            method,
            url,
            headers=_auth_headers(),
            json=json_body,
        )

    if response.status_code in (allow_status or set()):
        if not response.content:
            return {}
        return response.json()

    if response.status_code >= 400:
        detail = response.text.strip() or response.reason_phrase
        raise SupermemoryError(
            f"Supermemory API error ({response.status_code}): {detail}",
            status_code=response.status_code,
        )

    if not response.content:
        return {}
    return response.json()


def is_fetchable_document_key(key: str) -> bool:
    """v3 GET only accepts ingest customIds (drive:/kb:), not v4 memory/chunk ids."""
    normalized = key.strip()
    return normalized.startswith("drive:") or normalized.startswith("kb:")


def document_fetch_key(doc: dict[str, Any]) -> str:
    custom_id = str(doc.get("customId") or "").strip()
    if is_fetchable_document_key(custom_id):
        return custom_id
    return ""


def is_configured() -> bool:
    return bool(settings.supermemory_api_key.strip())


async def list_container_memories(
    *,
    limit: int = 100,
    force_refresh: bool = False,
) -> list[dict[str, Any]]:
    global _doc_list_cache

    now = time.monotonic()
    if (
        not force_refresh
        and _doc_list_cache is not None
        and now - _doc_list_cache[0] < _DOC_LIST_CACHE_TTL_SECONDS
    ):
        cached = _doc_list_cache[1]
        return cached[:limit]

    body = {"containerTag": container_tag(), "limit": limit}
    data = await _request("POST", "/v3/documents/list", json_body=body)
    if isinstance(data, dict):
        memories = data.get("memories") or data.get("documents") or data.get("items")
        if isinstance(memories, list):
            docs = [item for item in memories if isinstance(item, dict)]
            _doc_list_cache = (now, docs)
            return docs[:limit]
    if isinstance(data, list):
        docs = [item for item in data if isinstance(item, dict)]
        _doc_list_cache = (now, docs)
        return docs[:limit]
    _doc_list_cache = (now, [])
    return []


async def get_document_content(
    *,
    document_id: str | None = None,
    custom_id: str | None = None,
) -> str:
    """Fetch full indexed document text (all chunks) via v3 GET — search often returns one chunk only."""
    key = (custom_id or document_id or "").strip()
    if not key or not is_fetchable_document_key(key):
        return ""
    data = await _request("GET", f"/v3/documents/{key}", allow_status={404})
    if not isinstance(data, dict):
        return ""
    content = data.get("content") or data.get("text") or ""
    return str(content).strip()


async def find_document_by_file_name(file_name: str) -> dict[str, Any] | None:
    """Find a container document whose metadata.fileName matches exactly."""
    target = file_name.strip().casefold()
    if not target:
        return None
    docs = await list_container_memories(limit=1000)
    for doc in docs:
        metadata = doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {}
        if str(metadata.get("fileName") or "").strip().casefold() == target:
            return doc
    return None


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
    """Query KB via POST /v4/search (always v4 — never v3 for reads)."""
    body: dict[str, Any] = {
        "q": query,
        "limit": limit,
        "containerTag": container_tag(),
        "searchMode": search_mode,
        "rerank": True,
        "threshold": 0.45,
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


async def search_hybrid(
    *,
    query: str,
    limit: int = 8,
    include_full_docs: bool = False,
    filters: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """v4 hybrid — memories + document chunks (default retrieval mode)."""
    return await search_documents(
        query=query,
        limit=limit,
        include_full_docs=include_full_docs,
        filters=filters,
        search_mode="hybrid",
    )


async def search_document_chunks(
    *,
    query: str,
    limit: int = 8,
    filters: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """v4 documents mode — raw PDF/DOCX chunks (phones/emails live here, not in memory summaries)."""
    return await search_documents(
        query=query,
        limit=limit,
        include_full_docs=True,
        filters=filters,
        search_mode="documents",
    )


def merge_search_hits(
    hit_groups: list[list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    seen: set[str] = set()
    merged: list[dict[str, Any]] = []
    for hits in hit_groups:
        for hit in hits:
            key = str(hit.get("id") or hit.get("customId") or id(hit))
            if key in seen:
                continue
            seen.add(key)
            merged.append(hit)
    return merged


def hit_text(hit: dict[str, Any]) -> str:
    """Best available text from a v4 search hit — prefer document chunks over memory summaries."""
    documents = hit.get("documents")
    if isinstance(documents, list):
        for document in documents:
            if not isinstance(document, dict):
                continue
            for key in ("chunk", "content", "text"):
                value = document.get(key)
                if value:
                    return str(value).strip()

    for key in ("chunk", "content", "memory", "text", "summary", "documentSummary"):
        value = hit.get(key)
        if value:
            if isinstance(value, list):
                return "\n".join(str(item) for item in value).strip()
            return str(value).strip()
    return ""


def hit_custom_id(hit: dict[str, Any]) -> str:
    documents = hit.get("documents")
    if isinstance(documents, list):
        for document in documents:
            if isinstance(document, dict):
                custom_id = str(document.get("customId") or "").strip()
                if is_fetchable_document_key(custom_id):
                    return custom_id
    custom_id = str(hit.get("customId") or "").strip()
    if is_fetchable_document_key(custom_id):
        return custom_id
    return ""


def hit_file_name(hit: dict[str, Any]) -> str:
    metadata = hit.get("metadata") if isinstance(hit.get("metadata"), dict) else {}
    return str(
        metadata.get("fileName")
        or hit.get("title")
        or hit.get("customId")
        or ""
    )


def document_dedupe_key(hit: dict[str, Any]) -> str:
    """Stable per-document key for deduping search hits."""
    file_name = hit_file_name(hit).strip().casefold()
    if file_name:
        return file_name
    return hit_custom_id(hit).strip()


async def resolve_hit_document_content(hit: dict[str, Any]) -> str:
    """Return full indexed document text for a search hit, falling back to chunk text."""
    custom_id = hit_custom_id(hit)
    if custom_id:
        content = await get_document_content(custom_id=custom_id)
        if content:
            return content

    file_name = hit_file_name(hit).strip()
    if file_name:
        try:
            doc = await find_document_by_file_name(file_name)
            if doc:
                doc_key = document_fetch_key(doc)
                if doc_key:
                    content = await get_document_content(custom_id=doc_key)
                    if content:
                        return content
        except SupermemoryError:
            pass

    return hit_text(hit)


def _normalize_search_hit(hit: dict[str, Any]) -> dict[str, Any]:
    """Map v4 memory/chunk results onto the shape used by format_search_hits."""
    normalized = dict(hit)
    content = hit_text(hit)
    if content:
        normalized["content"] = content

    documents = hit.get("documents")
    if isinstance(documents, list) and documents:
        document = documents[0] if isinstance(documents[0], dict) else {}
        normalized.setdefault("customId", document.get("customId"))
        normalized.setdefault("title", document.get("title"))

    return normalized


def _normalize_search_results(data: Any) -> list[dict[str, Any]]:
    raw = _extract_search_results(data)
    return [_normalize_search_hit(hit) for hit in raw]


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
        content = hit_text(hit)
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


def _hit_label(hit: dict[str, Any]) -> str:
    metadata = hit.get("metadata") if isinstance(hit.get("metadata"), dict) else {}
    return str(
        hit.get("title")
        or metadata.get("fileName")
        or hit.get("customId")
        or hit.get("id")
        or "document"
    )


def _contact_doc_priority(hit: dict[str, Any]) -> int:
    label = _hit_label(hit).casefold()
    body = hit_text(hit).casefold()
    if "companyfacts" in label or "01_company" in label:
        return 0
    if "companyoverview" in label or "company overview" in label:
        return 1
    if "mastertemplate_intro" in label or "mastertemplate intro" in label:
        return 2
    if "04_bio" in label or "bio_sonja" in label or "bio sonja" in label:
        return 3
    if "bio" in label or "mastertemplate" in label:
        return 4
    if "phone" in body or "email" in body or "contact" in body:
        return 5
    return 9


async def fetch_hits_fact_text(
    hits: list[dict[str, Any]],
    *,
    max_hits: int = 12,
    max_chars: int = 32_000,
) -> str:
    """Build a fact blob from v4 search chunks (no v3 document GET)."""
    ranked = sorted(hits, key=_contact_doc_priority)
    parts: list[str] = []
    total = 0

    for hit in ranked[:max_hits]:
        content = hit_text(hit)
        if not content:
            continue
        block = f"### {_hit_label(hit)}\n{content}"
        remaining = max_chars - total
        if remaining <= 0:
            break
        if len(block) > remaining:
            block = block[:remaining]
        parts.append(block)
        total += len(block)

    return "\n\n".join(parts).strip()


async def batch_add_documents(
    documents: list[dict[str, Any]],
    *,
    dreaming: str = "instant",
) -> dict[str, Any]:
    """POST /v3/documents/batch — ingest many docs in one call (content = text or URL)."""
    if not documents:
        return {"results": []}
    body: dict[str, Any] = {
        "containerTag": container_tag(),
        "dreaming": dreaming,
        "documents": documents,
    }
    result = await _request("POST", "/v3/documents/batch", json_body=body)
    return result if isinstance(result, dict) else {"results": []}


async def trigger_google_drive_import() -> dict[str, Any]:
    """Kick off Supermemory Google Drive import (async on their side)."""
    return await _request(
        "POST",
        "/v3/connections/google-drive/import",
        json_body={"containerTags": [container_tag()]},
    )


async def ingest_knowledge_base_file(
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
