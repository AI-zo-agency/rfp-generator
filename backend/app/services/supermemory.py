"""Supermemory client.

API split (Supermemory platform — not our choice):
  - v4/search  → query KB
      • searchMode=hybrid    → memories (+ related docs)
      • searchMode=documents → raw PDF/DOCX chunks
    Retrieval priority: use memories when present; fill gaps from chunks
    (docs with Memories=0 still surface via chunk search).
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
_DOC_CONTENT_CACHE_MAX = 48
_doc_content_cache: dict[str, str] = {}
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
    from app.services.proposal_generation_cancel import run_with_generation_cancel

    url = f"{settings.supermemory_base_url.rstrip('/')}{path}"

    async def _do_request() -> httpx.Response:
        async with httpx.AsyncClient(timeout=120.0) as client:
            return await client.request(
                method,
                url,
                headers=_auth_headers(),
                json=json_body,
            )

    try:
        response = await run_with_generation_cancel(_do_request)
    except SupermemoryError:
        raise
    except Exception as exc:
        from app.services.proposal_generation_cancel import ProposalGenerationCancelled

        if isinstance(exc, ProposalGenerationCancelled):
            raise
        # httpx timeouts / connect errors must become SupermemoryError so callers
        # can soft-fail a single query instead of 502-ing Stage 1.
        raise SupermemoryError(
            f"Supermemory transport error: {exc}",
            status_code=502,
        ) from exc

    if response.status_code in (allow_status or set()):
        if not response.content:
            return {}
        try:
            return response.json()
        except Exception as exc:
            raise SupermemoryError(
                f"Supermemory returned non-JSON ({response.status_code}): {exc}",
                status_code=response.status_code,
            ) from exc

    if response.status_code >= 400:
        detail = response.text.strip() or response.reason_phrase
        raise SupermemoryError(
            f"Supermemory API error ({response.status_code}): {detail}",
            status_code=response.status_code,
        )

    if not response.content:
        return {}
    try:
        return response.json()
    except Exception as exc:
        raise SupermemoryError(
            f"Supermemory returned non-JSON ({response.status_code}): {exc}",
            status_code=response.status_code,
        ) from exc


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
    docs = await list_all_container_documents(force_refresh=force_refresh)
    return docs[:limit]


async def list_all_container_documents(
    *,
    page_size: int = 100,
    force_refresh: bool = False,
) -> list[dict[str, Any]]:
    """Paginate Supermemory /v3/documents/list for the active container."""
    global _doc_list_cache

    now = time.monotonic()
    if (
        not force_refresh
        and _doc_list_cache is not None
        and now - _doc_list_cache[0] < _DOC_LIST_CACHE_TTL_SECONDS
    ):
        return list(_doc_list_cache[1])

    all_docs: list[dict[str, Any]] = []
    page = 1

    while True:
        body = {"containerTag": container_tag(), "limit": page_size, "page": page}
        data = await _request("POST", "/v3/documents/list", json_body=body)
        batch: list[dict[str, Any]] = []
        if isinstance(data, dict):
            raw = data.get("memories") or data.get("documents") or data.get("items")
            if isinstance(raw, list):
                batch = [item for item in raw if isinstance(item, dict)]
        elif isinstance(data, list):
            batch = [item for item in data if isinstance(item, dict)]

        all_docs.extend(batch)

        pagination = data.get("pagination") if isinstance(data, dict) else None
        if not isinstance(pagination, dict):
            break
        total_pages = int(pagination.get("totalPages") or 1)
        if page >= total_pages:
            break
        page += 1

    _doc_list_cache = (now, all_docs)
    return all_docs


def invalidate_document_cache() -> None:
    """Drop the container document cache so the next list re-fetches."""
    global _doc_list_cache
    _doc_list_cache = None


async def delete_document(document_id: str) -> bool:
    """Hard-delete a document. Returns False when the endpoint is unavailable."""
    try:
        await _request("DELETE", f"/v3/documents/{document_id}")
    except SupermemoryError as exc:
        if exc.status_code in {404, 405, 501}:
            logger.warning(
                "Supermemory delete unsupported for %s (%s) — caller should soft-delete",
                document_id,
                exc.status_code,
            )
            return False
        raise
    invalidate_document_cache()
    return True


def is_knowledge_base_document(doc: dict[str, Any]) -> bool:
    metadata = doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {}
    doc_type = metadata.get("type") or doc.get("type")
    if doc_type == "knowledge_base":
        return True
    return str(doc.get("customId") or "").startswith("drive:")


def drive_file_id_from_document(doc: dict[str, Any]) -> str | None:
    custom = str(doc.get("customId") or "")
    if custom.startswith("drive:"):
        return custom.removeprefix("drive:")
    metadata = doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {}
    drive_id = metadata.get("driveFileId")
    return str(drive_id) if drive_id else None


def document_updated_at(doc: dict[str, Any]) -> str | None:
    for key in ("updatedAt", "updated_at", "modifiedTime", "modified_at"):
        value = doc.get(key)
        if value:
            return str(value)
    metadata = doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {}
    for key in ("modifiedTime", "driveModifiedTime"):
        value = metadata.get(key)
        if value:
            return str(value)
    return None


async def get_document(
    *,
    document_id: str | None = None,
    custom_id: str | None = None,
) -> dict[str, Any]:
    """Fetch a document record via v3 GET (includes status + content when ready)."""
    key = (custom_id or document_id or "").strip()
    if not key or not is_fetchable_document_key(key):
        return {}
    data = await _request("GET", f"/v3/documents/{key}", allow_status={404})
    return data if isinstance(data, dict) else {}


async def get_document_content(
    *,
    document_id: str | None = None,
    custom_id: str | None = None,
) -> str:
    """Fetch full indexed document text (all chunks) via v3 GET — search often returns one chunk only."""
    key = (custom_id or document_id or "").strip()
    if key:
        cached = _doc_content_cache.get(key)
        if cached is not None:
            return cached
    data = await get_document(document_id=document_id, custom_id=custom_id)
    content = str(data.get("content") or data.get("text") or "").strip()
    if key and content:
        if len(_doc_content_cache) >= _DOC_CONTENT_CACHE_MAX:
            _doc_content_cache.pop(next(iter(_doc_content_cache)))
        _doc_content_cache[key] = content
    return content


async def find_document_by_file_name(file_name: str) -> dict[str, Any] | None:
    """Find a container document whose metadata.fileName matches exactly."""
    target = file_name.strip().casefold()
    if not target:
        return None
    docs = await list_all_container_documents()
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
    threshold: float = 0.45,
) -> list[dict[str, Any]]:
    """Query KB via POST /v4/search (always v4 — never v3 for reads)."""
    body: dict[str, Any] = {
        "q": query,
        "limit": limit,
        "containerTag": container_tag(),
        "searchMode": search_mode,
        "rerank": True,
        "threshold": threshold,
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
    threshold: float = 0.45,
) -> list[dict[str, Any]]:
    """v4 hybrid — memories + document chunks (default retrieval mode)."""
    return await search_documents(
        query=query,
        limit=limit,
        include_full_docs=include_full_docs,
        filters=filters,
        search_mode="hybrid",
        threshold=threshold,
    )


async def search_document_chunks(
    *,
    query: str,
    limit: int = 8,
    filters: dict[str, Any] | None = None,
    threshold: float = 0.45,
) -> list[dict[str, Any]]:
    """v4 documents mode — raw PDF/DOCX chunks (phones/emails live here, not in memory summaries)."""
    return await search_documents(
        query=query,
        limit=limit,
        include_full_docs=True,
        filters=filters,
        search_mode="documents",
        threshold=threshold,
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

    # Chunks before memories — memories are summaries and often omit section detail (e.g. KPIs).
    for key in ("chunk", "chunks", "content", "text", "memory", "summary", "documentSummary"):
        value = hit.get(key)
        if value:
            if isinstance(value, list):
                return "\n".join(str(item) for item in value).strip()
            return str(value).strip()
    return ""


def is_chunk_hit(hit: dict[str, Any]) -> bool:
    """True when the hit is from searchMode=documents (raw chunks), not a memory summary."""
    if hit.get("chunk") or hit.get("chunks"):
        return True
    if hit.get("memory") and not hit.get("chunk"):
        return False
    return bool(hit.get("_retrieval_mode") == "documents")


def is_memory_hit(hit: dict[str, Any]) -> bool:
    """True when the hit carries a memory summary (searchMode=hybrid)."""
    if hit.get("_retrieval_mode") == "hybrid":
        return True
    return bool(hit.get("memory")) and not bool(hit.get("chunk"))


def merge_chunk_first_hits(
    memory_hits: list[dict[str, Any]],
    chunk_hits: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Chunks first for section-level fidelity; memories only fill docs chunks missed."""
    merged: list[dict[str, Any]] = []
    by_doc: dict[str, dict[str, Any]] = {}

    for hit in chunk_hits:
        tagged = dict(hit)
        tagged["_retrieval_mode"] = "documents"
        chunk_body = hit_text(tagged)
        if chunk_body:
            tagged["chunk"] = chunk_body
            tagged["content"] = chunk_body
        key = document_dedupe_key(tagged) or str(tagged.get("id") or id(tagged))
        if key in by_doc:
            continue
        by_doc[key] = tagged
        merged.append(tagged)

    for hit in memory_hits:
        tagged = dict(hit)
        tagged["_retrieval_mode"] = "hybrid"
        key = document_dedupe_key(tagged) or str(tagged.get("id") or id(tagged))
        if key in by_doc:
            existing = by_doc[key]
            for score_key in ("similarity", "score", "rerankScore"):
                mem_val = tagged.get(score_key)
                if mem_val is not None:
                    try:
                        ex_val = existing.get(score_key)
                        if ex_val is None or float(mem_val) > float(ex_val):
                            existing[score_key] = mem_val
                    except (TypeError, ValueError):
                        pass
            continue
        by_doc[key] = tagged
        merged.append(tagged)

    return merged


