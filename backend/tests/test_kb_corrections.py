"""Standing corrections service and its Supermemory helpers."""

import asyncio

from app.services import supermemory


def test_invalidate_document_cache_clears_cached_docs() -> None:
    supermemory._doc_list_cache = (9_999_999.0, [{"id": "cached"}])
    supermemory.invalidate_document_cache()
    assert supermemory._doc_list_cache is None


def test_delete_document_returns_true_on_success(monkeypatch) -> None:
    seen: dict[str, object] = {}

    async def fake_request(method, path, **kwargs):
        seen["method"] = method
        seen["path"] = path
        return {"ok": True}

    monkeypatch.setattr(supermemory, "_request", fake_request)
    supermemory._doc_list_cache = (9_999_999.0, [{"id": "cached"}])

    assert asyncio.run(supermemory.delete_document("doc-1")) is True
    assert seen["method"] == "DELETE"
    assert seen["path"] == "/v3/documents/doc-1"
    assert supermemory._doc_list_cache is None


def test_delete_document_returns_false_when_endpoint_missing(monkeypatch) -> None:
    async def fake_request(method, path, **kwargs):
        raise supermemory.SupermemoryError("not found", status_code=404)

    monkeypatch.setattr(supermemory, "_request", fake_request)

    assert asyncio.run(supermemory.delete_document("doc-1")) is False
