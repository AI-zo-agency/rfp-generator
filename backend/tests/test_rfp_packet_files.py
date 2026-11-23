"""Cite-linked packet files: store, replace, assemble text."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.services import rfp_packet_files as packet
from app.services import rfp_repository as repo
from app.services import rfp_storage
from app.services.rfp_content import assemble_packet_pdf_text


def _pdf(n: int = 600) -> bytes:
    return b"%PDF-1.4\n" + (b"0" * n)


@pytest.fixture()
def local_packet_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db = tmp_path / "rfps.db"
    monkeypatch.setattr("app.services.supabase_db.use_supabase_db", lambda: False)
    monkeypatch.setattr("app.services.rfp_repository._use_supabase", lambda: False)
    monkeypatch.setattr("app.services.rfp_repository._db_path", lambda: db)
    monkeypatch.setattr("app.services.rfp_packet_files._use_supabase", lambda: False)
    monkeypatch.setattr(rfp_storage.settings, "pdf_storage_path", tmp_path / "pdfs")
    monkeypatch.setattr(rfp_storage, "use_supabase", lambda: False)
    repo.init_db()
    return tmp_path


def test_assemble_keeps_supplements_when_truncating_primary():
    primary = "P" * 100
    supplements = [("Exhibit A", "ex-a.pdf", "SCOPE " + ("W" * 40))]
    out = assemble_packet_pdf_text(primary, supplements, max_chars=80)
    assert "--- PACKET FILE: Exhibit A (ex-a.pdf) ---" in out
    assert "SCOPE " in out
    assert len(out) <= 80


def test_assemble_empty_supplements_is_primary_only():
    assert assemble_packet_pdf_text("hello", [], max_chars=10) == "hello"


def test_upsert_list_replace_delete(local_packet_env: Path):
    rfp_id = "rfp-packet-1"
    first = packet.upsert_packet_file(
        rfp_id,
        cite_target="Exhibit A",
        cite_quote="See Exhibit A attached.",
        filename="exhibit-a.pdf",
        content=_pdf(),
        uploaded_by="rachel@zo.com",
    )
    assert first.cite_target == "Exhibit A"
    assert first.filename == "exhibit-a.pdf"
    assert first.content_sha256
    listed = packet.list_packet_files(rfp_id)
    assert len(listed) == 1
    assert listed[0].id == first.id

    second = packet.upsert_packet_file(
        rfp_id,
        cite_target="Exhibit A",
        cite_quote="See Exhibit A attached.",
        filename="exhibit-a-v2.pdf",
        content=_pdf(700),
    )
    listed2 = packet.list_packet_files(rfp_id)
    assert len(listed2) == 1
    assert listed2[0].filename == "exhibit-a-v2.pdf"
    assert listed2[0].id != first.id

    assert packet.delete_packet_file(rfp_id, second.id) is True
    assert packet.list_packet_files(rfp_id) == []


def test_one_file_per_distinct_cite(local_packet_env: Path):
    rfp_id = "rfp-packet-2"
    packet.upsert_packet_file(
        rfp_id, cite_target="Exhibit A", cite_quote="q1", filename="a.pdf", content=_pdf()
    )
    packet.upsert_packet_file(
        rfp_id, cite_target="Attachment A", cite_quote="q2", filename="att.pdf", content=_pdf()
    )
    targets = {f.cite_target for f in packet.list_packet_files(rfp_id)}
    assert targets == {"Exhibit A", "Attachment A"}


def test_load_local_rfp_text_includes_labeled_packet(local_packet_env: Path):
    from app.models.rfp import RfpRecord
    from app.services import rfp_content

    rfp_id = "rfp-packet-3"
    primary_path = rfp_storage.save_rfp_pdf(rfp_id, _pdf())
    packet.upsert_packet_file(
        rfp_id,
        cite_target="Exhibit A",
        cite_quote="Consultant shall perform Exhibit A",
        filename="sow.pdf",
        content=_pdf(700),
    )
    rfp = RfpRecord(
        id=rfp_id,
        title="T",
        client="C",
        dueDate="2026-12-01",
        receivedDate="2026-11-01",
        lastActivity="2026-11-01",
        lastActivityNote="n",
        pdfPath=primary_path,
        description="Primary description only",
    )

    with (
        patch(
            "app.services.rfp_content.extract_pdf_text_from_bytes",
            return_value="PRIMARY BODY TEXT",
        ),
        patch(
            "app.services.rfp_content._load_packet_supplement_texts",
            return_value=[
                ("Exhibit A", "sow.pdf", "EXHIBIT SCOPE DELIVERABLE ALPHA"),
            ],
        ),
    ):
        _desc, pdf_text, *_rest = rfp_content.load_local_rfp_text(rfp, max_chars=50_000)

    assert "PRIMARY BODY TEXT" in pdf_text
    assert "--- PACKET FILE: Exhibit A (sow.pdf) ---" in pdf_text
    assert "EXHIBIT SCOPE DELIVERABLE ALPHA" in pdf_text


def test_load_packet_supplement_texts_reads_stored_file(local_packet_env: Path):
    from app.services import rfp_content

    rfp_id = "rfp-packet-4"
    exhibit_bytes = _pdf(700)
    item = packet.upsert_packet_file(
        rfp_id,
        cite_target="Exhibit A",
        cite_quote="q",
        filename="sow.pdf",
        content=exhibit_bytes,
    )
    assert item.storage_path

    with patch(
        "app.services.rfp_content.extract_pdf_text_from_bytes",
        return_value="EXHIBIT SCOPE DELIVERABLE ALPHA",
    ):
        rows = rfp_content._load_packet_supplement_texts(rfp_id, max_chars=50_000)

    assert rows == [("Exhibit A", "sow.pdf", "EXHIBIT SCOPE DELIVERABLE ALPHA")]


def test_require_go_no_go_idle_raises_409_when_running():
    import asyncio

    from fastapi import HTTPException

    from app.api.v1 import rfps as rfps_api

    class _Job:
        status = "running"

    async def _run() -> None:
        with patch(
            "app.services.proposal_job_runner.get_proposal_job",
            new=AsyncMock(return_value=_Job()),
        ):
            with pytest.raises(HTTPException) as exc:
                await rfps_api._require_go_no_go_idle("rfp-busy")
            assert exc.value.status_code == 409

    asyncio.run(_run())
