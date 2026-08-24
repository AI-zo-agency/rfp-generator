"""Corrections CRUD routes and the upload notes field."""

import asyncio

import pytest
from fastapi import HTTPException

from app.api.v1 import knowledge_base as kb_api


def test_list_corrections_route_returns_rows(monkeypatch) -> None:
    async def fake_list():
        return [{"customId": "kbnote:a", "note": "Ron Comer has retired"}]

    monkeypatch.setattr(kb_api.supermemory, "is_configured", lambda: True)
    monkeypatch.setattr(kb_api.kb_corrections, "list_corrections", fake_list)

    result = asyncio.run(kb_api.list_knowledge_base_corrections())
    assert result["corrections"][0]["note"] == "Ron Comer has retired"


def test_create_correction_route_rejects_blank_note(monkeypatch) -> None:
    monkeypatch.setattr(kb_api.supermemory, "is_configured", lambda: True)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            kb_api.create_knowledge_base_correction(
                kb_api.CorrectionRequest(title="x", note="   ")
            )
        )
    assert exc.value.status_code == 400


def test_update_correction_route_returns_404_when_missing(monkeypatch) -> None:
    async def fake_update(**kwargs):
        raise LookupError("Correction not found.")

    monkeypatch.setattr(kb_api.supermemory, "is_configured", lambda: True)
    monkeypatch.setattr(kb_api.kb_corrections, "update_correction", fake_update)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            kb_api.update_knowledge_base_correction(
                "kbnote:missing", kb_api.CorrectionRequest(title="t", note="n")
            )
        )
    assert exc.value.status_code == 404


def test_upload_note_failure_does_not_fail_the_upload(monkeypatch) -> None:
    async def fake_upload(**kwargs):
        return {
            "id": "sm-1",
            "title": "Doc",
            "category": "reference",
            "categoryTitle": "Reference / Guides",
            "fileName": "doc.txt",
            "mimeType": "application/octet-stream",
            "fileSize": 3,
            "uploadedAt": "2026-08-24T00:00:00Z",
            "supermemoryCustomId": "kb:1",
            "supermemorySyncedAt": "2026-08-24T00:00:00Z",
            "supermemoryError": None,
            "supermemoryStatus": "queued",
            "supermemoryUrl": None,
        }

    async def boom(**kwargs):
        raise kb_api.supermemory.SupermemoryError("down", status_code=502)

    monkeypatch.setattr(kb_api.supermemory, "is_configured", lambda: True)
    monkeypatch.setattr(kb_api.knowledge_base_service, "upload_document", fake_upload)
    monkeypatch.setattr(kb_api.kb_corrections, "create_correction", boom)

    note_error = asyncio.run(
        kb_api._create_upload_note(
            notes="Ron Comer has retired",
            title="Doc",
            linked_document_id="kb:1",
        )
    )
    assert note_error is not None
    assert "down" in note_error


def test_upload_note_skipped_when_blank(monkeypatch) -> None:
    async def boom(**kwargs):
        raise AssertionError("should not be called")

    monkeypatch.setattr(kb_api.kb_corrections, "create_correction", boom)

    assert (
        asyncio.run(
            kb_api._create_upload_note(notes="   ", title="Doc", linked_document_id="kb:1")
        )
        is None
    )
