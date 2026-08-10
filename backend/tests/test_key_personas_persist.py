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

    def test_four_user_picks_survive_bio_normalize_including_shared_surname(self) -> None:
        """Regression: 4 Key Personas must become 4 bios — last-name dedupe is LLM-only."""
        from app.services.company_qualification.agents.team_selection import (
            normalize_selected_members,
        )
        from app.services.proposal_sections_graph import _team_selection_from_personas

        personas = [
            {"id": "sonja-anderson", "name": "Sonja Anderson", "title": "Principal"},
            {"id": "ron-comer", "name": "Ron Comer", "title": "Account Lead"},
            {"id": "justin-bronson", "name": "Justin Bronson", "title": "Developer"},
            {"id": "todd-anderson", "name": "Todd Anderson", "title": "Strategist"},
        ]
        selection = _team_selection_from_personas(personas)
        self.assertEqual(len(selection.members), 4)

        # Skill-based path would collapse the two Andersons — user picks must not.
        collapsed = normalize_selected_members([m.name for m in selection.members])
        self.assertEqual(len(collapsed), 3)

        kept = normalize_selected_members(
            [m.name for m in selection.members],
            max_members=max(5, len(selection.members)),
            dedupe_by_last_name=False,
        )
        self.assertEqual(len(kept), 4)
        self.assertEqual(
            kept,
            ["Sonja Anderson", "Ron Comer", "Justin Bronson", "Todd Anderson"],
        )

    def test_missing_kb_persona_id_is_stubbed_not_dropped(self) -> None:
        import asyncio
        from unittest.mock import AsyncMock, patch

        from app.models.proposal import ProposalDraft
        from app.services import proposal_sections_graph as graph

        draft = ProposalDraft(
            rfpId="rfp-x",
            sections=[],
            updatedAt="2026-01-01T00:00:00Z",
            selectedKeyPersonas=[
                "sonja-anderson",
                "ron-comer",
                "justin-bronson",
                "missing-person",
            ],
        )
        known = [
            {"id": "sonja-anderson", "name": "Sonja Anderson", "title": "CEO"},
            {"id": "ron-comer", "name": "Ron Comer", "title": "AM"},
            {"id": "justin-bronson", "name": "Justin Bronson", "title": "Dev"},
        ]

        async def _run() -> list:
            with patch.object(graph, "aget_proposal_draft", AsyncMock(return_value=draft)), \
                 patch.object(
                     graph.team_personas_service,
                     "get_all_key_personas",
                     AsyncMock(return_value=known),
                 ):
                return await graph._selected_key_personas_for_rfp("rfp-x")

        resolved = asyncio.run(_run())
        self.assertEqual(len(resolved), 4)
        self.assertEqual(resolved[3]["id"], "missing-person")
        self.assertEqual(resolved[3]["name"], "Missing Person")


if __name__ == "__main__":
    unittest.main()