def merge_memory_and_chunk_hits(
    memory_hits: list[dict[str, Any]],
    chunk_hits: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Priority merge: keep memory hits, then add chunk hits for docs memories missed.

    When the same document appears in both, keep the memory hit for ranking identity but
    overlay chunk text so section detail (KPIs, etc.) is available without relying on
    memory summaries alone.
    """
    merged: list[dict[str, Any]] = []
    by_doc: dict[str, dict[str, Any]] = {}

    for hit in memory_hits:
        tagged = dict(hit)
        tagged["_retrieval_mode"] = "hybrid"
        key = document_dedupe_key(tagged) or str(tagged.get("id") or id(tagged))
        by_doc[key] = tagged
        merged.append(tagged)

    for hit in chunk_hits:
        tagged = dict(hit)
        tagged["_retrieval_mode"] = "documents"
        key = document_dedupe_key(tagged) or str(tagged.get("id") or id(tagged))
        chunk_body = hit_text(tagged)
        if key in by_doc:
            # Memories found the doc — enrich with chunk text when memory summary is thinner
            existing = by_doc[key]
            memory_body = hit_text(existing)
            if chunk_body and (
                not memory_body
                or len(chunk_body) > len(memory_body) * 1.2
                or (chunk_body and chunk_body not in memory_body)
            ):
                existing["chunk"] = chunk_body
                existing["content"] = chunk_body
                existing["_enriched_from_chunks"] = True
            continue
        # Not in memories → include chunk hit (Torrent-style: Memories 0, Chunks 10)
        by_doc[key] = tagged
        merged.append(tagged)

    return merged


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
