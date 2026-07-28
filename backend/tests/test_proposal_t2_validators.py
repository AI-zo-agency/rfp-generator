"""T2 Fact Ledger manuscript validators (W4 T4.4)."""

from __future__ import annotations

import unittest

from app.models.fact_ledger import ClaimClass, LedgerClaim
from app.services.fact_ledger_builder import build_fact_ledger
from app.services.proposal_t2_validators import scan_all_t2, scan_years_experience_against_ledger
from tests.fixtures.manuscripts.loader import load_fixture


class YearsExperienceT2Tests(unittest.TestCase):
    def test_gsu_fixture_flags_cross_section_years(self) -> None:
        draft, _, _, expected = load_fixture("gsu_inconsistent_years")
        self.assertIn("years_inconsistency", expected["critical"])

        ledger = build_fact_ledger(
            version="gsu-test",
            built_at="2026-07-28T00:00:00Z",
            claims=[
                LedgerClaim(
                    claimId="c35",
                    claimClass=ClaimClass.YEARS_EXPERIENCE,
                    subjectType="person",
                    subjectId="person:ron-comer",
                    fieldName="years_experience",
                    valueText="35 years",
                    valueNumber=35,
                    verbatimSnippet="35+ years",
                ),
                LedgerClaim(
                    claimId="c38",
                    claimClass=ClaimClass.YEARS_EXPERIENCE,
                    subjectType="person",
                    subjectId="person:ron-comer",
                    fieldName="years_experience",
                    valueText="38 years",
                    valueNumber=38,
                    verbatimSnippet="38 years",
                ),
            ],
            people_names={"person:ron-comer": "Ron Comer"},
        )
        findings = scan_years_experience_against_ledger(draft, ledger)
        codes = {f["code"] for f in findings}
        self.assertTrue(
            codes & {
                "t2.fact_ledger.years_unresolved",
                "t2.fact_ledger.years_cross_section",
                "t2.fact_ledger.years_manuscript_conflict",
            },
            msg=f"expected years conflict codes, got {codes}",
        )

    def test_known_good_clean_no_t2_without_ledger(self) -> None:
        draft, _, _, _ = load_fixture("known_good_clean")
        self.assertEqual(scan_all_t2(draft, None), [])

    def test_authoritative_single_value_mismatch(self) -> None:
        from app.models.proposal import ProposalDraft, ProposalSection

        draft = ProposalDraft(
            rfpId="r1",
            updatedAt="t",
            sections=[
                ProposalSection(
                    id="s1",
                    title="Bio",
                    content="Ron Comer brings 38 years of public-sector experience.",
                )
            ],
        )
        ledger = build_fact_ledger(
            version="v1",
            built_at="t",
            claims=[
                LedgerClaim(
                    claimId="c35",
                    claimClass=ClaimClass.YEARS_EXPERIENCE,
                    subjectType="person",
                    subjectId="person:ron-comer",
                    fieldName="years_experience",
                    valueText="35 years",
                    valueNumber=35,
                )
            ],
            people_names={"person:ron-comer": "Ron Comer"},
        )
        findings = scan_years_experience_against_ledger(draft, ledger)
        self.assertTrue(
            any(f["code"] == "t2.fact_ledger.years_mismatch" for f in findings),
            msg=findings,
        )

    def test_benign_number_without_name_does_not_flag(self) -> None:
        from app.models.proposal import ProposalDraft, ProposalSection

        draft = ProposalDraft(
            rfpId="r1",
            updatedAt="t",
            sections=[
                ProposalSection(
                    id="s1",
                    title="Approach",
                    content="We deliver within 35 days using a 12-week plan.",
                )
            ],
        )
        ledger = build_fact_ledger(
            version="v1",
            built_at="t",
            claims=[
                LedgerClaim(
                    claimId="c35",
                    claimClass=ClaimClass.YEARS_EXPERIENCE,
                    subjectType="person",
                    subjectId="person:ron-comer",
                    fieldName="years_experience",
                    valueText="35 years",
                    valueNumber=35,
                )
            ],
            people_names={"person:ron-comer": "Ron Comer"},
        )
        self.assertEqual(scan_years_experience_against_ledger(draft, ledger), [])


if __name__ == "__main__":
    unittest.main()
