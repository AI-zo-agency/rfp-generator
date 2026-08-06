"""Task 6 — the Scan-RFP fulfill guards must run during full generation too.

Three guards existed and were reachable only from proposal_fulfill_rfp_gaps.py's
mode="full" path (historically the live Scan button used verify_scrub_only).
Every proposal produced by generate_full_proposal shipped without them:

  - repair_truncated_manuscript_sections  (proposal_fulfill_truncation_repair.py)
  - repair_fabricated_qualifications      (proposal_fulfill_fabrication_guard.py)
  - validate_and_flag_section             (evidence_trust/claim_validator.py)

This file has three parts:

1. Phase3GuardReachabilityTests — proves run_phase3_drafting (what
   generate_full_proposal calls, unconditionally, for Phase 3) now invokes all
   three guards, by patching the names as imported by proposal_generator.
2. RealDefectCaughtTests — exercises the REAL (unmocked) guard functions against
   the two defects from human QA: a mid-sentence cutoff, and an invented
   case-study/reference-block client. These prove actual repair/flag behavior,
   not just that a mock was called.
3. CredentialFabricationGapTests — documents, with a real (unmocked) repro, that
   neither wired guard actually catches the literal motivating example (a named
   staff member's overstated certification vs. their verified bio). See
   task-6-report.md for the full analysis and what would need to be built to
   close this gap.
"""

from __future__ import annotations

import unittest
from contextlib import ExitStack
from unittest import mock
from unittest.mock import AsyncMock

from app.models.proposal import (
    ProposalDraft,
    ProposalSection,
    RfpSectionMap,
)
from app.models.rfp import RfpRecord
from app.services import proposal_generator as generator
from app.services.evidence_trust.claim_validator import validate_and_flag_section
from app.services.evidence_trust.client_list import ClientListEntry, ClientListRegistry
from app.services.proposal_fulfill_fabrication_guard import (
    repair_fabricated_qualifications,
)
from app.services.proposal_fulfill_truncation_repair import (
    repair_truncated_manuscript_sections,
)
from tests.fixtures.manuscripts.loader import load_fixture
from tests.test_manuscript_auditor import _ready_research


def _phase3_fixture():
    draft, research, rfp, _expected = load_fixture(
        "cvvb_v2_truncation_orphan_commission"
    )
    ready = _ready_research(draft, research)
    return draft, ready, rfp


def _rfp(rfp_id: str = "rfp-guard-wiring") -> RfpRecord:
    return RfpRecord(
        id=rfp_id,
        title="Test RFP",
        client="Test Client",
        sector="Health",
        source="manual",
        dueDate="2026-08-01",
        receivedDate="2026-07-01",
        lastActivity="2026-07-01",
        lastActivityNote="test",
    )


