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


def test_create_correction_writes_supermemory_doc_and_clears_cache(monkeypatch) -> None:
    calls: dict[str, object] = {}
    cleared: list[bool] = []

    async def fake_add(*, content, custom_id, metadata=None):
        calls["content"] = content
        calls["custom_id"] = custom_id
        calls["metadata"] = metadata
        return {"id": "sm-1"}

    monkeypatch.setattr(kb_corrections.supermemory, "add_text_document", fake_add)
    monkeypatch.setattr(
        kb_corrections.supermemory,
        "invalidate_document_cache",
        lambda: cleared.append(True),
    )

    row = asyncio.run(
        kb_corrections.create_correction(
            title="Ron Comer retired", note="Ron Comer has retired."
        )
    )

    assert str(calls["custom_id"]).startswith("kbnote:")
    metadata = calls["metadata"]
    assert metadata["type"] == "kb_correction"
    assert metadata["note"] == "Ron Comer has retired."
    assert metadata["active"] is True
    assert "STANDING CORRECTION" in str(calls["content"])
    assert "Ron Comer has retired." in str(calls["content"])
    assert row["customId"] == calls["custom_id"]
    assert cleared == [True]


def test_update_correction_reuses_custom_id_and_preserves_created_at(monkeypatch) -> None:
    existing = {
        "id": "sm-1",
        "customId": "kbnote:abc",
        "title": "old",
        "note": "old note",
        "createdAt": "2026-08-01T00:00:00Z",
        "updatedAt": "",
        "linkedDocumentId": None,
    }
    calls: dict[str, object] = {}

    async def fake_list():
        return [existing]

    async def fake_add(*, content, custom_id, metadata=None):
        calls["custom_id"] = custom_id
        calls["metadata"] = metadata
        return {"id": "sm-1"}

    monkeypatch.setattr(kb_corrections, "list_corrections", fake_list)
    monkeypatch.setattr(kb_corrections.supermemory, "add_text_document", fake_add)
    monkeypatch.setattr(kb_corrections.supermemory, "invalidate_document_cache", lambda: None)

    row = asyncio.run(
        kb_corrections.update_correction(
            custom_id="kbnote:abc", title="Ron retired", note="Ron Comer has retired."
        )
    )

    assert calls["custom_id"] == "kbnote:abc"
    metadata = calls["metadata"]
    assert metadata["createdAt"] == "2026-08-01T00:00:00Z"
    assert metadata["note"] == "Ron Comer has retired."
    assert metadata["updatedAt"]
    assert row["note"] == "Ron Comer has retired."


def test_delete_correction_soft_deletes_when_hard_delete_unsupported(monkeypatch) -> None:
    calls: dict[str, object] = {}

    async def fake_delete(document_id):
        return False

    async def fake_add(*, content, custom_id, metadata=None):
        calls["metadata"] = metadata
        return {"id": "sm-1"}

    async def fake_list():
        return [
            {
                "id": "sm-1",
                "customId": "kbnote:abc",
                "title": "t",
                "note": "n",
                "createdAt": "2026-08-01T00:00:00Z",
                "updatedAt": "",
                "linkedDocumentId": None,
            }
        ]

    monkeypatch.setattr(kb_corrections.supermemory, "delete_document", fake_delete)
    monkeypatch.setattr(kb_corrections.supermemory, "add_text_document", fake_add)
    monkeypatch.setattr(kb_corrections, "list_corrections", fake_list)
    monkeypatch.setattr(kb_corrections.supermemory, "invalidate_document_cache", lambda: None)

    asyncio.run(kb_corrections.delete_correction(custom_id="kbnote:abc", document_id="sm-1"))

    assert calls["metadata"]["active"] is False
