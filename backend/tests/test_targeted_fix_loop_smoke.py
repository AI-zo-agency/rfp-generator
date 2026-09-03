"""Review & Fix loop: run it end to end with every collaborator stubbed.

Every stub is autospec'd, so a wrong keyword or a swapped tuple unpack fails
here in ~1s instead of after minutes of paid LLM calls in a Celery worker.
"""

from __future__ import annotations

import asyncio
import contextlib
import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from app.models.proposal import (
    BudgetLineItem,
    PreSubmitReview,
    ProposalBudget,
    ProposalDraft,
    ProposalPipelineCheckpoint,
    ProposalResearchCache,
    ProposalSection,
)
from app.models.rfp import RfpRecord
from app.services.proposal_blocker_prevention import BlockerPreventionResult
from app.services.proposal_kb_fact_checker import FactCheckReport
from app.services.proposal_rfp_compulsory_content import (
    CompulsoryContentAsk,
    CompulsoryShortfall,
)

FULFILL = "app.services.proposal_fulfill_rfp_gaps"


def _section(sid: str, content: str | None = None) -> ProposalSection:
    return ProposalSection(
        id=sid, title=sid.upper(), content=content or f"body of {sid}"
    )


def _draft(*ids: str, content: str | None = None) -> ProposalDraft:
    return ProposalDraft(
        rfpId="r1",
        sections=[_section(s, content) for s in ids],
        updatedAt="2026-09-03T00:00:00Z",
    )


def _research(
    done: list[str] | None = None,
    structure_done: bool = False,
    contradiction_done: bool = False,
    won_fill_done: bool = False,
) -> ProposalResearchCache:
    return ProposalResearchCache(
        rfpId="r1",
        updatedAt="2026-09-03T00:00:00Z",
        pipelineCheckpoint=ProposalPipelineCheckpoint(
            updatedAt="2026-09-03T00:00:00Z",
            scanProfile="targeted_fix",
            inProgressPhase="fulfill-scan",
            targetedFixDoneSectionIds=list(done or []),
            targetedFixStructureDone=structure_done,
            targetedFixContradictionDone=contradiction_done,
            targetedFixWonFillDone=won_fill_done,
        ),
    )