class Phase3GuardReachabilityTests(unittest.IsolatedAsyncioTestCase):
    """generate_full_proposal always calls run_phase3_drafting for Phase 3 —
    prove that call path now reaches all three guards."""

    async def test_phase3_drafting_calls_all_three_guards(self) -> None:
        draft, research, rfp = _phase3_fixture()
        mapped_section = RfpSectionMap(
            id="section-rfp-1", title="Approach", requirements=["x"]
        )
        research = research.model_copy(update={"rfp_sections": [mapped_section]})

        drafted_section = ProposalSection(
            id="section-rfp-1",
            title="Approach",
            content="Our approach to this engagement is thorough and evidence based.",
            status="generated",
            source="generated",
            mode="write",
        )

        trunc_mock = AsyncMock(return_value=(draft, []))
        fab_mock = mock.Mock(return_value=(draft, [], []))
        claim_mock = mock.Mock(
            side_effect=lambda content, **_k: (content, mock.Mock(notes=[]))
        )
        registry_with_entry = ClientListRegistry(
            entries=[
                ClientListEntry(
                    name="Acme Test City",
                    sector="Government",
                    work_type="Website",
                    public="Confirm",
                )
            ]
        )

        patches = [
            mock.patch(
                "app.services.proposal_generator.llm.is_configured", return_value=True
            ),
            mock.patch(
                "app.services.proposal_generator._load_rfp_for_proposal",
                return_value=(rfp, mock.Mock(description="", pdf_text=""), "context"),
            ),
            mock.patch(
                "app.services.proposal_generator.aget_research_cache",
                new=AsyncMock(return_value=research),
            ),
            mock.patch(
                "app.services.proposal_generator.aget_proposal_draft",
                new=AsyncMock(return_value=draft),
            ),
            mock.patch(
                "app.services.proposal_drafting_graph.partition_phase3_sections",
                return_value=([mapped_section], []),
            ),
            mock.patch(
                "app.services.proposal_generator._persist_phase3_partial",
                new=AsyncMock(),
            ),
            mock.patch(
                "app.services.proposal_generator.run_drafting_graph",
                new=AsyncMock(return_value=([drafted_section], "test-provider", [])),
            ),
            mock.patch(
                "app.services.proposal_generator.asave_proposal_draft",
                new=AsyncMock(),
            ),
            mock.patch(
                "app.services.proposal_generator.asave_research_cache",
                new=AsyncMock(),
            ),
            mock.patch(
                "app.services.proposal_generator.repair_truncated_manuscript_sections",
                new=trunc_mock,
            ),
            mock.patch(
                "app.services.proposal_generator.repair_fabricated_qualifications",
                new=fab_mock,
            ),
            mock.patch(
                "app.services.proposal_generator.validate_and_flag_section",
                new=claim_mock,
            ),
            mock.patch(
                "app.services.proposal_generator.load_client_list_registry",
                new=AsyncMock(return_value=registry_with_entry),
            ),
        ]
        with ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            await generator.run_phase3_drafting(rfp.id)

        trunc_mock.assert_awaited_once()
        self.assertEqual(trunc_mock.await_args.kwargs["rfp"], rfp)
        fab_mock.assert_called_once()
        claim_mock.assert_called()

    async def test_ordering_truncation_before_fabrication_before_claim(self) -> None:
        """The plan requires truncation repair -> fabrication guard -> claim
        validation, so validators see complete sentences."""
        draft, research, rfp = _phase3_fixture()
        mapped_section = RfpSectionMap(
            id="section-rfp-1", title="Approach", requirements=["x"]
        )
        research = research.model_copy(update={"rfp_sections": [mapped_section]})
        drafted_section = ProposalSection(
            id="section-rfp-1",
            title="Approach",
            content="Our approach to this engagement is thorough and evidence based.",
            status="generated",
            source="generated",
            mode="write",
        )

        call_order: list[str] = []

        async def _trunc(*_a, **_k):
            call_order.append("truncation")
            return draft, []

        def _fab(*_a, **_k):
            call_order.append("fabrication")
            return draft, [], []

        def _claim(content, **_k):
            call_order.append("claim")
            return content, mock.Mock(notes=[])

        registry_with_entry = ClientListRegistry(
            entries=[
                ClientListEntry(
                    name="Acme Test City",
                    sector="Government",
                    work_type="Website",
                    public="Confirm",
                )
            ]
        )

        patches = [
            mock.patch(
                "app.services.proposal_generator.llm.is_configured", return_value=True
            ),
            mock.patch(
                "app.services.proposal_generator._load_rfp_for_proposal",
                return_value=(rfp, mock.Mock(description="", pdf_text=""), "context"),
            ),
            mock.patch(
                "app.services.proposal_generator.aget_research_cache",
                new=AsyncMock(return_value=research),
            ),
            mock.patch(
                "app.services.proposal_generator.aget_proposal_draft",
                new=AsyncMock(return_value=draft),
            ),
            mock.patch(
                "app.services.proposal_drafting_graph.partition_phase3_sections",
                return_value=([mapped_section], []),
            ),
            mock.patch(
                "app.services.proposal_generator._persist_phase3_partial",
                new=AsyncMock(),
            ),
            mock.patch(
                "app.services.proposal_generator.run_drafting_graph",
                new=AsyncMock(return_value=([drafted_section], "test-provider", [])),
            ),
            mock.patch(
                "app.services.proposal_generator.asave_proposal_draft", new=AsyncMock()
            ),
            mock.patch(
                "app.services.proposal_generator.asave_research_cache", new=AsyncMock()
            ),
            mock.patch(
                "app.services.proposal_generator.repair_truncated_manuscript_sections",
                new=AsyncMock(side_effect=_trunc),
            ),
            mock.patch(
                "app.services.proposal_generator.repair_fabricated_qualifications",
                new=mock.Mock(side_effect=_fab),
            ),
            mock.patch(
                "app.services.proposal_generator.validate_and_flag_section",
                new=mock.Mock(side_effect=_claim),
            ),
            mock.patch(
                "app.services.proposal_generator.load_client_list_registry",
                new=AsyncMock(return_value=registry_with_entry),
            ),
        ]
        with ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            await generator.run_phase3_drafting(rfp.id)

        self.assertEqual(call_order[0], "truncation")
        self.assertIn("fabrication", call_order)
        self.assertIn("claim", call_order)
        self.assertLess(call_order.index("fabrication"), call_order.index("claim"))


