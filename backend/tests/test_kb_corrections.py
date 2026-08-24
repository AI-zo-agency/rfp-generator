"""Standing corrections service and its Supermemory helpers."""

import asyncio

from app.services import kb_corrections, supermemory


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


def _memory(custom_id: str, note: str, created: str, **extra: object) -> dict:
    metadata = {
        "type": "kb_correction",
        "title": note[:40],
        "note": note,
        "createdAt": created,
        "active": True,
    }
    metadata.update(extra)
    return {"id": custom_id.split(":")[-1], "customId": custom_id, "metadata": metadata}


def test_list_corrections_filters_and_sorts_newest_first(monkeypatch) -> None:
    memories = [
        _memory("kbnote:a", "Ron Comer has retired", "2026-08-20T00:00:00Z"),
        {"id": "doc", "customId": "kb:xyz", "metadata": {"type": "knowledge_base"}},
        _memory("kbnote:b", "Ella Lindau is now Director of Operations", "2026-08-24T00:00:00Z"),
    ]

    async def fake_list(**kwargs):
        return memories

    monkeypatch.setattr(kb_corrections.supermemory, "list_container_memories", fake_list)

    rows = asyncio.run(kb_corrections.list_corrections())
    assert [row["customId"] for row in rows] == ["kbnote:b", "kbnote:a"]
    assert rows[0]["note"] == "Ella Lindau is now Director of Operations"


def test_list_corrections_skips_inactive(monkeypatch) -> None:
    memories = [_memory("kbnote:a", "old note", "2026-08-20T00:00:00Z", active=False)]

    async def fake_list(**kwargs):
        return memories

    monkeypatch.setattr(kb_corrections.supermemory, "list_container_memories", fake_list)

    assert asyncio.run(kb_corrections.list_corrections()) == []


def test_corrections_block_renders_dated_lines() -> None:
    block = kb_corrections.corrections_block(
        [
            {"note": "Ron Comer has retired", "createdAt": "2026-08-20T00:00:00Z"},
            {"note": "Ella Lindau is now Director of Operations", "createdAt": "2026-08-24T00:00:00Z"},
        ]
    )
    assert "## STANDING CORRECTIONS (authoritative)" in block
    assert "- (2026-08-20) Ron Comer has retired" in block
    assert "correction wins" in block


def test_corrections_block_empty_when_no_corrections() -> None:
    assert kb_corrections.corrections_block([]) == ""


def test_corrections_prompt_block_survives_supermemory_failure(monkeypatch) -> None:
    async def boom(**kwargs):
        raise kb_corrections.supermemory.SupermemoryError("down", status_code=502)

    monkeypatch.setattr(kb_corrections.supermemory, "list_container_memories", boom)

    block = asyncio.run(kb_corrections.corrections_prompt_block())
    assert "corrections unavailable" in block
