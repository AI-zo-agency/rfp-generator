"""Knowledge base document deletion."""

import asyncio

from app.services import knowledge_base_service, supermemory


def _kb_memory(
    *,
    memory_id: str = "sm-1",
    custom_id: str = "kb:abc",
    title: str = "Test doc",
) -> dict:
    return {
        "id": memory_id,
        "customId": custom_id,
        "metadata": {
            "type": "knowledge_base",
            "title": title,
            "category": "reference",
            "fileName": "test.pdf",
        },
    }


def test_delete_document_calls_supermemory_with_custom_id_first(monkeypatch) -> None:
    seen: list[str] = []

    async def fake_list(**kwargs):
        return [_kb_memory()]

    async def fake_delete(document_id: str) -> bool:
        seen.append(document_id)
        return True

    async def fake_list_corrections():
        return []

    monkeypatch.setattr(supermemory, "list_all_container_documents", fake_list)
    monkeypatch.setattr(supermemory, "delete_document", fake_delete)
    monkeypatch.setattr(
        knowledge_base_service.kb_corrections,
        "list_corrections",
        fake_list_corrections,
    )

    asyncio.run(
        knowledge_base_service.delete_document(
            document_id="sm-1",
            custom_id="kb:abc",
        )
    )
    assert seen == ["kb:abc"]


def test_delete_document_raises_when_not_found(monkeypatch) -> None:
    async def fake_list(**kwargs):
        return []

    monkeypatch.setattr(supermemory, "list_all_container_documents", fake_list)

    try:
        asyncio.run(
            knowledge_base_service.delete_document(
                document_id="missing",
                custom_id=None,
            )
        )
        assert False, "expected LookupError"
    except LookupError as exc:
        assert "not found" in str(exc).casefold()


def test_delete_document_removes_linked_correction(monkeypatch) -> None:
    deleted_corrections: list[tuple[str, str]] = []

    async def fake_list(**kwargs):
        return [_kb_memory()]

    async def fake_delete(document_id: str) -> bool:
        return True

    async def fake_list_corrections():
        return [
            {
                "id": "corr-1",
                "customId": "kbnote:xyz",
                "linkedDocumentId": "kb:abc",
            }
        ]

    async def fake_delete_correction(*, custom_id: str, document_id: str) -> None:
        deleted_corrections.append((custom_id, document_id))

    monkeypatch.setattr(supermemory, "list_all_container_documents", fake_list)
    monkeypatch.setattr(supermemory, "delete_document", fake_delete)
    monkeypatch.setattr(
        knowledge_base_service.kb_corrections,
        "list_corrections",
        fake_list_corrections,
    )
    monkeypatch.setattr(
        knowledge_base_service.kb_corrections,
        "delete_correction",
        fake_delete_correction,
    )

    asyncio.run(
        knowledge_base_service.delete_document(
            document_id="sm-1",
            custom_id="kb:abc",
        )
    )
    assert deleted_corrections == [("kbnote:xyz", "corr-1")]
