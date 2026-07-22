"""Proposal draft snapshots: keep chat content, prune restore spam."""

from __future__ import annotations

import unittest

from app.models.proposal import ProposalDraft, ProposalSection
from app.services.proposal_draft_snapshots import (
    filled_count,
    prune_clutter_snapshots,
    push_after_section_edit_snapshot,
    push_proposal_snapshot,
    restore_proposal_snapshot,
)


def _section(sid: str, content: str = "") -> ProposalSection:
    return ProposalSection(id=sid, title=sid, content=content, source="rfp")


def _draft(*sections: ProposalSection) -> ProposalDraft:
    return ProposalDraft(
        rfpId="rfp-snap",
        sections=list(sections),
        updatedAt="2026-07-22T00:00:00+00:00",
    )


class ProposalDraftSnapshotTests(unittest.TestCase):
    def test_after_chat_snapshot_keeps_improved_content(self) -> None:
        draft = _draft(_section("form-2"), _section("qual", "Old quals"))
        improved = draft.model_copy(
            update={
                "sections": [
                    _section("form-2", "Chat filled Form 2"),
                    _section("qual", "Old quals"),
                ]
            }
        )
        improved = push_after_section_edit_snapshot(
            improved, section_title="Form 2 — Proposal Submission Form"
        )
        latest = (improved.snapshots or [])[-1]
        self.assertIn("Saved after chat", latest.label)
        form2 = next(s for s in latest.sections if s.id == "form-2")
        self.assertIn("Chat filled Form 2", form2.content)

    def test_prune_removes_before_restore_and_before_chat_spam(self) -> None:
        draft = _draft(_section("form-2", "Live content"))
        # Simulate legacy spam already stored on the draft.
        from app.models.proposal import ProposalDraftSnapshot

        spam = [
            ProposalDraftSnapshot(
                savedAt="2026-07-22T10:49:00+00:00",
                label="Before restore — current",
                sections=[_section("form-2", "Live content")],
            ),
            ProposalDraftSnapshot(
                savedAt="2026-07-22T10:49:01+00:00",
                label="Before restore — current",
                sections=[_section("form-2", "Live content")],
            ),
            ProposalDraftSnapshot(
                savedAt="2026-07-22T10:38:00+00:00",
                label="Before chat edit — Form 2 - Proposal Submission Form",
                sections=[_section("form-2", "")],
            ),
            ProposalDraftSnapshot(
                savedAt="2026-07-22T09:00:00+00:00",
                label="Before Scan RFP",
                sections=[_section("form-2", "Live content")],
            ),
        ]
        draft = draft.model_copy(update={"snapshots": spam})
        cleaned = prune_clutter_snapshots(draft)
        labels = [s.label for s in cleaned.snapshots or []]
        self.assertEqual(labels, ["Before Scan RFP"])
        self.assertTrue(
            all("before chat edit" not in (l or "").lower() for l in labels)
        )

    def test_prune_all_junk_keeps_saved_draft_checkpoint(self) -> None:
        from app.models.proposal import ProposalDraftSnapshot

        draft = _draft(_section("form-2", "Chat filled Form 2"))
        draft = draft.model_copy(
            update={
                "snapshots": [
                    ProposalDraftSnapshot(
                        savedAt="2026-07-22T10:38:00+00:00",
                        label="Before chat edit — Form 2",
                        sections=[_section("form-2", "")],
                    )
                ]
            }
        )
        cleaned = prune_clutter_snapshots(draft)
        labels = [s.label for s in cleaned.snapshots or []]
        self.assertEqual(labels, ["Saved draft"])
        self.assertIn(
            "Chat filled Form 2",
            next(s for s in (cleaned.snapshots or [])[0].sections if s.id == "form-2").content,
        )

    def test_restore_does_not_require_before_restore_row(self) -> None:
        draft = _draft(_section("form-2", "Chat filled Form 2"))
        draft = push_after_section_edit_snapshot(draft, section_title="Form 2")
        saved_at = (draft.snapshots or [])[-1].saved_at
        emptied = draft.model_copy(
            update={"sections": [_section("form-2", "")]}
        )
        restored = restore_proposal_snapshot(emptied, saved_at=saved_at)
        assert restored is not None
        self.assertIn(
            "Chat filled Form 2",
            next(s for s in restored.sections if s.id == "form-2").content,
        )

    def test_restore_rejects_empty_snapshot_bodies(self) -> None:
        from app.models.proposal import ProposalDraftSnapshot

        draft = _draft(_section("form-2", "Live"))
        draft = draft.model_copy(
            update={
                "snapshots": [
                    ProposalDraftSnapshot(
                        savedAt="2026-07-22T11:09:00+00:00",
                        label="Saved after chat — Form 2",
                        sections=[],
                    )
                ]
            }
        )
        self.assertIsNone(
            restore_proposal_snapshot(
                draft, saved_at="2026-07-22T11:09:00+00:00"
            )
        )

    def test_filled_count(self) -> None:
        draft = _draft(_section("a", "x"), _section("b", ""))
        self.assertEqual(filled_count(draft), 1)


if __name__ == "__main__":
    unittest.main()
