"""The gate loop: every ticket terminates, nothing exits silently.

Each ticket must end as fixed, manual_fill, reverted, or unfixed — and the ones that
were not fixed must appear in the convergence report. That invariant is what makes
"nothing left behind" checkable rather than asserted.
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app.models.proposal import (
    ClaimVerdict,
    CriterionVerdict,
    GateTicket,
    ProposalDraft,
    ProposalSection,
)
from app.models.rfp import RfpRecord
from app.services import proposal_quality_gate as gate

LONG = "This is the original section content that is long enough to survive shrink checks. " * 3


def _draft() -> ProposalDraft:
    return ProposalDraft(
        rfpId="r1",
        sections=[ProposalSection(id="s1", title="Approach", content=LONG)],
        updatedAt="2026-08-13T00:00:00Z",
    )


def _rfp() -> RfpRecord:
    return RfpRecord(
        id="r1",
        title="RFP",
        client="Acme",
        due_date="2026-09-01",
        received_date="2026-08-01",
        last_activity="2026-08-13",
        last_activity_note="scan",
    )


def _ticket(**kw) -> GateTicket:
    base = dict(sectionId="s1", code="slop.filler", detector="slop", message="filler")
    base.update(kw)
    return GateTicket(**base)


class GateLoopTests(unittest.IsolatedAsyncioTestCase):
    async def _run(
        self,
        *,
        tickets_per_round: list[list[GateTicket]],
        patch_result: tuple[str, str] | list[tuple[str, str]] = ("PATCHED " + LONG, "patched"),
        claims: list[ClaimVerdict] | None = None,
        scorecard: list[CriterionVerdict] | None = None,
    ):
        rounds = iter(tickets_per_round + [[]] * 10)
        side = patch_result if isinstance(patch_result, list) else None
        with (
            patch.object(gate, "verify_fact_bound_claims", new=AsyncMock(return_value=claims or [])),
            patch.object(gate, "evaluate_against_criteria", new=AsyncMock(return_value=scorecard or [])),
            patch.object(gate, "detect_quality_tickets", new=AsyncMock(side_effect=lambda **_k: next(rounds))),
            patch.object(
                gate,
                "_patch_section",
                new=AsyncMock(side_effect=side) if side else AsyncMock(return_value=patch_result),
            ),
        ):
            return await gate.run_quality_gate(
                rfp=_rfp(), draft=_draft(), research=None, rfp_text="RFP text"
            )

    async def test_clean_manuscript_stops_in_one_round(self):
        _draft_out, report = await self._run(tickets_per_round=[[]])
        self.assertEqual(report.rounds_run, 1)
        self.assertIn("no new findings", report.stopped_reason)
        self.assertEqual(report.tickets, [])

    async def test_successful_patch_is_applied_and_recorded(self):
        draft_out, report = await self._run(tickets_per_round=[[_ticket()]])
        self.assertEqual(report.tickets[0].outcome, "fixed")
        self.assertTrue(draft_out.sections[0].content.startswith("PATCHED"))
        self.assertTrue(report.changes)

    async def test_regressing_patch_is_reverted_not_committed(self):
        """A repair loop with no revert path can only ratchet downward."""
        draft_out, report = await self._run(
            tickets_per_round=[[_ticket()]], patch_result=("tiny", "patched")
        )
        self.assertEqual(report.tickets[0].outcome, "reverted")
        self.assertEqual(draft_out.sections[0].content, LONG)

    async def test_no_evidence_becomes_manual_fill_not_an_invented_claim(self):
        draft_out, report = await self._run(
            tickets_per_round=[[_ticket(requiresEvidence=True)]],
            patch_result=(LONG + "\n\n[MANUAL FILL: filler]", "no evidence — emitted MANUAL FILL"),
        )
        self.assertEqual(report.tickets[0].outcome, "manual_fill")
        self.assertIn("[MANUAL FILL", draft_out.sections[0].content)

    async def test_same_ticket_is_never_reopened(self):
        _d, report = await self._run(tickets_per_round=[[_ticket()], [_ticket()]])
        self.assertEqual(len(report.tickets), 1)

    async def test_loop_stops_at_three_rounds(self):
        """Each round must genuinely change the text, or the cheaper stop fires first."""
        rounds = [[_ticket(code=f"slop.r{i}")] for i in range(10)]
        _d, report = await self._run(
            tickets_per_round=rounds,
            patch_result=[(f"REV{i} " + LONG, "patched") for i in range(10)],
        )
        self.assertEqual(report.rounds_run, gate.MAX_ROUNDS)
        self.assertIn("3-round limit", report.stopped_reason)

    async def test_a_round_that_changes_nothing_stops_early(self):
        """Re-detecting unchanged text would buy the same answer twice."""
        _d, report = await self._run(
            tickets_per_round=[[_ticket(code="a")], [_ticket(code="b")]],
            patch_result=(LONG, "patched"),
        )
        self.assertIn("no sections changed", report.stopped_reason)
        self.assertLess(report.rounds_run, gate.MAX_ROUNDS)

    async def test_later_rounds_only_re_detect_changed_sections(self):
        """The cost control: round 2 must not re-scan the whole manuscript."""
        seen_scopes: list[set[str] | None] = []
        rounds = iter([[_ticket(code="a")], [_ticket(code="b")], [], []])

        async def _detect(**kw):
            seen_scopes.append(kw.get("only_sections"))
            return next(rounds)

        with (
            patch.object(gate, "verify_fact_bound_claims", new=AsyncMock(return_value=[])),
            patch.object(gate, "evaluate_against_criteria", new=AsyncMock(return_value=[])),
            patch.object(gate, "detect_quality_tickets", new=AsyncMock(side_effect=_detect)),
            patch.object(
                gate, "_patch_section",
                new=AsyncMock(side_effect=[(f"R{i} " + LONG, "patched") for i in range(5)]),
            ),
        ):
            await gate.run_quality_gate(
                rfp=_rfp(), draft=_draft(), research=None, rfp_text=""
            )
        self.assertIsNone(seen_scopes[0], "round 1 must examine everything")
        self.assertEqual(seen_scopes[1], {"s1"}, "round 2 must scope to edited sections")

    async def test_oscillation_stops_the_loop(self):
        """Round 2 undoing round 1 ends the run instead of burning round 3."""
        _d, report = await self._run(
            tickets_per_round=[[_ticket(code="a")], [_ticket(code="b")]],
            patch_result=[("PATCHED " + LONG, "patched"), (LONG, "patched")],
        )
        self.assertIn("oscillation", report.stopped_reason)

    async def test_patch_exception_is_recorded_not_raised(self):
        with (
            patch.object(gate, "verify_fact_bound_claims", new=AsyncMock(return_value=[])),
            patch.object(gate, "evaluate_against_criteria", new=AsyncMock(return_value=[])),
            patch.object(
                gate, "detect_quality_tickets",
                new=AsyncMock(side_effect=[[_ticket()], [], [], []]),
            ),
            patch.object(gate, "_patch_section", new=AsyncMock(side_effect=RuntimeError("boom"))),
        ):
            _d, report = await gate.run_quality_gate(
                rfp=_rfp(), draft=_draft(), research=None, rfp_text=""
            )
        self.assertEqual(report.tickets[0].outcome, "unfixed")
        self.assertIn("boom", report.tickets[0].detail)

    async def test_unfixed_tickets_reach_the_convergence_report(self):
        _d, report = await self._run(
            tickets_per_round=[[_ticket()]], patch_result=("tiny", "patched")
        )
        self.assertTrue(any("reverted" in line or "regressed" in line for line in report.convergence))

    async def test_unresolved_claims_reach_the_convergence_report(self):
        _d, report = await self._run(
            tickets_per_round=[[]],
            claims=[ClaimVerdict(sectionId="s1", claim="Acme saved 30%", status="unresolved")],
        )
        self.assertTrue(any("Acme saved 30%" in line for line in report.convergence))

    async def test_verified_claims_do_not_pollute_convergence(self):
        _d, report = await self._run(
            tickets_per_round=[[]],
            claims=[ClaimVerdict(sectionId="s1", claim="ok", status="verified")],
        )
        self.assertEqual(report.convergence, [])

    async def test_contradicted_claims_are_reported_as_changes(self):
        _d, report = await self._run(
            tickets_per_round=[[]],
            claims=[
                ClaimVerdict(
                    sectionId="s1", claim="12 staff", status="contradicted",
                    correctedValue="15 staff", evidence="Roster lists 15.",
                )
            ],
        )
        self.assertTrue(any("15 staff" in c for c in report.changes))

    async def test_cancellation_propagates(self):
        async def _stop() -> None:
            raise KeyboardInterrupt("cancelled")

        with (
            patch.object(gate, "verify_fact_bound_claims", new=AsyncMock(return_value=[])),
            patch.object(gate, "evaluate_against_criteria", new=AsyncMock(return_value=[])),
            patch.object(gate, "detect_quality_tickets", new=AsyncMock(return_value=[])),
        ):
            with self.assertRaises(KeyboardInterrupt):
                await gate.run_quality_gate(
                    rfp=_rfp(), draft=_draft(), research=None, rfp_text="",
                    ensure_not_stopped=_stop,
                )

    async def test_every_ticket_terminates_in_a_known_state(self):
        """The core invariant: nothing exits silently."""
        _d, report = await self._run(
            tickets_per_round=[[_ticket(code="a"), _ticket(code="b", sectionId="missing")]]
        )
        self.assertTrue(report.tickets)
        for ticket in report.tickets:
            self.assertIn(ticket.outcome, {"fixed", "manual_fill", "reverted", "unfixed"})

    async def test_tenure_without_kb_rewrites_years_instead_of_banner(self):
        ticket = GateTicket(
            sectionId="section-1-who-we-are",
            code="fact.tenure",
            detector="consistency",
            message="Draft says 12 years of experience; companyfacts is 13",
            guidance="Use 13 years",
            requiresEvidence=True,
        )
        after, note = await gate._patch_section(
            rfp=_rfp(),
            section_id="section-1-who-we-are",
            section_title="Who We Are",
            content=(
                "zö agency combines 12 years of experience with strategy "
                "and storytelling to guide purpose-driven brands."
            ),
            ticket=ticket,
            packed_evidence="",
        )
        self.assertIn("13 years of experience", after)
        self.assertNotIn("MANUAL FILL", after)
        self.assertIn("canonical agency tenure", note)


if __name__ == "__main__":
    unittest.main()
