"""The whole-manuscript LLM audit must not re-run on an unchanged manuscript.

The audit costs ~30k input tokens and fires 4-6 times per generation. Several
of those calls see a byte-identical draft — notably the post-loop
_attach_phase4_manuscript_audit, which re-audits the draft the repair loop just
finished auditing.
"""

from __future__ import annotations

import unittest
from unittest import mock

from app.models.proposal import (
    PreSubmitReview,
    ProofPoint,
    ProposalDraft,
    ProposalResearchCache,
    RfpSectionMap,
)
from app.services.proposal_manuscript_auditor import (
    clear_manuscript_audit_cache,
    run_manuscript_auditor,
)
from tests.fixtures.manuscripts.loader import load_fixture

_LLM_RESPONSE = (
    {
        "findings": [
            {
                "severity": "critical",
                "category": "fabrication",
                "sectionId": "section-1",
                "message": "Unsupported retention statistic.",
            }
        ]
    },
    "mock-provider",
)


def _ready_research(
    draft: ProposalDraft, research: ProposalResearchCache | None
) -> ProposalResearchCache:
    mapped = [
        RfpSectionMap(id=s.id, title=s.title, requirements=["x"]) for s in draft.sections
    ]
    review = PreSubmitReview(
        rfpId=draft.rfp_id,
        scannedAt="2026-01-01T00:00:00Z",
        summary="ok",
        readyToSubmit=True,
    )
    return ProposalResearchCache(
        rfpId=draft.rfp_id,
        updatedAt="2026-01-01T00:00:00Z",
        rfpSections=mapped,
        proofPoints=[
            ProofPoint(
                requirement="x",
                caseStudy="Example",
                kbSource="KB",
                narrativeHook="Delivered measurable outcomes",
            )
        ],
        proposalExecutionPlan={"validation": {"readinessStatus": "ready"}},
        presubmitReview=review,
        budget=research.budget if research and research.budget else None,
    )


class ManuscriptAuditCacheTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        clear_manuscript_audit_cache()

    def tearDown(self) -> None:
        clear_manuscript_audit_cache()

    async def test_unchanged_manuscript_skips_second_llm_call(self) -> None:
        draft, research, rfp, _ = load_fixture("known_good_clean")
        ready = _ready_research(draft, research)

        fake = mock.AsyncMock(return_value=_LLM_RESPONSE)
        with mock.patch(
            "app.services.proposal_manuscript_auditor.llm.chat_json", new=fake
        ):
            first = await run_manuscript_auditor(
                draft=draft, research=ready, rfp=rfp, use_llm=True
            )
            second = await run_manuscript_auditor(
                draft=draft, research=ready, rfp=rfp, use_llm=True
            )

        self.assertEqual(
            fake.await_count, 1, "second audit of an unchanged draft must hit cache"
        )
        self.assertEqual(
            [f.message for f in first.findings],
            [f.message for f in second.findings],
            "cached audit must return the same findings",
        )

    async def test_changed_manuscript_reruns_the_audit(self) -> None:
        draft, research, rfp, _ = load_fixture("known_good_clean")
        ready = _ready_research(draft, research)

        fake = mock.AsyncMock(return_value=_LLM_RESPONSE)
        with mock.patch(
            "app.services.proposal_manuscript_auditor.llm.chat_json", new=fake
        ):
            await run_manuscript_auditor(
                draft=draft, research=ready, rfp=rfp, use_llm=True
            )

            edited = draft.model_copy(deep=True)
            edited.sections[0].content = (
                (edited.sections[0].content or "") + "\n\nNewly added paragraph."
            )
            await run_manuscript_auditor(
                draft=edited, research=ready, rfp=rfp, use_llm=True
            )

        self.assertEqual(
            fake.await_count, 2, "an edited manuscript must trigger a fresh audit"
        )


if __name__ == "__main__":
    unittest.main()