class RealDefectCaughtTests(unittest.IsolatedAsyncioTestCase):
    """Exercise the real, unmocked guard functions against the two motivating
    real-world defects."""

    async def test_mid_sentence_cutoff_is_repaired_by_real_truncation_guard(
        self,
    ) -> None:
        """Section 1.2 in the observed proposal ended mid-sentence with no
        closing punctuation. Reproduce that shape and prove the real
        (unmocked) repair function fixes it."""
        truncated = (
            "Our creative process begins with a discovery workshop where we align "
            "on goals, audience, and success metrics with your team before any "
            "design work starts. From there, our strategists build a positioning "
            "framework grounded in the research phase, and our designers translate "
            "that framework into visual concepts that are tested against the "
            "original brief for clarity, distinctiveness, and channel fit, and "
            "the resulting recommendations are documented and shared with the "
            "client stakeholders for review and sign-off before we proceed to the "
            "next phase of the engagement, which includes the development of a "
            "detailed rollout timeline that accounts for internal review cycles "
            "and third-party vendor"
        )
        self.assertGreaterEqual(len(truncated), 350)
        section = ProposalSection(
            id="section-1-2-approach",
            title="1.2 — Our Approach",
            content=truncated,
            status="generated",
            source="generated",
            mode="write",
        )
        draft = ProposalDraft(
            rfpId="rfp-trunc", sections=[section], updatedAt="2026-01-01T00:00:00Z"
        )
        rfp = _rfp("rfp-trunc")

        with mock.patch(
            "app.services.proposal_fulfill_truncation_repair.llm.is_configured",
            return_value=False,
        ):
            repaired_draft, logs = await repair_truncated_manuscript_sections(
                draft=draft,
                rfp=rfp,
                skip_section_ids=set(),
                use_llm=False,
            )

        # No LLM and no closing-template match for this section id -> the guard
        # cannot silently complete it, but it MUST NOT ship the cutoff
        # unexamined either: confirm the detector that drives the guard agrees
        # this is truncated, matching the plan's own definition of the defect.
        from app.services.proposal_fulfill_truncation_repair import (
            looks_truncated_for_fulfill,
        )

        self.assertTrue(looks_truncated_for_fulfill(truncated))

        # Now prove the guard actually repairs when the LLM path is available.
        async def _fake_chat_json(*_a, **_k):
            return (
                {
                    "content": truncated
                    + " onboarding, delivered as a single integrated document."
                },
                {},
            )

        with (
            mock.patch(
                "app.services.proposal_fulfill_truncation_repair.llm.is_configured",
                return_value=True,
            ),
            mock.patch(
                "app.services.proposal_fulfill_truncation_repair.llm.chat_json",
                new=AsyncMock(side_effect=_fake_chat_json),
            ),
        ):
            repaired_draft, logs = await repair_truncated_manuscript_sections(
                draft=draft,
                rfp=rfp,
                skip_section_ids=set(),
                use_llm=True,
            )

        self.assertTrue(logs, "expected a repair log entry")
        new_content = repaired_draft.sections[0].content
        self.assertNotEqual(new_content, truncated)
        self.assertFalse(looks_truncated_for_fulfill(new_content))
        self.assertTrue(new_content.rstrip().endswith("."))

    def test_invented_case_study_client_is_reverted_by_real_fabrication_guard(
        self,
    ) -> None:
        """The fabrication guard's actual job: an invented case-study client
        (not in the real portfolio) must be reverted to an honest [VERIFY]
        placeholder rather than shipping a fabricated reference."""
        section = ProposalSection(
            id="section-3-qualifications",
            title="Qualifications and References",
            content=(
                "### Case Study 1: Queensland Tourism Commission\n\n"
                "We delivered a record-breaking visitor campaign for Queensland "
                "Tourism, driving a 40% increase in bookings.\n"
            ),
            status="generated",
            source="generated",
            mode="write",
        )
        draft = ProposalDraft(
            rfpId="rfp-fab", sections=[section], updatedAt="2026-01-01T00:00:00Z"
        )

        new_draft, logs, human = repair_fabricated_qualifications(
            draft, research=None, registry=None
        )

        self.assertTrue(logs, "expected a fabrication-guard log entry")
        new_content = new_draft.sections[0].content
        self.assertNotIn("Queensland Tourism", new_content)
        self.assertIn("[VERIFY", new_content)
        self.assertTrue(human, "expected a human-facing decision-gap note")


