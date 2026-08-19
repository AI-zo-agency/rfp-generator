"""RFP-agnostic money constraint extraction and ledger comparison."""

from __future__ import annotations

import unittest

from app.models.proposal import BudgetLineItem, ProposalBudget
from app.services.evidence_trust.rfp_money_constraints import (
    CONSTRAINT_HARD_FEE_NTE,
    CONSTRAINT_PROGRAM_OR_MEDIA_ENVELOPE,
    apply_constraints_to_budget_fields,
    collect_invented_ceiling_mismatches,
    collect_over_authority_flags,
    extract_rfp_money_constraints,
    primary_hard_fee_nte,
    primary_program_media_envelope,
)


ADVERTISING_ENVELOPE_SNIPPET = """
Section VI — Costs. The University is allocating up to $95,000 for program-specific
digital advertising for the initial term. Proposals should separate professional
service fees from paid media expenditures.
"""

HARD_NTE_SNIPPET = """
Section 2.4 Compensation. The contract is a fixed-price ceiling of $2,950,000.
Compensation shall not exceed this amount for the base period.
"""

TUITION_NOISE_SNIPPET = """
Undergraduate tuition is approximately $12K per year for in-state students.
No contract ceiling is stated for professional services.
"""


def _budget(
    *,
    fee: float,
    media: float = 0.0,
    rfp_id: str = "fixture-money",
) -> ProposalBudget:
    items = [
        BudgetLineItem(
            id="L1",
            category="Strategy",
            description="Agency fees",
            extended=fee,
            lineItemType="agency_fee",
        ),
    ]
    if media > 0:
        items.append(
            BudgetLineItem(
                id="M1",
                category="Media",
                description="Paid media pass-through",
                extended=media,
                lineItemType="client_passthrough",
            )
        )
    total = fee + media
    return ProposalBudget(
        rfpId=rfp_id,
        updatedAt="2026-08-01T00:00:00Z",
        lineItems=items,
        agencyRevenueEstimate=fee,
        agencyFeeSubtotal=fee,
        clientMediaPassthrough=media,
        totalClientInvoicing=total,
        lineItemSum=total,
        lumpSumTotal=fee,
    )


class ExtractMoneyConstraintsTests(unittest.TestCase):
    def test_extracts_program_media_envelope(self) -> None:
        constraints = extract_rfp_money_constraints(ADVERTISING_ENVELOPE_SNIPPET)
        env = primary_program_media_envelope(constraints)
        self.assertIsNotNone(env)
        assert env is not None
        self.assertEqual(env.kind, CONSTRAINT_PROGRAM_OR_MEDIA_ENVELOPE)
        self.assertAlmostEqual(env.amount, 95_000.0, places=2)

    def test_extracts_hard_fee_nte(self) -> None:
        constraints = extract_rfp_money_constraints(HARD_NTE_SNIPPET)
        nte = primary_hard_fee_nte(constraints)
        self.assertIsNotNone(nte)
        assert nte is not None
        self.assertEqual(nte.kind, CONSTRAINT_HARD_FEE_NTE)
        self.assertAlmostEqual(nte.amount, 2_950_000.0, places=2)

    def test_tuition_not_authority(self) -> None:
        constraints = extract_rfp_money_constraints(TUITION_NOISE_SNIPPET)
        self.assertIsNone(primary_hard_fee_nte(constraints))
        self.assertIsNone(primary_program_media_envelope(constraints))

    def test_year1_budget_under_100k_is_hard_nte(self) -> None:
        text = (
            "Section IV. Year 1 budget is $68,200. Proposals shall not exceed "
            "this amount. Years 2–3 budget approximately $50,000 per year "
            "subject to annual review."
        )
        constraints = extract_rfp_money_constraints(text)
        nte = primary_hard_fee_nte(constraints)
        self.assertIsNotNone(nte)
        assert nte is not None
        self.assertAlmostEqual(nte.amount, 68_200.0, places=2)
        self.assertFalse(
            any(
                c.kind == CONSTRAINT_HARD_FEE_NTE and abs(c.amount - 50_000.0) < 1
                for c in constraints
            ),
            constraints,
        )

    def test_do_not_exceed_available_funds(self) -> None:
        text = (
            "Available funds for this engagement shall not exceed $68,200. "
            "Do not go above this budget."
        )
        constraints = extract_rfp_money_constraints(text)
        nte = primary_hard_fee_nte(constraints)
        self.assertIsNotNone(nte)
        assert nte is not None
        self.assertAlmostEqual(nte.amount, 68_200.0, places=2)

    def test_apply_sets_budget_fields(self) -> None:
        budget = _budget(fee=80_000, media=30_000)
        constraints = extract_rfp_money_constraints(ADVERTISING_ENVELOPE_SNIPPET)
        updated = apply_constraints_to_budget_fields(budget, constraints)
        self.assertAlmostEqual(float(updated.rfp_media_or_program_envelope or 0), 95_000.0)
        self.assertTrue(updated.rfp_money_constraint_notes)


class OverAuthorityAndInventedCeilingTests(unittest.TestCase):
    def test_over_program_envelope_flags_total_invoicing(self) -> None:
        budget = _budget(fee=80_000, media=30_000)  # 110k > 95k
        constraints = extract_rfp_money_constraints(ADVERTISING_ENVELOPE_SNIPPET)
        budget = apply_constraints_to_budget_fields(budget, constraints)
        flags = collect_over_authority_flags(budget)
        self.assertTrue(any("DISQUALIFY" in f or "exceeds" in f.lower() for f in flags), flags)

    def test_under_envelope_no_flag(self) -> None:
        budget = _budget(fee=50_000, media=20_000)
        constraints = extract_rfp_money_constraints(ADVERTISING_ENVELOPE_SNIPPET)
        budget = apply_constraints_to_budget_fields(budget, constraints)
        flags = collect_over_authority_flags(budget)
        self.assertFalse(flags)

    def test_invented_ceiling_matching_bid_total(self) -> None:
        budget = _budget(fee=80_000, media=30_000)
        constraints = extract_rfp_money_constraints(ADVERTISING_ENVELOPE_SNIPPET)
        budget = apply_constraints_to_budget_fields(budget, constraints)
        prose = (
            "The total sits within the University's $110,000 ceiling for this engagement."
        )
        hits = collect_invented_ceiling_mismatches(
            prose,
            budget=budget,
            section_id="pricing",
            section_title="Pricing",
        )
        self.assertTrue(hits)
        self.assertIn("110,000", hits[0].sentence or hits[0].note)


if __name__ == "__main__":
    unittest.main()
