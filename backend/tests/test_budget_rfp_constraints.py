"""A budget must not price work the RFP forbids.

Observed: $2,500 of travel in an RFP stating all work shall be performed remotely.

Fixture note: the brief's ``_budget`` helper (``BudgetLineItem(description=d,
extended=x)`` / ``ProposalBudget(lineItems=[...])``) does not construct
against the real Pydantic models — ``BudgetLineItem.id`` / ``.category`` and
``ProposalBudget.rfp_id`` (``rfpId``) / ``.updated_at`` (``updatedAt``) are
required fields with no default (see app/models/proposal.py). Fixed here to
supply them, following the same pattern as
tests/test_budget_underbid_floor.py; no assertion below was changed from the
brief.
"""

from __future__ import annotations

import unittest

from app.models.proposal import BudgetLineItem, ProposalBudget
from app.services.proposal_budget_floor import collect_rfp_constraint_violations

REMOTE_RFP = (
    "2.7 Location of Work. All work under this agreement shall be performed remotely. "
    "No on-site presence is anticipated unless requested by MSU Denver."
)
ONSITE_RFP = "The selected firm shall attend monthly on-site stakeholder meetings in Denver."


def _line(item_id: str, description: str, extended: float) -> BudgetLineItem:
    return BudgetLineItem(
        id=item_id,
        category="Direct Expense",
        description=description,
        extended=extended,
    )


def _budget(*pairs: tuple[str, float]) -> ProposalBudget:
    return ProposalBudget(
        rfpId="rfp-constraint-test",
        updatedAt="2026-08-05T00:00:00+00:00",
        lineItems=[
            _line(f"L{i + 1:02d}", description, extended)
            for i, (description, extended) in enumerate(pairs)
        ],
    )


class RfpConstraintTests(unittest.TestCase):
    def test_travel_in_a_remote_only_engagement_is_rejected(self) -> None:
        violations = collect_rfp_constraint_violations(_budget(("Travel — site visits", 2500)), REMOTE_RFP)
        self.assertTrue(violations)
        self.assertIn("remote", violations[0].lower())

    def test_travel_is_fine_when_the_rfp_requires_on_site_work(self) -> None:
        self.assertEqual(
            collect_rfp_constraint_violations(_budget(("Travel — site visits", 2500)), ONSITE_RFP), []
        )

    def test_non_travel_lines_are_unaffected_by_a_remote_clause(self) -> None:
        self.assertEqual(
            collect_rfp_constraint_violations(_budget(("Strategy & creative", 14000)), REMOTE_RFP), []
        )

    def test_empty_rfp_text_is_not_a_violation(self) -> None:
        self.assertEqual(collect_rfp_constraint_violations(_budget(("Travel", 2500)), ""), [])

    def test_all_travel_vocabulary_is_caught(self) -> None:
        for text in ("Airfare", "Lodging", "Per diem", "Mileage"):
            with self.subTest(text=text):
                self.assertTrue(collect_rfp_constraint_violations(_budget((text, 500)), REMOTE_RFP))

    # --- False-positive hardening -----------------------------------------
    # Every case below is a real phrasing that must NOT halt a correctly
    # priced budget — see proposal_budget_floor.py's _REMOTE_ONLY_RE /
    # _ONSITE_REQUIRED_RE docstring for the reasoning.

    def test_incidental_remote_mentions_do_not_suppress_travel(self) -> None:
        for text in (
            "Vendor shall provide remote desktop support for the client's IT staff.",
            "The engagement includes analysis of remote sensing data for the corridor study.",
            "There is the remote possibility that on-site work is requested later.",
        ):
            with self.subTest(text=text):
                self.assertEqual(
                    collect_rfp_constraint_violations(_budget(("Travel", 2500)), text), []
                )

    def test_remote_clause_with_on_site_kickoff_carve_out_does_not_flag_travel(self) -> None:
        text = (
            "All work under this agreement shall be performed remotely, however an "
            "on-site kickoff meeting is required at contract start."
        )
        self.assertEqual(collect_rfp_constraint_violations(_budget(("Travel", 2500)), text), [])

    def test_remote_clause_with_quarterly_on_site_review_does_not_flag_travel(self) -> None:
        text = (
            "All work under this agreement shall be performed remotely. On-site presence "
            "is required for the quarterly business review."
        )
        self.assertEqual(collect_rfp_constraint_violations(_budget(("Travel", 2500)), text), [])

    def test_hybrid_primarily_remote_clause_does_not_flag_travel(self) -> None:
        text = "Work is primarily remote, with occasional on-site meetings as needed."
        self.assertEqual(collect_rfp_constraint_violations(_budget(("Travel", 2500)), text), [])

    def test_missing_rfp_text_never_halts(self) -> None:
        self.assertEqual(collect_rfp_constraint_violations(_budget(("Travel", 2500)), None), [])  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
