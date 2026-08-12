"""JustWin re-sync must skip RFPs already on file (same id or title+date)."""

from __future__ import annotations

from unittest.mock import patch

from app.models.rfp import RfpRecord
from app.services import rfp_repository as repo
from app.services.rfp_repository import find_existing_justwin_rfp, upsert_rfp


def _sample(*, external_id: str, title: str, received: str) -> RfpRecord:
    return RfpRecord(
        id=f"rfp-jw-{external_id}",
        externalId=external_id,
        title=title,
        client="City",
        source="justwin",
        sector="Public Sector",
        location="NY",
        dueDate="2026-09-01",
        receivedDate=received,
        stage="intake",
        status="new",
        priority="medium",
        lastActivity="2026-08-11T12:00:00+00:00",
        lastActivityNote="test",
        contractRole="prime",
        syncedAt="2026-08-11T12:00:00+00:00",
        pdfPath="/tmp/sample.pdf",
    )


def _sqlite_ctx(tmp_path):
    db_file = tmp_path / "rfps.db"
    return (
        patch.object(repo, "_use_supabase", return_value=False),
        patch("app.services.rfp_repository._use_supabase", return_value=False),
        patch("app.services.supabase_db.use_supabase_db", return_value=False),
        patch.object(repo, "_db_path", return_value=db_file),
        patch("app.services.rfp_repository._db_path", return_value=db_file),
    )


def test_find_existing_by_external_id(tmp_path):
    patches = _sqlite_ctx(tmp_path)
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        repo.init_db()
        upsert_rfp(_sample(external_id="abc123", title="Bridge RFP", received="2026-08-11"))

        hit = find_existing_justwin_rfp(
            external_id="abc123",
            title="Other Title",
            received_date="2026-08-11",
        )
        assert hit is not None
        assert hit.external_id == "abc123"


def test_find_existing_by_title_and_received_date(tmp_path):
    patches = _sqlite_ctx(tmp_path)
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        repo.init_db()
        upsert_rfp(
            _sample(
                external_id="old-id",
                title="Civic Ballot Education",
                received="2026-08-10",
            )
        )

        hit = find_existing_justwin_rfp(
            external_id="new-id",
            title="Civic Ballot Education",
            received_date="2026-08-10",
        )
        assert hit is not None
        assert hit.external_id == "old-id"


def test_same_title_different_date_is_not_duplicate(tmp_path):
    patches = _sqlite_ctx(tmp_path)
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        repo.init_db()
        upsert_rfp(
            _sample(external_id="a", title="Annual Transit RFP", received="2026-08-01")
        )

        hit = find_existing_justwin_rfp(
            external_id="b",
            title="Annual Transit RFP",
            received_date="2026-08-11",
        )
        assert hit is None
