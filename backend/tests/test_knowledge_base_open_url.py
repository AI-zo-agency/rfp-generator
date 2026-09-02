"""Resolve open URLs for knowledge-base documents."""

import asyncio

from app.services import knowledge_base_service, supermemory


def test_resolve_open_url_uses_list_url_first(monkeypatch) -> None:
    async def fake_find(**kwargs):
        return {
            "id": "sm-1",
            "customId": "kb:abc",
            "url": "https://files.supermemory.ai/example.pdf",
        }

    monkeypatch.setattr(knowledge_base_service, "find_kb_document", fake_find)

    url = asyncio.run(
        knowledge_base_service.resolve_open_url(
            document_id="sm-1",
            custom_id="kb:abc",
        )
    )
    assert url == "https://files.supermemory.ai/example.pdf"


def test_resolve_open_url_fetches_file_url_when_missing(monkeypatch) -> None:
    async def fake_find(**kwargs):
        return {"id": "sm-1", "customId": "kb:abc", "metadata": {}}

    async def fake_file_url(*, document_key: str) -> str:
        assert document_key == "kb:abc"
        return "https://files.supermemory.ai/fresh.pdf"

    monkeypatch.setattr(knowledge_base_service, "find_kb_document", fake_find)
    monkeypatch.setattr(supermemory, "get_document_file_url", fake_file_url)

    url = asyncio.run(
        knowledge_base_service.resolve_open_url(
            document_id="sm-1",
            custom_id="kb:abc",
        )
    )
    assert url == "https://files.supermemory.ai/fresh.pdf"
