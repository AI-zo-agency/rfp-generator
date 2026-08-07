"""A user's Key Persona picks must survive generation rebuilding the draft.

Observed: user selected 3 personas, then generated. The toolbar badge reset to
"Key Personas 0" and Section 2 contained a single bio instead of three.

Cause: generation constructs ProposalDraft from scratch at several points
(sections 1-3, phase-3 partials, budget merge) and none passed
selectedKeyPersonas, so it defaulted to [] and was wiped on the first save.
With the picks gone, _select_team fell through to the LLM roster-matching agent
which chose one person.
"""

from __future__ import annotations

import unittest
from unittest import mock

from app.models.proposal import ProposalDraft
from app.services import proposal_repository as repo


def _draft(personas=None) -> ProposalDraft:
    kwargs = {
        "rfpId": "rfp-1",
        "sections": [],
        "updatedAt": "2026-01-01T00:00:00Z",
        "generatedAt": "2026-01-01T00:00:00Z",
    }
    if personas is not None:
        kwargs["selectedKeyPersonas"] = personas
    return ProposalDraft(**kwargs)


class PreserveKeyPersonasTests(unittest.TestCase):
    def test_rebuild_without_personas_keeps_existing(self) -> None:
        existing = _draft(["p1", "p2", "p3"])
        rebuilt = _draft()  # generation omits field → None
        self.assertIsNone(rebuilt.selected_key_personas)

        with mock.patch.object(repo, "get_proposal_draft", return_value=existing):
            repo._preserve_selected_key_personas(rebuilt)

        self.assertEqual(rebuilt.selected_key_personas, ["p1", "p2", "p3"])

    def test_explicit_empty_clear_is_not_restored(self) -> None:
        existing = _draft(["p1", "p2", "p3"])
        cleared = _draft([])  # user Clear / Reset

        with mock.patch.object(repo, "get_proposal_draft", return_value=existing):
            repo._preserve_selected_key_personas(cleared)

        self.assertEqual(cleared.selected_key_personas, [])

    def test_explicit_selection_is_not_overwritten(self) -> None:
        existing = _draft(["old"])
        incoming = _draft(["new1", "new2"])

        with mock.patch.object(repo, "get_proposal_draft", return_value=existing):
            repo._preserve_selected_key_personas(incoming)

        self.assertEqual(incoming.selected_key_personas, ["new1", "new2"])

    def test_no_existing_draft_is_safe(self) -> None:
        incoming = ProposalDraft(
            rfpId="rfp-1",
            sections=[],
            updatedAt="2026-01-01T00:00:00Z",
        )
        self.assertIsNone(incoming.selected_key_personas)
        with mock.patch.object(repo, "get_proposal_draft", return_value=None):
            repo._preserve_selected_key_personas(incoming)
        self.assertIsNone(incoming.selected_key_personas)

    def test_lookup_failure_never_blocks_a_save(self) -> None:
        incoming = ProposalDraft(
            rfpId="rfp-1",
            sections=[],
            updatedAt="2026-01-01T00:00:00Z",
        )
        with mock.patch.object(
            repo, "get_proposal_draft", side_effect=RuntimeError("db down")
        ):
            repo._preserve_selected_key_personas(incoming)  # must not raise
        self.assertIsNone(incoming.selected_key_personas)

    def test_save_path_applies_the_guard(self) -> None:
        existing = _draft(["p1", "p2", "p3"])
        rebuilt = ProposalDraft(
            rfpId="rfp-1",
            sections=[],
            updatedAt="2026-01-01T00:00:00Z",
            generatedAt="2026-01-01T00:00:00Z",
        )

        with mock.patch.object(repo, "get_proposal_draft", return_value=existing), \
             mock.patch.object(repo, "_use_supabase", return_value=True), \
             mock.patch.object(repo, "_with_supabase_retry", return_value=None):
            repo.save_proposal_draft(rebuilt)

        self.assertEqual(rebuilt.selected_key_personas, ["p1", "p2", "p3"])


class TeamSelectionFromPersonasTests(unittest.TestCase):
    def test_all_selected_personas_become_team_members(self) -> None:
        from app.services.proposal_sections_graph import _team_selection_from_personas

        selection = _team_selection_from_personas([
            {"id": "1", "name": "Sonja Anderson", "title": "Principal"},
            {"id": "2", "name": "Shawn DiCriscio", "title": "Web Developer"},
            {"id": "3", "name": "Curt Schultz", "title": "Creative Director"},
        ])

        self.assertEqual(len(selection.members), 3)
        self.assertEqual(
            [m.name for m in selection.members],
            ["Sonja Anderson", "Shawn DiCriscio", "Curt Schultz"],
        )


if __name__ == "__main__":
    unittest.main()
