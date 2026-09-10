"""Unverified state business-registration claims must not ship as fact."""

from __future__ import annotations

import unittest

from app.models.proposal import ProposalDraft, ProposalSection
from app.services.proposal_state_registration_guard import (
    scrub_unverified_state_registration_claims,
)
from app.services.proposal_zero_fabrication import apply_zero_fabrication_guards


def _draft() -> ProposalDraft:
    return ProposalDraft(
        rfpId="rfp-md",
        updatedAt="2026-01-01T00:00:00Z",
        sections=[
            ProposalSection(
                id="section-1-business-info",
                title="1.3 — Business Information",
                content=(
                    "### State Registrations\n\n"
                    "| State | Status |\n"
                    "| Oregon | Active |\n"
                    "| Washington | Active |\n"
                    "| Texas | Active |\n"
                    "| Colorado | Active |\n"
                    "| California | Active |\n"
                ),
            ),
            ProposalSection(
                id="rfp-sec-transmission",
                title="Transmission Letter",
                content=(
                    "Dear Evaluation Committee,\n\n"
                    "We are registered to conduct business in Maryland. "
                    "We look forward to partnering with Calvert County.\n"
                ),
            ),
        ],
    )


class StateRegistrationGuardTests(unittest.TestCase):
    def test_maryland_claim_removed_when_not_on_verified_list(self) -> None:
        updated, logs = scrub_unverified_state_registration_claims(_draft())
        letter = next(s.content or "" for s in updated.sections if s.id == "rfp-sec-transmission")
        self.assertNotIn("We are registered to conduct business in Maryland.", letter)
        self.assertIn("MANUAL FILL", letter)
        self.assertIn("Maryland", letter)
        self.assertIn("Oregon", letter)
        self.assertTrue(any("Maryland" in line for line in logs))
        inventory = next(
            s.content or "" for s in updated.sections if s.id == "section-1-business-info"
        )
        self.assertIn("Oregon", inventory)
        self.assertNotIn("MANUAL FILL", inventory)

    def test_zero_fabrication_stack_runs_the_guard(self) -> None:
        updated, report = apply_zero_fabrication_guards(_draft(), label="test")
        letter = next(s.content or "" for s in updated.sections if s.id == "rfp-sec-transmission")
        self.assertNotIn("We are registered to conduct business in Maryland.", letter)
        self.assertTrue(
            any("state registration" in line.casefold() for line in report.logs)
        )

    def test_does_not_rewrite_a_listed_state(self) -> None:
        draft = _draft()
        letter = draft.sections[1].model_copy(
            update={
                "content": "We are registered to conduct business in Oregon.\n",
            }
        )
        draft = draft.model_copy(update={"sections": [draft.sections[0], letter]})
        updated, logs = scrub_unverified_state_registration_claims(draft)
        body = updated.sections[1].content or ""
        self.assertIn("We are registered to conduct business in Oregon.", body)
        self.assertFalse(logs)

    def test_drops_stale_do_not_assert_fill_when_jurisdiction_verified(self) -> None:
        """Claim + contradictory MANUAL FILL must not ship side by side."""
        draft = ProposalDraft(
            rfpId="rfp-ca",
            updatedAt="2026-01-01T00:00:00Z",
            sections=[
                ProposalSection(
                    id="section-1-business-info",
                    title="1.3 — Business Information",
                    content=(
                        "### State Registrations\n\n"
                        "| State | Status |\n"
                        "| California | Active |\n"
                        "| Oregon | Active |\n"
                    ),
                ),
                ProposalSection(
                    id="rfp-sec-license",
                    title="Business License",
                    content=(
                        "We hold the business registration and standing this contract "
                        "requires to operate in California.\n\n"
                        "[MANUAL FILL: Sonja — do not assert California business "
                        "registration until it appears in companyfacts / Section 1.3 "
                        "State Registrations.]\n"
                    ),
                ),
            ],
        )
        updated, logs = scrub_unverified_state_registration_claims(draft)
        body = next(s.content or "" for s in updated.sections if s.id == "rfp-sec-license")
        self.assertIn("California", body)
        self.assertNotIn("do not assert California", body)
        self.assertNotIn("MANUAL FILL", body)
        self.assertTrue(any("stale" in line.casefold() for line in logs))


if __name__ == "__main__":
    unittest.main()
