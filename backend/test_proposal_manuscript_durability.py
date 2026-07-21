"""Manuscript durability: soft regen never deletes; archives restore."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.core import config
from app.models.proposal import ProposalDraft, ProposalSection
from app.services import proposal_repository as repo
from app.services.proposal_draft_archives import (
    REASON_BEFORE_RESET,
    REASON_BEFORE_SECTIONS_1_3_REGEN,
    archive_filled_draft,
    draft_has_filled_content,
)


def _section(sid: str, content: str = "") -> ProposalSection:
    return ProposalSection(id=sid, title=sid, content=content)


def _draft(rfp_id: str, *, content: str = "Filled prose") -> ProposalDraft:
    return ProposalDraft(
        rfpId=rfp_id,
        sections=[_section("section-1-who-we-are", content)],
        updatedAt="2026-07-21T00:00:00+00:00",
    )


class ManuscriptDurabilityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._db = Path(self._tmpdir.name) / "durability.db"
        self._patchers = [
            patch.object(config.settings, "database_path", self._db),
            patch.object(repo, "_use_supabase", return_value=False),
            patch(
                "app.services.rfp_repository._use_supabase", return_value=False
            ),
            patch(
                "app.services.supabase_db.use_supabase_db", return_value=False
            ),
        ]
        for p in self._patchers:
            p.start()
        repo.init_proposal_db()

    async def asyncTearDown(self) -> None:
        for p in reversed(self._patchers):
            p.stop()
        self._tmpdir.cleanup()

    async def test_archive_and_restore_round_trip(self) -> None:
        rfp_id = "rfp-durability-1"
        draft = _draft(rfp_id)
        await repo.asave_proposal_draft(draft)

        archive_id = await archive_filled_draft(
            draft,
            reason=REASON_BEFORE_SECTIONS_1_3_REGEN,
            label="Before regen",
        )
        self.assertIsNotNone(archive_id)
        metas = await repo.alist_proposal_draft_archives(rfp_id)
        self.assertEqual(len(metas), 1)
        self.assertEqual(metas[0]["filled_count"], 1)

        await repo.adelete_proposal_draft(rfp_id)
        self.assertIsNone(await repo.aget_proposal_draft(rfp_id))

        restored = await repo.arestore_proposal_draft_archive(
            rfp_id, archive_id or ""
        )
        self.assertTrue(draft_has_filled_content(restored))
        self.assertIn("Filled prose", restored.sections[0].content)

    async def test_empty_draft_not_archived(self) -> None:
        draft = _draft("rfp-empty", content="")
        archive_id = await archive_filled_draft(
            draft, reason=REASON_BEFORE_RESET, label="noop"
        )
        self.assertIsNone(archive_id)
        self.assertEqual(await repo.alist_proposal_draft_archives("rfp-empty"), [])

    async def test_force_regenerate_source_never_deletes(self) -> None:
        src_path = (
            Path(__file__).resolve().parent
            / "app"
            / "services"
            / "proposal_generator.py"
        )
        src = src_path.read_text(encoding="utf-8")
        self.assertIn("soft — no draft delete", src)
        self.assertNotIn("await adelete_proposal_draft(rfp_id)", src)

    async def test_force_regenerate_archives_filled_draft(self) -> None:
        from app.services import proposal_generator as gen

        rfp_id = "rfp-soft-regen"
        existing = _draft(rfp_id)
        archive_mock = AsyncMock(return_value="arch-1")

        with (
            patch.object(gen.llm, "is_configured", return_value=True),
            patch.object(
                gen,
                "_load_rfp_for_proposal",
                return_value=(object(), object(), object()),
            ),
            patch.object(
                gen, "aget_proposal_draft", new=AsyncMock(return_value=existing)
            ),
            patch(
                "app.services.proposal_draft_archives.archive_filled_draft",
                archive_mock,
            ),
            patch.object(
                gen,
                "aget_research_cache",
                new=AsyncMock(side_effect=RuntimeError("stop-after-archive")),
            ),
        ):
            with self.assertRaises(RuntimeError):
                await gen.generate_sections_1_3(rfp_id, force_regenerate=True)

        archive_mock.assert_awaited()
        kwargs = archive_mock.await_args.kwargs
        self.assertEqual(kwargs.get("reason"), REASON_BEFORE_SECTIONS_1_3_REGEN)


if __name__ == "__main__":
    unittest.main()
