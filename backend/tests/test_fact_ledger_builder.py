"""Tests for Fact Ledger schema + conflict-aware builder (W4 T4.1–T4.2)."""

from __future__ import annotations

import unittest

from app.models.fact_ledger import ClaimClass, FactLedger, LedgerClaim
from app.services.fact_ledger_builder import build_fact_ledger


def _years_claim(
    *,
    claim_id: str,
    subject_id: str,
    years: float,
    snippet: str,
) -> LedgerClaim:
    return LedgerClaim(
        claimId=claim_id,
        claimClass=ClaimClass.YEARS_EXPERIENCE,
        subjectType="person",
        subjectId=subject_id,
        fieldName="years_experience",
        valueText=f"{years:g} years",
        valueNumber=years,
        unit="years",
        sourceDoc="bio",
        sourceLocator=claim_id,
        verbatimSnippet=snippet,
        confidence=0.9,
    )


class FactLedgerSchemaTests(unittest.TestCase):
    def test_round_trip_empty_ledger(self) -> None:
        ledger = FactLedger(version="1", builtAt="2026-07-28T00:00:00Z")
        raw = ledger.model_dump(by_alias=True)
        again = FactLedger.model_validate(raw)
        self.assertEqual(again.version, "1")
        self.assertEqual(again.blocking_conflicts, [])
        self.assertEqual(again.claims, [])

    def test_v1_claim_classes_only(self) -> None:
        expected = {
            "budget",
            "years_experience",
            "employee_count",
            "certification",
            "date",
            "contract_value",
            "retention_stat",
        }
        self.assertEqual({c.value for c in ClaimClass}, expected)


class FactLedgerBuilderConflictTests(unittest.TestCase):
    def test_same_person_two_year_values_is_blocking_conflict(self) -> None:
        """GSU-style: Ron Comer 35 vs 38 — never silently collapse."""
        claims = [
            _years_claim(
                claim_id="c35",
                subject_id="person:ron-comer",
                years=35,
                snippet="Ron Comer — 35+ years",
            ),
            _years_claim(
                claim_id="c38",
                subject_id="person:ron-comer",
                years=38,
                snippet="Ron Comer brings 38 years",
            ),
        ]
        ledger = build_fact_ledger(
            version="test-1",
            built_at="2026-07-28T00:00:00Z",
            claims=claims,
            people_names={"person:ron-comer": "Ron Comer"},
        )
        self.assertEqual(len(ledger.claims), 2)
        self.assertTrue(ledger.blocking_conflicts)
        joined = " ".join(ledger.blocking_conflicts).lower()
        self.assertIn("ron comer", joined)
        self.assertIn("years_experience", joined)
        # Both claims retained — no silent winner
        nums = sorted(
            c.value_number for c in ledger.claims if c.value_number is not None
        )
        self.assertEqual(nums, [35.0, 38.0])

    def test_matching_year_claims_no_conflict(self) -> None:
        claims = [
            _years_claim(
                claim_id="c1",
                subject_id="person:ron-comer",
                years=35,
                snippet="35 years",
            ),
            _years_claim(
                claim_id="c2",
                subject_id="person:ron-comer",
                years=35,
                snippet="35+ years",
            ),
        ]
        ledger = build_fact_ledger(
            version="test-2",
            built_at="2026-07-28T00:00:00Z",
            claims=claims,
            people_names={"person:ron-comer": "Ron Comer"},
        )
        self.assertEqual(ledger.blocking_conflicts, [])
        self.assertEqual(len(ledger.people), 1)
        self.assertEqual(ledger.people[0].name, "Ron Comer")

    def test_different_people_same_years_no_conflict(self) -> None:
        claims = [
            _years_claim(
                claim_id="a",
                subject_id="person:a",
                years=20,
                snippet="A — 20 years",
            ),
            _years_claim(
                claim_id="b",
                subject_id="person:b",
                years=20,
                snippet="B — 20 years",
            ),
        ]
        ledger = build_fact_ledger(
            version="test-3",
            built_at="2026-07-28T00:00:00Z",
            claims=claims,
            people_names={"person:a": "Alice", "person:b": "Bob"},
        )
        self.assertEqual(ledger.blocking_conflicts, [])
        self.assertEqual(len(ledger.people), 2)

    def test_employee_count_conflict(self) -> None:
        claims = [
            LedgerClaim(
                claimId="e1",
                claimClass=ClaimClass.EMPLOYEE_COUNT,
                subjectType="company",
                subjectId="company:zo",
                fieldName="employee_count",
                valueText="12",
                valueNumber=12,
                sourceDoc="about",
                verbatimSnippet="12 employees",
            ),
            LedgerClaim(
                claimId="e2",
                claimClass=ClaimClass.EMPLOYEE_COUNT,
                subjectType="company",
                subjectId="company:zo",
                fieldName="employee_count",
                valueText="18",
                valueNumber=18,
                sourceDoc="pitch",
                verbatimSnippet="team of 18",
            ),
        ]
        ledger = build_fact_ledger(
            version="test-4",
            built_at="2026-07-28T00:00:00Z",
            claims=claims,
        )
        self.assertTrue(any("employee_count" in c for c in ledger.blocking_conflicts))


if __name__ == "__main__":
    unittest.main()