class TargetedFixLoopSmokeTests(unittest.IsolatedAsyncioTestCase):
    async def _run(
        self,
        *,
        done: list[str] | None = None,
        needs_fill: bool = False,
        structure_done: bool = False,
        contradiction_done: bool = False,
        won_fill_done: bool = False,
        section_content: str | None = None,
        section_ids: tuple[str, ...] = ("sec-a", "sec-b", "sec-c"),
        compulsory_shortfalls: list | None = None,
        compulsory_merge_result: tuple | None = None,
        dq_text_risks: list[str] | None = None,
        scrub_result: tuple[str, int] | None = None,
        hollow_fill_result: tuple | None = None,
        hollow_fill_raises: Exception | None = None,
        budget: Any | None = None,
        money_constraints: list | None = None,
        money_constraints_raises: Exception | None = None,
        apply_constraints_result: Any | None = None,
        over_authority_flags: list[str] | None = None,
        under_minimum_flags: list[str] | None = None,
        ceiling_mismatches: list | None = None,
        # Matches config.review_fix_section_concurrency's shipped default, so the
        # suite exercises the parallel path that production actually runs. Tests
        # asserting strictly per-section activity pin concurrency=1 explicitly.
        concurrency: int = 3,
        fact_check_delays: dict[str, float] | None = None,
        fact_check_exceptions: dict[str, Exception] | None = None,
        record_done_raise_after: int | None = None,
        recorded_out: list[str] | None = None,
    ):
        from app.services import proposal_fulfill_rfp_gaps as mod

        draft = _draft(*section_ids, content=section_content)
        research = _research(
            done,
            structure_done=structure_done,
            contradiction_done=contradiction_done,
            won_fill_done=won_fill_done,
        )
        if budget is not None:
            research = research.model_copy(update={"budget": budget})
        rfp = RfpRecord(
            id="r1",
            title="RFP",
            client="C",
            sector="Government",
            source="manual",
            dueDate="2026-10-01",
            receivedDate="2026-09-01",
            lastActivity="2026-09-01",
            lastActivityNote="t",
        )
        checked: list[str] = []
        # Exposed to the caller when passed in, so a test can inspect it even
        # after an exception has propagated out of `_run` (the closures below
        # live inside this frame and are otherwise unreachable post-raise).
        recorded: list[str] = recorded_out if recorded_out is not None else []
        review_calls: list[dict] = []
        saved_research: list[ProposalResearchCache] = []

        def _review(**kw):
            review_calls.append(kw)
            return PreSubmitReview(rfpId="r1", scannedAt="2026-09-03T00:00:00Z")

        async def _save_research(rc):
            saved_research.append(rc)

        async def _fact_check(section, **_kw):
            if fact_check_exceptions and section.id in fact_check_exceptions:
                # Let the real batch code observe the failure the same way a
                # real fact-check crash would arrive via asyncio.gather.
                raise fact_check_exceptions[section.id]
            if fact_check_delays and section.id in fact_check_delays:
                # Lets a test make a later-submitted section finish first, to
                # prove the sequential tail still applies in draft order and
                # not completion order.
                await asyncio.sleep(fact_check_delays[section.id])
            checked.append(section.id)
            return section, FactCheckReport(sections_checked=1, logs=[f"checked {section.id}"])

        blocker_calls: list[dict] = []

        async def _blocker(d, **kw):
            blocker_calls.append(kw)
            return BlockerPreventionResult(draft=d, logs=[])

        order_calls: list[int] = []

        async def _order(**kw):
            order_calls.append(1)
            return kw["draft"], []

        record_done_call_count = 0

        async def _record_done(rfp_id, section_id, **_kw):
            nonlocal record_done_call_count
            record_done_call_count += 1
            if (
                record_done_raise_after is not None
                and record_done_call_count == record_done_raise_after
            ):
                # Simulates the run being killed mid-batch, AFTER the earlier
                # batch(es) have already been checkpointed and saved.
                raise RuntimeError("simulated interruption")
            recorded.append(section_id)

        structure_recorded: list[str] = []

        async def _record_structure_done(rfp_id):
            structure_recorded.append(rfp_id)

        contradiction_recorded: list[str] = []

        async def _record_contradiction_done(rfp_id):
            contradiction_recorded.append(rfp_id)

        activity_calls: list[dict] = []

        async def _activity(rfp_id, **kw):
            activity_calls.append({"rfp_id": rfp_id, **kw})

        stub_calls: list[dict] = []

        async def _stubs(d, **kw):
            stub_calls.append(kw)
            return d, ["stub filled"]

        async def _audit_compulsory(_draft, _rfp_text):
            return compulsory_shortfalls or []

        def _merge_compulsory(d, shortfalls):
            if compulsory_merge_result is not None:
                return compulsory_merge_result
            return d, []

        def _collect_text_risks(**_kw):
            return dq_text_risks or []

        scrub_calls: list[str] = []

        def _scrub(content, _rfp_text_arg):
            scrub_calls.append(content)
            if scrub_result is not None:
                return scrub_result
            return content, 0

        hollow_fill_calls: list[dict] = []

        async def _hollow_fill(d, **kw):
            hollow_fill_calls.append(kw)
            if hollow_fill_raises is not None:
                raise hollow_fill_raises
            if hollow_fill_result is not None:
                return hollow_fill_result
            return d, []

        won_fill_recorded: list[str] = []

        async def _record_won_fill_done(rfp_id):
            won_fill_recorded.append(rfp_id)

        money_constraints_calls: list[str] = []

        async def _extract_money_constraints(text):
            money_constraints_calls.append(text)
            if money_constraints_raises is not None:
                raise money_constraints_raises
            return money_constraints if money_constraints is not None else []

        apply_constraints_calls: list[dict] = []

        def _apply_constraints(budget_arg, constraints_arg):
            apply_constraints_calls.append(
                {"budget": budget_arg, "constraints": constraints_arg}
            )
            if apply_constraints_result is not None:
                return apply_constraints_result
            return budget_arg

        def _over_authority(budget_arg):
            return over_authority_flags or []

        def _under_minimum(budget_arg):
            return under_minimum_flags or []

        def _ceiling_mismatches(content, **kw):
            return ceiling_mismatches or []

        extra = {
            "scrub_calls": scrub_calls,
            "hollow_fill_calls": hollow_fill_calls,
            "won_fill_recorded": won_fill_recorded,
            "money_constraints_calls": money_constraints_calls,
            "apply_constraints_calls": apply_constraints_calls,
        }

        with contextlib.ExitStack() as _stack:
            _stack.enter_context(patch(
                "app.core.config.settings.review_fix_section_concurrency",
                concurrency,
            ))
            _stack.enter_context(patch(
                f"{FULFILL}.aload_rfp_for_proposal",
                new=AsyncMock(return_value=(rfp, SimpleNamespace(description="x" * 400), "x" * 400)),
            ))
            _stack.enter_context(patch(
                f"{FULFILL}.load_local_rfp_text",
                return_value=("x" * 400, "", False, [], 0, None),
            ))
            _stack.enter_context(patch(f"{FULFILL}.combine_rfp_text", return_value="RFP BODY " * 100))
            _stack.enter_context(patch(f"{FULFILL}.aget_proposal_draft", new=AsyncMock(return_value=draft)))
            _stack.enter_context(patch(f"{FULFILL}.aget_research_cache", new=AsyncMock(return_value=research)))
            saved_drafts: list[ProposalDraft] = []

            async def _save_draft(d):
                saved_drafts.append(d)

            extra["saved_drafts"] = saved_drafts
            _stack.enter_context(patch(
                f"{FULFILL}.asave_proposal_draft", new=AsyncMock(side_effect=_save_draft)
            ))
            _stack.enter_context(patch(
                "app.services.proposal_generation_cancel.check_generation_cancelled",
                new=AsyncMock(),
            ))
            _stack.enter_context(patch(
                "app.services.proposal_pipeline_checkpoint.record_pipeline_activity",
                new=AsyncMock(side_effect=_activity),
            ))
            _stack.enter_context(patch(
                "app.services.proposal_pipeline_checkpoint.complete_fulfill_scan",
                new=AsyncMock(),
            ))
            _stack.enter_context(patch(
                "app.services.proposal_pipeline_checkpoint.record_targeted_fix_section_done",
                new=AsyncMock(side_effect=_record_done),
            ))
            _stack.enter_context(patch(
                "app.services.proposal_pipeline_checkpoint.record_targeted_fix_structure_done",
                new=AsyncMock(side_effect=_record_structure_done),
            ))
            _stack.enter_context(patch(
                "app.services.proposal_pipeline_checkpoint.record_targeted_fix_contradiction_done",
                new=AsyncMock(side_effect=_record_contradiction_done),
            ))
            _stack.enter_context(patch(
                "app.services.proposal_fulfill_rfp_structure.apply_rfp_section_order_pass",
                new=AsyncMock(side_effect=_order),
            ))
            _stack.enter_context(patch(
                "app.services.proposal_kb_fact_checker._fact_check_one_section",
                new=AsyncMock(side_effect=_fact_check),
            ))
            _stack.enter_context(patch(
                "app.services.proposal_blocker_prevention.apply_feedback_blocker_suite",
                new=AsyncMock(side_effect=_blocker),
            ))
            _stack.enter_context(patch(
                "app.services.proposal_draft_structure_stubs.section_needs_presubmit_fill",
                return_value=needs_fill,
            ))
            stub_call = _stack.enter_context(patch(
                "app.services.proposal_draft_structure_stubs.draft_rfp_structure_stubs",
                # autospec: a wrong keyword here is a TypeError in the test, not
                # a crash mid-run after the LLM calls have already been paid for.
                autospec=True,
                side_effect=_stubs,
            ))
            _stack.enter_context(patch(
                f"{FULFILL}.run_presubmit_review_with_manual_flags",
                side_effect=_review,
            ))
            _stack.enter_context(patch(
                f"{FULFILL}.asave_research_cache",
                new=AsyncMock(side_effect=_save_research),
            ))
            _stack.enter_context(patch(
                "app.services.proposal_rfp_compulsory_content."
                "audit_draft_against_rfp_compulsory_content",
                new=AsyncMock(side_effect=_audit_compulsory),
            ))
            _stack.enter_context(patch(
                "app.services.proposal_rfp_compulsory_content.merge_compulsory_gap_stubs",
                side_effect=_merge_compulsory,
            ))
            _stack.enter_context(patch(
                "app.services.proposal_scan_dq_orchestrator.collect_rfp_text_dq_risks",
                side_effect=_collect_text_risks,
            ))
            _stack.enter_context(patch(
                "app.services.proposal_verify_optional_scrub."
                "strip_placeholder_tags_not_required_by_rfp",
                side_effect=_scrub,
            ))
            _stack.enter_context(patch(
                "app.services.proposal_hollow_kb_fill.fill_hollow_sections_for_pipeline",
                new=AsyncMock(side_effect=_hollow_fill),
            ))
            _stack.enter_context(patch(
                "app.services.proposal_pipeline_checkpoint.record_targeted_fix_won_fill_done",
                new=AsyncMock(side_effect=_record_won_fill_done),
            ))
            _stack.enter_context(patch(
                "app.services.evidence_trust.rfp_money_constraints."
                "extract_rfp_money_constraints_with_llm_fallback",
                new=AsyncMock(side_effect=_extract_money_constraints),
            ))
            _stack.enter_context(patch(
                "app.services.evidence_trust.rfp_money_constraints."
                "apply_constraints_to_budget_fields",
                side_effect=_apply_constraints,
            ))
            _stack.enter_context(patch(
                "app.services.evidence_trust.rfp_money_constraints."
                "collect_over_authority_flags",
                side_effect=_over_authority,
            ))
            _stack.enter_context(patch(
                "app.services.evidence_trust.rfp_money_constraints."
                "collect_under_minimum_flags",
                side_effect=_under_minimum,
            ))
            _stack.enter_context(patch(
                "app.services.evidence_trust.rfp_money_constraints."
                "collect_invented_ceiling_mismatches",
                side_effect=_ceiling_mismatches,
            ))
            result = await mod._run_fulfill_rfp_gaps_body(
                "r1", use_llm=True, scan_profile="targeted_fix"
            )
            del stub_call
        return (
            result,
            checked,
            recorded,
            stub_calls,
            order_calls,
            structure_recorded,
            review_calls,
            saved_research,
            blocker_calls,
            contradiction_recorded,
            activity_calls,
            extra,
        )

    async def test_fresh_run_reviews_every_section(self):
        (
            (_review, _research_out, _draft_out, report),
            checked,
            recorded,
            stubs,
            order_calls,
            structure_recorded,
            _review_calls,
            _saved_research,
            blocker_calls,
            contradiction_recorded,
            activity_calls,
            _extra,
        ) = await self._run(
            # concurrency=1: this test pins the strictly per-section step
            # numbering. Batching (the shipped default) deliberately collapses a
            # batch into ONE activity call — covered by the concurrency tests.
            concurrency=1,
        )
        self.assertEqual(checked, ["sec-a", "sec-b", "sec-c"])
        self.assertEqual(recorded, ["sec-a", "sec-b", "sec-c"])
        self.assertEqual(report["mode"], "targeted_fix")
        self.assertIn("checked sec-a", report["logs"])
        # Nothing was hollow, so no stub-fill LLM call was made.
        self.assertEqual(stubs, [])
        # Structure pass ran once and was checkpointed.
        self.assertEqual(len(order_calls), 1)
        self.assertEqual(structure_recorded, ["r1"])
        # None of the sections were hollow or marked insufficient evidence.
        self.assertEqual(report["manualReview"], [])
        # 3 dynamic sections: 3 in-loop blocker calls + 1 cross-section pass.
        self.assertEqual(len(blocker_calls), 4)
        self.assertEqual(contradiction_recorded, ["r1"])
        # One continuous step space: 2 prep stages + 3 reviewed sections + 3
        # finishing stages = 8 total. The 3 sections are steps 3, 4, 5 — not
        # positional indices padded by any skipped static block, and not a
        # dense 1..3 counter isolated from the prep/finish stages either.
        per_section = [
            a for a in activity_calls if a.get("detail") == "Scanning section against RFP requirements..."
        ]
        self.assertEqual([a["step_index"] for a in per_section], [3, 4, 5])
        self.assertTrue(all(a["step_total"] == 8 for a in per_section))

    async def test_resume_skips_the_sections_already_reviewed(self):
        (
            (_r, _rc, _d, report),
            checked,
            recorded,
            _stubs,
            order_calls,
            _sr,
            _review_calls,
            _saved_research,
            _blocker_calls,
            _contradiction_recorded,
            _activity_calls,
            _extra,
        ) = await self._run(done=["sec-a", "sec-b"], structure_done=True)
        # The point of the checkpoint: no re-scan (and no re-spend) on 1-2.
        self.assertEqual(checked, ["sec-c"])
        self.assertEqual(recorded, ["sec-c"])
        self.assertTrue(any("Resume: 2 section(s)" in line for line in report["logs"]))
        # The ~4-LLM-call structure pass is not paid for a second time.
        self.assertEqual(order_calls, [])
        self.assertTrue(
            any("structure/order pass already done" in line for line in report["logs"])
        )


    async def test_hollow_section_calls_the_stub_filler_with_a_valid_signature(self):
        (
            (_r, _rc, _d, report),
            _checked,
            _recorded,
            stubs,
            _oc,
            _sr,
            _review_calls,
            _saved_research,
            _blocker_calls,
            _contradiction_recorded,
            _activity_calls,
            _extra,
        ) = await self._run(needs_fill=True)
        self.assertEqual(len(stubs), 3)
        self.assertEqual(stubs[0]["rfp_id"], "r1")
        self.assertEqual(stubs[0]["max_sections"], 1)
        self.assertIn("stub filled", report["logs"])

    async def test_still_hollow_sections_are_flagged_for_manual_review(self):
        # section_needs_presubmit_fill is patched True for every section, and
        # the stub filler (also patched) does not actually fill anything in —
        # the returned draft's sections stay hollow, so each one should be
        # detected as a KB gap and flagged for manual review.
        (
            (review, research_out, _d, report),
            _checked,
            _recorded,
            stubs,
            _oc,
            _sr,
            review_calls,
            saved_research,
            _blocker_calls,
            _contradiction_recorded,
            _activity_calls,
            _extra,
        ) = await self._run(needs_fill=True)

        self.assertEqual(len(stubs), 3)
        self.assertEqual(len(report["manualReview"]), 3)
        for gap in report["manualReview"]:
            self.assertEqual(
                gap["reason"],
                "Section is still empty / heading-only — no zö KB evidence to fill it.",
            )
        self.assertTrue(
            any("flagged for manual review" in line for line in report["logs"])
        )

        self.assertEqual(len(review_calls), 1)
        extra_issues = review_calls[0]["extra_issues"]
        self.assertEqual(len(extra_issues), 3)
        for issue in extra_issues:
            self.assertEqual(issue.category, "Knowledge base gap")
            self.assertEqual(issue.severity, "critical")

        # The review built from those extra issues is persisted to the
        # research cache.
        self.assertIs(review, saved_research[-1].presubmit_review)
        self.assertIs(research_out.presubmit_review, review)


    async def test_leftover_verify_tags_are_flagged_for_manual_review(self):
        # Section is filled, but a [VERIFY: ...] tag means the KB had no source
        # for that fact — a human has to supply it, so flag, do not ship silently.
        (
            (_review, _rc, _d, report),
            _checked,
            _recorded,
            _stubs,
            _oc,
            _sr,
            review_calls,
            _saved,
            _blocker_calls,
            _contradiction_recorded,
            _activity_calls,
            _extra,
        ) = await self._run(
            section_content="Our team delivered [VERIFY: client count — not in KB] projects."
        )

        self.assertEqual(len(report["manualReview"]), 3)
        for gap in report["manualReview"]:
            self.assertIn("[VERIFY:", gap["reason"])
        extra_issues = review_calls[0]["extra_issues"]
        self.assertEqual(len(extra_issues), 3)
        for issue in extra_issues:
            self.assertEqual(issue.category, "Knowledge base gap")
            # Filled-but-unverified is a warning; empty section is critical.
            self.assertEqual(issue.severity, "warning")


    async def test_static_sections_1_3_are_skipped_by_the_per_section_review(self):
        (
            (_review, _rc, _d, report),
            checked,
            recorded,
            _stubs,
            _oc,
            _sr,
            _review_calls,
            _saved,
            blocker_calls,
            contradiction_recorded,
            activity_calls,
            _extra,
        ) = await self._run(
            # concurrency=1: this test pins the strictly per-section step
            # numbering. Batching (the shipped default) deliberately collapses a
            # batch into ONE activity call — covered by the concurrency tests.
            concurrency=1,
            section_ids=(
                "section-1-who-we-are",
                "section-2-team-overview",
                "section-3-our-work",
                "dyn-a",
                "dyn-b",
            )
        )
        # Only the two RFP-driven (dynamic) tabs are fact-checked.
        self.assertEqual(checked, ["dyn-a", "dyn-b"])
        self.assertEqual(recorded, ["dyn-a", "dyn-b"])
        self.assertTrue(
            any(
                "Static Sections 1-3 (3 tab(s)) skipped" in line
                for line in report["logs"]
            )
        )
        # 2 dynamic sections: 2 in-loop blocker calls + 1 cross-section pass.
        self.assertEqual(len(blocker_calls), 3)
        self.assertEqual(contradiction_recorded, ["r1"])

        # One continuous step space: 2 prep stages + 2 reviewed (dynamic)
        # sections + 3 finishing stages = 7 total. With 3 static + 2 dynamic
        # sections, the per-section activity must report step_index 3, then
        # 4 (NOT positional indices 4/5 out of a total of 5, and not a dense
        # 1/2 counter isolated from the prep stages).
        per_section = [
            a for a in activity_calls if a.get("detail") == "Scanning section against RFP requirements..."
        ]
        self.assertEqual([a["step_index"] for a in per_section], [3, 4])
        self.assertTrue(all(a["step_total"] == 7 for a in per_section))

        # The structure pass and the cross-section pass both emit their own
        # activity, distinct from the per-section chips.
        self.assertTrue(
            any(
                a["label"] == "Reviewing proposal sections against the RFP"
                for a in activity_calls
            )
        )
        self.assertTrue(
            any(
                a["label"] == "Checking contradictions across sections"
                and a["step_index"] == 5
                and a["step_total"] == 7
                for a in activity_calls
            )
        )

    async def test_resume_skips_the_cross_section_contradiction_pass(self):
        (
            (_review, _rc, _d, report),
            checked,
            recorded,
            _stubs,
            _oc,
            _sr,
            _review_calls,
            _saved,
            blocker_calls,
            contradiction_recorded,
            activity_calls,
            _extra,
        ) = await self._run(contradiction_done=True)
        self.assertEqual(checked, ["sec-a", "sec-b", "sec-c"])
        self.assertEqual(recorded, ["sec-a", "sec-b", "sec-c"])
        # Only the 3 in-loop blocker calls run — the cross-section pass is skipped.
        self.assertEqual(len(blocker_calls), 3)
        self.assertEqual(contradiction_recorded, [])
        self.assertTrue(
            any(
                "cross-section contradiction pass already done" in line
                for line in report["logs"]
            )
        )
        self.assertFalse(
            any(
                a["label"] == "Checking contradictions across sections"
                for a in activity_calls
            )
        )

    async def test_structure_activity_not_emitted_when_checkpoint_already_set(self):
        (
            (_review, _rc, _d, _report),
            _checked,
            _recorded,
            _stubs,
            order_calls,
            _sr,
            _review_calls,
            _saved,
            _blocker_calls,
            _contradiction_recorded,
            activity_calls,
            _extra,
        ) = await self._run(structure_done=True)
        # The structure pass itself is skipped (already checkpointed)...
        self.assertEqual(order_calls, [])
        # ...and so is its activity label.
        self.assertFalse(
            any(
                a["label"] == "Reviewing proposal sections against the RFP"
                for a in activity_calls
            )
        )

    async def test_disqualification_risks_are_flagged_and_stubs_merged(self):
        ask = CompulsoryContentAsk(
            kind="case_studies", minimum=3, rfp_quote="submit 3 case studies",
            pass_fail=True,
        )
        shortfall = CompulsoryShortfall(
            ask=ask,
            found=2,
            message=(
                "Qualification / non-responsive risk: case studies — "
                "manuscript has 2, RFP requires 3."
            ),
        )
        merged_draft = _draft("sec-a", "sec-b", "sec-c", "rfp-compulsory-gap-case_studies")
        (
            (_review, _rc, _d, report),
            _checked,
            _recorded,
            _stubs,
            _oc,
            _sr,
            review_calls,
            _saved,
            _blocker_calls,
            _contradiction_recorded,
            activity_calls,
            _extra,
        ) = await self._run(
            compulsory_shortfalls=[shortfall],
            compulsory_merge_result=(merged_draft, [shortfall.message]),
            dq_text_risks=["RFP requires sealed original signature."],
        )

        self.assertEqual(
            report["disqualificationRisks"],
            [
                shortfall.message,
                "RFP requires sealed original signature.",
            ],
        )
        self.assertIn(shortfall.message, report["logs"])
        self.assertTrue(
            any(
                line == f"Disqualification risk — {shortfall.message}"
                for line in report["logs"]
            )
        )
        self.assertTrue(
            any(
                "2 disqualification risk(s) flagged for manual review" in line
                for line in report["logs"]
            )
        )

        extra_issues = review_calls[0]["extra_issues"]
        dq_issues = [i for i in extra_issues if i.category == "Disqualification risk"]
        self.assertEqual(len(dq_issues), 2)
        for issue in dq_issues:
            self.assertEqual(issue.severity, "critical")

        self.assertTrue(
            any(a["label"] == "Checking RFP-mandated sections" for a in activity_calls)
        )

    async def test_duplicate_disqualification_risks_are_deduplicated(self):
        (
            (_review, _rc, _d, report),
            _checked,
            _recorded,
            _stubs,
            _oc,
            _sr,
            review_calls,
            _saved,
            _blocker_calls,
            _contradiction_recorded,
            _activity_calls,
            _extra,
        ) = await self._run(
            dq_text_risks=[
                "RFP requires sealed original signature.",
                "rfp requires sealed original signature.",
            ]
        )

        self.assertEqual(
            report["disqualificationRisks"],
            ["RFP requires sealed original signature."],
        )
        extra_issues = review_calls[0]["extra_issues"]
        dq_issues = [i for i in extra_issues if i.category == "Disqualification risk"]
        self.assertEqual(len(dq_issues), 1)

    async def test_dq_audit_still_runs_on_resume_when_structure_already_done(self):
        (
            (_review, _rc, _d, report),
            checked,
            recorded,
            _stubs,
            order_calls,
            _sr,
            review_calls,
            _saved,
            _blocker_calls,
            _contradiction_recorded,
            _activity_calls,
            _extra,
        ) = await self._run(
            structure_done=True,
            dq_text_risks=["RFP requires sealed original signature."],
        )
        # Structure pass itself is still skipped on resume...
        self.assertEqual(order_calls, [])
        # ...but the DQ audit still ran and still surfaced its risk.
        self.assertEqual(
            report["disqualificationRisks"],
            ["RFP requires sealed original signature."],
        )
        extra_issues = review_calls[0]["extra_issues"]
        dq_issues = [i for i in extra_issues if i.category == "Disqualification risk"]
        self.assertEqual(len(dq_issues), 1)

    async def test_optional_placeholder_scrub_replaces_section_content_and_is_saved(self):
        (
            (_review, _rc, _d, report),
            _checked,
            _recorded,
            _stubs,
            _oc,
            _sr,
            _review_calls,
            _saved,
            _blocker_calls,
            _contradiction_recorded,
            _activity_calls,
            extra,
        ) = await self._run(scrub_result=("scrubbed body", 2))

        self.assertTrue(
            any(
                "Removed 2 optional placeholder tag(s) not required by the RFP" in line
                for line in report["logs"]
            )
        )
        saved_drafts = extra["saved_drafts"]
        self.assertTrue(saved_drafts)
        last_draft = saved_drafts[-1]
        self.assertTrue(
            all(s.content == "scrubbed body" for s in last_draft.sections)
        )

    async def test_won_proposal_gap_fill_runs_once_on_fresh_run_and_is_skipped_on_resume(self):
        (
            (_review, _rc, _d, report),
            _checked,
            _recorded,
            _stubs,
            _oc,
            _sr,
            _review_calls,
            _saved,
            _blocker_calls,
            _contradiction_recorded,
            _activity_calls,
            extra,
        ) = await self._run(hollow_fill_result=(_draft("sec-a", "sec-b", "sec-c"), ["filled sec-a from WON"]))

        self.assertEqual(len(extra["hollow_fill_calls"]), 1)
        self.assertEqual(extra["won_fill_recorded"], ["r1"])
        self.assertIn("filled sec-a from WON", report["logs"])

        (
            (_review2, _rc2, _d2, _report2),
            _checked2,
            _recorded2,
            _stubs2,
            _oc2,
            _sr2,
            _review_calls2,
            _saved2,
            _blocker_calls2,
            _contradiction_recorded2,
            _activity_calls2,
            extra2,
        ) = await self._run(won_fill_done=True)

        self.assertEqual(extra2["hollow_fill_calls"], [])
        self.assertEqual(extra2["won_fill_recorded"], [])
        self.assertTrue(
            any(
                "WON-proposal gap fill already done — skipped" in line
                for line in _report2["logs"]
            )
        )

    async def test_won_proposal_gap_fill_failure_does_not_fail_the_run(self):
        (
            (_review, _rc, _d, report),
            checked,
            recorded,
            _stubs,
            _oc,
            _sr,
            _review_calls,
            _saved,
            _blocker_calls,
            contradiction_recorded,
            _activity_calls,
            extra,
        ) = await self._run(hollow_fill_raises=RuntimeError("kb unavailable"))

        # The earlier per-section checkpoints are intact — the run still
        # completed and reviewed every section despite the KB failure.
        self.assertEqual(checked, ["sec-a", "sec-b", "sec-c"])
        self.assertEqual(recorded, ["sec-a", "sec-b", "sec-c"])
        self.assertEqual(contradiction_recorded, ["r1"])
        self.assertEqual(extra["won_fill_recorded"], [])
        self.assertTrue(
            any("WON-proposal gap fill skipped" in line for line in report["logs"])
        )
        self.assertEqual(report["mode"], "targeted_fix")

    async def test_no_money_constraints_found_means_no_budget_issues(self):
        (
            (_review, _rc, _d, report),
            _checked,
            _recorded,
            _stubs,
            _oc,
            _sr,
            _review_calls,
            _saved,
            _blocker_calls,
            _contradiction_recorded,
            _activity_calls,
            _extra,
        ) = await self._run()

        self.assertEqual(report["budgetIssues"], [])
        self.assertTrue(
            any(
                "RFP states no money constraints this pass could find" in line
                for line in report["logs"]
            )
        )

    async def test_over_authority_flag_becomes_critical_budget_issue(self):
        budget = ProposalBudget(
            rfpId="r1",
            updatedAt="2026-09-03T00:00:00Z",
            agencyRevenueEstimate=90000,
        )
        flag = (
            "[PRICING FLAG: DISQUALIFY RISK — agency revenue $90,000.00 exceeds "
            "RFP hard fee NTE $50,000.00 — scope down or confirm with Sonja]"
        )
        (
            (_review, _rc, _d, report),
            _checked,
            _recorded,
            _stubs,
            _oc,
            _sr,
            review_calls,
            _saved,
            _blocker_calls,
            _contradiction_recorded,
            _activity_calls,
            _extra,
        ) = await self._run(
            budget=budget,
            money_constraints=["hard_fee_nte"],
            over_authority_flags=[flag],
        )

        self.assertEqual(report["budgetIssues"], [flag])
        extra_issues = review_calls[0]["extra_issues"]
        budget_issue_entries = [
            i for i in extra_issues if i.category == "Budget vs RFP"
        ]
        self.assertEqual(len(budget_issue_entries), 1)
        self.assertEqual(budget_issue_entries[0].severity, "critical")
        self.assertEqual(budget_issue_entries[0].message, flag)

    async def test_manual_fill_line_item_flags_manual_review_without_dropping_kb_gaps(self):
        budget = ProposalBudget(
            rfpId="r1",
            updatedAt="2026-09-03T00:00:00Z",
            agencyRevenueEstimate=10000,
            lineItems=[
                BudgetLineItem(
                    id="li-1",
                    category="Media",
                    description="Paid Social Media",
                    isManualFill=True,
                )
            ],
        )
        (
            (_review, _rc, _d, report),
            _checked,
            _recorded,
            _stubs,
            _oc,
            _sr,
            _review_calls,
            _saved,
            _blocker_calls,
            _contradiction_recorded,
            _activity_calls,
            _extra,
        ) = await self._run(
            budget=budget,
            money_constraints=["hard_fee_nte"],
            needs_fill=True,
        )

        # KB gaps from the still-hollow sections are still present...
        kb_gap_entries = [
            m for m in report["manualReview"] if "zö KB evidence" in m["reason"]
        ]
        self.assertEqual(len(kb_gap_entries), 3)
        # ...alongside the new budget manual-review entry for the unbound rate.
        budget_manual_entries = [
            m for m in report["manualReview"] if "zö KB rate card" in m["reason"]
        ]
        self.assertEqual(len(budget_manual_entries), 1)
        self.assertIn("Paid Social Media", budget_manual_entries[0]["reason"])
        self.assertIn(
            "not bound to a verified zö KB rate card", budget_manual_entries[0]["reason"]
        )

    async def test_budget_audit_exception_does_not_fail_the_run(self):
        (
            (_review, _rc, _d, report),
            checked,
            recorded,
            _stubs,
            _oc,
            _sr,
            _review_calls,
            _saved,
            _blocker_calls,
            contradiction_recorded,
            _activity_calls,
            _extra,
        ) = await self._run(money_constraints_raises=RuntimeError("extraction unavailable"))

        # Per-section checkpoints from earlier passes are intact — the budget
        # audit failure did not roll back or fail the run.
        self.assertEqual(checked, ["sec-a", "sec-b", "sec-c"])
        self.assertEqual(recorded, ["sec-a", "sec-b", "sec-c"])
        self.assertEqual(contradiction_recorded, ["r1"])
        self.assertEqual(report["budgetIssues"], [])
        self.assertTrue(
            any("Budget-vs-RFP audit skipped" in line for line in report["logs"])
        )
        self.assertEqual(report["mode"], "targeted_fix")


    async def test_continuous_step_sequence_across_the_whole_run(self):
        # One continuous step space so the UI can always answer "what step
        # am I on?": 2 prep stages, then one step per reviewed section, then
        # 3 finishing stages. With 3 dynamic sections that is 2 + 3 + 3 = 8
        # steps total, and every activity call in the run must report a
        # step_index somewhere in 1..8 with a CONSTANT step_total of 8 (no
        # step_index=0/step_total=0 placeholders for the prep/finish stages).
        (
            (_review, _rc, _d, _report),
            _checked,
            _recorded,
            _stubs,
            _oc,
            _sr,
            _review_calls,
            _saved,
            _blocker_calls,
            _contradiction_recorded,
            activity_calls,
            _extra,
        ) = await self._run(
            # concurrency=1: this test pins the strictly per-section step
            # numbering. Batching (the shipped default) deliberately collapses a
            # batch into ONE activity call — covered by the concurrency tests.
            concurrency=1,
        )

        self.assertEqual([a["step_index"] for a in activity_calls], [1, 2, 3, 4, 5, 6, 7, 8])
        self.assertTrue(all(a["step_total"] == 8 for a in activity_calls))
        self.assertEqual(
            [a["label"] for a in activity_calls],
            [
                "Checking RFP-mandated sections",
                "Reviewing proposal sections against the RFP",
                "SEC-A",
                "SEC-B",
                "SEC-C",
                "Checking contradictions across sections",
                "Filling gaps from past won proposals",
                "Checking budget against RFP limits",
            ],
        )

    async def test_step_total_never_decreases_across_the_run(self):
        # The DQ audit's compulsory-gap stub (merged in AFTER the first
        # activity call reports an early, pre-merge step_total) grows the
        # section list from 3 to 4, so the real review_total recomputed just
        # before the per-section loop is bigger than the early estimate.
        # step_total must never go DOWN mid-run — a shrinking total looks
        # broken — so later calls report the larger, recomputed total instead
        # of silently reverting to the smaller early one.
        ask = CompulsoryContentAsk(
            kind="case_studies", minimum=3, rfp_quote="submit 3 case studies",
            pass_fail=True,
        )
        shortfall = CompulsoryShortfall(
            ask=ask,
            found=2,
            message=(
                "Qualification / non-responsive risk: case studies — "
                "manuscript has 2, RFP requires 3."
            ),
        )
        merged_draft = _draft("sec-a", "sec-b", "sec-c", "rfp-compulsory-gap-case_studies")
        (
            (_review, _rc, _d, _report),
            _checked,
            _recorded,
            _stubs,
            _oc,
            _sr,
            _review_calls,
            _saved,
            _blocker_calls,
            _contradiction_recorded,
            activity_calls,
            _extra,
        ) = await self._run(
            compulsory_shortfalls=[shortfall],
            compulsory_merge_result=(merged_draft, [shortfall.message]),
        )

        totals = [a["step_total"] for a in activity_calls]
        self.assertTrue(
            all(later >= earlier for earlier, later in zip(totals, totals[1:])),
            f"step_total decreased somewhere in {totals}",
        )
        # The early prep-stage report (before the merge) used the smaller,
        # pre-merge estimate...
        self.assertEqual(activity_calls[0]["step_total"], 8)
        # ...while everything from the per-section loop onward uses the
        # bigger, recomputed total (2 prep + 4 reviewed sections + 3 finish).
        self.assertEqual(activity_calls[-1]["step_total"], 9)
        self.assertGreater(activity_calls[-1]["step_total"], activity_calls[0]["step_total"])

    async def test_batched_review_checks_every_section_exactly_once_in_draft_order(self):
        # 5 sections, concurrency 3 -> batches of [0,1,2] then [3,4]. Section
        # "sec-b" is made to finish its (concurrent) fact-check LAST within
        # its batch, but the sequential tail must still apply / checkpoint in
        # draft order, not completion order.
        section_ids = ("sec-a", "sec-b", "sec-c", "sec-d", "sec-e")
        (
            (_review, _rc, _d, _report),
            checked,
            recorded,
            _stubs,
            _oc,
            _sr,
            _review_calls,
            _saved,
            _blocker_calls,
            _contradiction_recorded,
            _activity_calls,
            _extra,
        ) = await self._run(
            section_ids=section_ids,
            concurrency=3,
            fact_check_delays={"sec-a": 0.02, "sec-b": 0.06, "sec-c": 0.0},
        )
        # Every section fact-checked exactly once.
        self.assertEqual(sorted(checked), sorted(section_ids))
        self.assertEqual(len(checked), len(section_ids))
        # Every section checkpointed exactly once, and in draft order — not
        # the order the concurrent fact-checks happened to complete in.
        self.assertEqual(recorded, list(section_ids))

    async def test_one_section_exception_does_not_abort_the_batch(self):
        section_ids = ("sec-a", "sec-b", "sec-c", "sec-d", "sec-e")
        (
            (_review, _rc, _d, report),
            checked,
            recorded,
            _stubs,
            _oc,
            _sr,
            _review_calls,
            _saved,
            _blocker_calls,
            contradiction_recorded,
            _activity_calls,
            _extra,
        ) = await self._run(
            section_ids=section_ids,
            concurrency=3,
            fact_check_exceptions={"sec-b": RuntimeError("kb timeout")},
        )
        # The other 4 sections were still reviewed and checkpointed...
        self.assertEqual(checked, ["sec-a", "sec-c", "sec-d", "sec-e"])
        self.assertEqual(recorded, list(section_ids))
        # ...and the run still reached the cross-section pass at the end.
        self.assertEqual(contradiction_recorded, ["r1"])
        # ...and a report line names the failed section.
        self.assertTrue(
            any(
                "Fact-check failed for SEC-B" in line
                for line in report["logs"]
            )
        )
        self.assertEqual(report["mode"], "targeted_fix")

    async def test_cancellation_from_one_section_propagates_out_of_the_run(self):
        from app.services.proposal_generation_cancel import ProposalGenerationCancelled

        section_ids = ("sec-a", "sec-b", "sec-c")
        with self.assertRaises(ProposalGenerationCancelled):
            await self._run(
                section_ids=section_ids,
                concurrency=3,
                fact_check_exceptions={"sec-b": ProposalGenerationCancelled()},
            )

    async def test_batch_level_durability_on_interruption(self):
        # 5 sections, concurrency 3 -> first batch is [sec-a, sec-b, sec-c].
        # Kill the run right as the sequential tail tries to checkpoint the
        # 4th section (the first section of the SECOND batch) — proving the
        # first batch's 3 sections are already fully checkpointed/durable
        # before the second batch's fan-out is even attempted.
        section_ids = ("sec-a", "sec-b", "sec-c", "sec-d", "sec-e")
        recorded_out: list[str] = []
        with self.assertRaises(RuntimeError):
            await self._run(
                section_ids=section_ids,
                concurrency=3,
                record_done_raise_after=4,
                recorded_out=recorded_out,
            )
        # Exactly the first batch's 3 section ids were checkpointed before
        # the interruption hit — the second batch's fan-out never got far
        # enough to checkpoint anything.
        self.assertEqual(recorded_out, ["sec-a", "sec-b", "sec-c"])


if __name__ == "__main__":
    unittest.main()