class CredentialFabricationGapTests(unittest.IsolatedAsyncioTestCase):
    """Document, with a real (unmocked) repro, that the literal motivating
    example from human QA — an overstated individual certification versus a
    named person's verified bio — is NOT caught by either wired guard.

    validate_and_flag_section only reasons about ClientListRegistry entries
    (past/current customers named in case studies); it has no notion of a
    staff member's verified bio. repair_fabricated_qualifications only reverts
    invented case-study/reference CLIENT content; it does not compare a named
    person's claimed certifications against any source of truth. Wiring these
    three guards fixes the truncation defect and adds real client-claim
    protections, but does not close this specific credential gap — that needs
    its own guard (see task-6-report.md).
    """

    def test_credential_overclaim_not_flagged_by_claim_validator(self) -> None:
        text = (
            "Harsh Mohite is our paid media lead. He holds Google Ads "
            "Certification, Google Analytics 4, Google Search Console, and "
            "DV360 certifications. He is also Meta Ads Certified."
        )
        out, report = validate_and_flag_section(
            text, registry=ClientListRegistry(entries=[]), slot="key_personnel"
        )
        self.assertEqual(out, text)
        self.assertEqual(report.flags_inserted, 0)

    def test_credential_overclaim_not_reverted_by_fabrication_guard(self) -> None:
        text = (
            "Harsh Mohite is our paid media lead. He holds Google Ads "
            "Certification, Google Analytics 4, Google Search Console, and "
            "DV360 certifications. He is also Meta Ads Certified."
        )
        section = ProposalSection(
            id="section-2-key-personnel",
            title="Key Personnel Qualifications",
            content=text,
            status="generated",
            source="generated",
            mode="write",
        )
        draft = ProposalDraft(
            rfpId="rfp-cred", sections=[section], updatedAt="2026-01-01T00:00:00Z"
        )
        new_draft, logs, human = repair_fabricated_qualifications(
            draft, research=None, registry=None
        )
        self.assertEqual(new_draft.sections[0].content, text)
        self.assertEqual(logs, [])
        self.assertEqual(human, [])


if __name__ == "__main__":
    unittest.main()
