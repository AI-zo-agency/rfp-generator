"""Tests for Fact Ledger overrides — one authoritative value without cleaning KB."""

from __future__ import annotations

import unittest
from pathlib import Path

from app.models.fact_ledger import ClaimClass, FactLedgerOverride, LedgerClaim
from app.services.fact_ledger_builder import build_fact_ledger
from app.services.fact_ledger_overrides import (
    apply_overrides_to_claims,
    load_fact_ledger_overrides,
)


def _years(claim_id: str, years: float, snippet: str) -> LedgerClaim:
    return LedgerClaim(
        claimId=claim_id,
        claimClass=ClaimClass.YEARS_EXPERIENCE,
        subjectType="person",
        subjectId="person:ron-comer",
        fieldName="years_experience",
        valueText=f"{years:g} years",
        valueNumber=years,
        unit="years",
        sourceDoc="kb",
        verbatimSnippet=snippet,
    )


class ApplyOverridesTests(unittest.TestCase):
    def test_override_collapses_conflict_to_single_value(self) -> None:
        claims = [
            _years("c35", 35, "35+ years"),
            _years("c38", 38, "38 years"),
        ]
        overrides = [
            FactLedgerOverride(
                subjectId="person:ron-comer",
                claimClass=ClaimClass.YEARS_EXPERIENCE,
                fieldName="years_experience",
                valueNumber=35,
                valueText="35+ years",
                unit="years",
                reason="Primary bio is authoritative",
                approvedBy="Sonja",
            )
        ]
        resolved, notes = apply_overrides_to_claims(claims, overrides)
        nums = sorted(
            c.value_number for c in resolved if c.value_number is not None
        )
        self.assertEqual(nums, [35.0])
        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved[0].source_doc, "override")
        self.assertTrue(any("35" in n and "Sonja" in n for n in notes))

    def test_unrelated_claims_preserved(self) -> None:
        claims = [
            _years("c35", 35, "35"),
            _years("c38", 38, "38"),
            LedgerClaim(
                claimId="emp",
                claimClass=ClaimClass.EMPLOYEE_COUNT,
                subjectType="company",
                subjectId="company:zo",
                fieldName="employee_count",
                valueText="12",
                valueNumber=12,
            ),
        ]
        overrides = [
            FactLedgerOverride(
                subjectId="person:ron-comer",
                claimClass=ClaimClass.YEARS_EXPERIENCE,
                fieldName="years_experience",
                valueNumber=35,
                valueText="35 years",
                approvedBy="Sonja",
            )
        ]
        resolved, _notes = apply_overrides_to_claims(claims, overrides)
        emp = [c for c in resolved if c.claim_class == ClaimClass.EMPLOYEE_COUNT]
        self.assertEqual(len(emp), 1)
        self.assertEqual(emp[0].value_number, 12)

    def test_no_override_leaves_conflict_for_builder(self) -> None:
        claims = [_years("c35", 35, "35"), _years("c38", 38, "38")]
        resolved, notes = apply_overrides_to_claims(claims, [])
        self.assertEqual(len(resolved), 2)
        self.assertEqual(notes, [])
        ledger = build_fact_ledger(
            version="v",
            built_at="t",
            claims=resolved,
            people_names={"person:ron-comer": "Ron Comer"},
        )
        self.assertTrue(ledger.blocking_conflicts)

    def test_build_fact_ledger_with_overrides_clears_blocking(self) -> None:
        claims = [_years("c35", 35, "35"), _years("c38", 38, "38")]
        overrides = [
            FactLedgerOverride(
                subjectId="person:ron-comer",
                claimClass=ClaimClass.YEARS_EXPERIENCE,
                fieldName="years_experience",
                valueNumber=35,
                valueText="35+ years",
                approvedBy="Sonja",
            )
        ]
        ledger = build_fact_ledger(
            version="v",
            built_at="t",
            claims=claims,
            people_names={"person:ron-comer": "Ron Comer"},
            overrides=overrides,
        )
        self.assertEqual(ledger.blocking_conflicts, [])
        self.assertTrue(ledger.resolution_notes)
        self.assertEqual(
            [c.value_number for c in ledger.claims if c.value_number is not None],
            [35.0],
        )

    def test_override_without_matching_claims_still_injects_authority(self) -> None:
        """Override can seed a value even when KB only had the wrong number."""
        claims = [_years("c38", 38, "38 years")]
        overrides = [
            FactLedgerOverride(
                subjectId="person:ron-comer",
                claimClass=ClaimClass.YEARS_EXPERIENCE,
                fieldName="years_experience",
                valueNumber=35,
                valueText="35+ years",
                approvedBy="Sonja",
            )
        ]
        ledger = build_fact_ledger(
            version="v",
            built_at="t",
            claims=claims,
            people_names={"person:ron-comer": "Ron Comer"},
            overrides=overrides,
        )
        self.assertEqual(ledger.blocking_conflicts, [])
        self.assertEqual(ledger.claims[0].value_number, 35.0)

    def test_wrong_field_override_does_not_affect_years(self) -> None:
        claims = [_years("c35", 35, "35"), _years("c38", 38, "38")]
        overrides = [
            FactLedgerOverride(
                subjectId="person:ron-comer",
                claimClass=ClaimClass.EMPLOYEE_COUNT,
                fieldName="employee_count",
                valueNumber=12,
                valueText="12",
                approvedBy="Sonja",
            )
        ]
        ledger = build_fact_ledger(
            version="v",
            built_at="t",
            claims=claims,
            people_names={"person:ron-comer": "Ron Comer"},
            overrides=overrides,
        )
        self.assertTrue(ledger.blocking_conflicts)


class LoadOverridesYamlTests(unittest.TestCase):
    def test_loads_packaged_yaml(self) -> None:
        overrides = load_fact_ledger_overrides()
        self.assertIsInstance(overrides, list)
        # Packaged file may be empty list or sample entries
        for item in overrides:
            self.assertIsInstance(item, FactLedgerOverride)

    def test_loads_explicit_path(self) -> None:
        path = Path(__file__).resolve().parents[1] / "app" / "data" / "fact_ledger_overrides.yaml"
        self.assertTrue(path.is_file(), msg=f"missing {path}")
        overrides = load_fact_ledger_overrides(path)
        ron = [
            o
            for o in overrides
            if o.subject_id == "person:ron-comer"
            and o.field_name == "years_experience"
        ]
        self.assertEqual(len(ron), 1)
        self.assertEqual(ron[0].value_number, 35.0)


if __name__ == "__main__":
    unittest.main()
