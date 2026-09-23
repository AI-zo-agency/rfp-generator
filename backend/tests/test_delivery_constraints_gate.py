"""DeliveryConstraints post-edit gate — LLM judge mocked in unit tests."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app.models.delivery_constraints import DeliveryConstraints, DeliveryTrack
from app.models.proposal import ProposalDraft, ProposalResearchCache, ProposalSection
from app.services.delivery_constraints_gate import (
    collect_delivery_constraint_issues,
    gate_delivery_section,
    scan_delivery_constraints_on_draft_sync,
    section_title_is_sow_or_timeline,
)


def _dupage_constraints() -> DeliveryConstraints:
    return DeliveryConstraints(
        tracks=[
            DeliveryTrack(
                id="part-1",
                label="General Marketing",
                nteAnnual=75000,
                billing="hourly",
            ),
            DeliveryTrack(
                id="part-2",
                label="Young Adult Outreach",
                nteAnnual=100000,
                billing="hourly",
            ),
        ],
        outOfScope=[
            "County-wide website redesign",
            "three social platforms retainer not in RFP",
        ],
        mandatoryDeliverables=[
            "General Marketing communications",
            "Young Adult Outreach Campaign",
        ],
        nonCommingleTracks=True,
    )


class DeliveryConstraintsGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_flags_out_of_scope_invention(self) -> None:
        constraints = _dupage_constraints()
        llm_json = {
            "drifts": [
                {
                    "kind": "out_of_scope",
                    "severity": "critical",
                    "message": (
                        "Section invents County-wide website redesign which is "
                        "out of scope for this RFP"
                    ),
                    "excerpt": "redesign the County-wide website",
                    "constraintEvidence": "outOfScope: County-wide website redesign",
                },
                {
                    "kind": "out_of_scope",
                    "severity": "critical",
                    "message": "Managing 3 social platforms is out of scope",
                    "excerpt": "manage 3 social platforms",
                    "constraintEvidence": (
                        "outOfScope: three social platforms retainer not in RFP"
                    ),
                },
            ]
        }
        with patch(
            "app.services.delivery_constraints_gate.safe_chat_json",
            new=AsyncMock(return_value=(llm_json, "mock")),
        ):
            issues = await gate_delivery_section(
                "Statement of Work",
                (
                    "We will redesign the County-wide website and manage "
                    "3 social platforms under a retainer..."
                ),
                constraints,
            )
        self.assertTrue(
            any(
                "out of scope" in (getattr(i, "detail", None) or i.message or "").casefold()
                for i in issues
            )
        )
        self.assertTrue(all(i.category == "delivery_constraints" for i in issues))

    async def test_clean_section_returns_no_issues(self) -> None:
        constraints = _dupage_constraints()
        with patch(
            "app.services.delivery_constraints_gate.safe_chat_json",
            new=AsyncMock(return_value=({"drifts": []}, "mock")),
        ):
            issues = await gate_delivery_section(
                "Statement of Work",
                (
                    "Part 1 covers General Marketing communications. "
                    "Part 2 covers the Young Adult Outreach Campaign. "
                    "Tracks are billed separately within each NTE."
                ),
                constraints,
            )
        self.assertEqual(issues, [])

    async def test_llm_failure_returns_review_required_issue(self) -> None:
        constraints = _dupage_constraints()
        with patch(
            "app.services.delivery_constraints_gate.safe_chat_json",
            new=AsyncMock(return_value=({}, "none")),
        ):
            issues = await gate_delivery_section(
                "Statement of Work",
                "Part 1 covers General Marketing communications.",
                constraints,
            )
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].category, "delivery_constraints")
        self.assertIn("verification failed", (issues[0].message or "").casefold())
        self.assertIn("review required", (issues[0].message or "").casefold())

    async def test_empty_constraints_skip_llm(self) -> None:
        with patch(
            "app.services.delivery_constraints_gate.safe_chat_json",
            new=AsyncMock(return_value=({"drifts": []}, "mock")),
        ) as mock_llm:
            issues = await gate_delivery_section(
                "Statement of Work",
                "Some prose about delivery.",
                DeliveryConstraints(),
            )
        self.assertEqual(issues, [])
        mock_llm.assert_not_called()

    async def test_collect_only_sow_timeline_sections(self) -> None:
        draft = ProposalDraft(
            rfpId="rfp-t",
            sections=[
                ProposalSection(
                    id="s-cover",
                    title="Cover Letter",
                    content="We love this RFP and will redesign the County website.",
                ),
                ProposalSection(
                    id="s-sow",
                    title="Statement of Work",
                    content="We will redesign the County-wide website.",
                ),
            ],
            updatedAt="2026-09-23T00:00:00+00:00",
        )
        research = ProposalResearchCache(
            rfpId="rfp-t",
            updatedAt="2026-09-23T00:00:00+00:00",
            deliveryConstraints=_dupage_constraints().model_dump(by_alias=True),
        )
        llm_json = {
            "drifts": [
                {
                    "kind": "out_of_scope",
                    "severity": "critical",
                    "message": "County-wide website is out of scope",
                    "excerpt": "County-wide website",
                    "constraintEvidence": "outOfScope",
                }
            ]
        }
        with patch(
            "app.services.delivery_constraints_gate.safe_chat_json",
            new=AsyncMock(return_value=(llm_json, "mock")),
        ) as mock_llm:
            issues = await collect_delivery_constraint_issues(
                draft=draft, research=research
            )
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].section_id, "s-sow")
        self.assertEqual(mock_llm.await_count, 1)

    def test_title_hints(self) -> None:
        self.assertTrue(section_title_is_sow_or_timeline("4. Statement of Work"))
        self.assertTrue(section_title_is_sow_or_timeline("Project Timeline"))
        self.assertFalse(section_title_is_sow_or_timeline("Cost Proposal"))

    def test_sync_scan_wires_into_consistency_shape(self) -> None:
        draft = ProposalDraft(
            rfpId="rfp-t",
            sections=[
                ProposalSection(
                    id="s-sow",
                    title="Scope of Work",
                    content="We will redesign the County-wide website.",
                ),
            ],
            updatedAt="2026-09-23T00:00:00+00:00",
        )
        research = ProposalResearchCache(
            rfpId="rfp-t",
            updatedAt="2026-09-23T00:00:00+00:00",
            deliveryConstraints=_dupage_constraints().model_dump(by_alias=True),
        )
        llm_json = {
            "drifts": [
                {
                    "kind": "out_of_scope",
                    "severity": "critical",
                    "message": "Invented County-wide redesign is out of scope",
                    "excerpt": "County-wide website",
                    "constraintEvidence": "outOfScope",
                }
            ]
        }
        with patch(
            "app.services.delivery_constraints_gate.safe_chat_json",
            new=AsyncMock(return_value=(llm_json, "mock")),
        ):
            issues = scan_delivery_constraints_on_draft_sync(
                draft=draft, research=research
            )
        self.assertTrue(
            any("out of scope" in (i.message or "").casefold() for i in issues)
        )


if __name__ == "__main__":
    unittest.main()
