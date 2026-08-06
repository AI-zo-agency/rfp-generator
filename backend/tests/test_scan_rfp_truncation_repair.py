"""Task 12: Scan-RFP repairs truncated sections instead of only reporting them.

Root defect (verified against a real user's live Scan-RFP run):
``run_verify_scrub_only_scan`` (the ONLY path the button calls — see
``proposal_fulfill_rfp_gaps.run_fulfill_rfp_gaps``) detected truncation via
the T1 scanner (``proposal_t1_validators.scan_truncation_artifacts``) and
reported it in the banner, but never repaired it.
``repair_truncated_manuscript_sections`` (proposal_fulfill_truncation_repair.py)
existed, but only ran on ``mode="full"``, which the button never sends — and
even there it skipped team bios / case studies via
``fulfill_scan_preserves_section``, which is exactly what the user's live run
showed cut off mid-sentence (5 bios + 2 case studies, t1.truncation.
mid_sentence_cutoff / currency_fragment).

``repair_truncated_sections_from_kb`` (same module) is the fix wired into the
real ``verify_scrub_only`` path: for every section T1 flags as truncated, it
retrieves KB evidence (``retrieve_for_section`` — zero LLM calls) and asks the
LLM to complete ONLY the cut-off tail, never invent a missing fact (drop a
narrow ``[VERIFY: field]`` instead), and never rewrite content that already
reads complete. A word-prefix guard (``_shared_word_prefix_ratio``) rejects
any response that reads like a rewrite rather than a completion, so this is
safe to run on bios/case studies even though
``repair_truncated_manuscript_sections`` is not.

This file drives the REAL entry point end to end (mirrors
test_scan_rfp_ledger_add_drafting.py's pattern: sqlite instead of Supabase,
LLM + KB retrieval stubbed by node_name — never a live network call) and
proves:

  1. A genuinely truncated section with KB evidence available gets completed:
     no longer truncated, real fact from evidence appears, exactly ONE LLM
     call (node_name="scan_truncation_kb_repair") for that section.
  2. A truncated section the LLM cannot ground from the KB (or that comes
     back looking like a rewrite rather than a completion) is left alone and
     reported "still truncated" — never fabricated.
  3. Repaired vs. still-truncated are reported as separate counts/titles.
  4. A second click is idempotent: the repaired section fires no further LLM
     calls (it no longer trips the T1 scanner).
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core import config
from app.models.proposal import EvidenceItem, ProposalDraft, ProposalSection
from app.models.rfp import RfpRecord
from app.services import proposal_repository as repo

# Cut off mid-sentence — no terminal punctuation — trips
# t1.truncation.mid_sentence_cutoff.
TRUNCATED_BIO_CONTENT = (
    "Jordan Ellis has led public-sector marketing engagements for over a "
    "decade and holds a Project Management Professional certification "
    "issued by"
)

REPAIRED_BIO_CONTENT = (
    "Jordan Ellis has led public-sector marketing engagements for over a "
    "decade and holds a Project Management Professional certification "
    "issued by the Project Management Institute in 2016."
)

# Also mid-sentence cutoff, but no KB evidence will be provided for this one.
TRUNCATED_CASE_STUDY_CONTENT = (
    "Our team completed a full brand refresh for the City of Fernvale, "
    "including a new visual identity and a public-facing campaign that "
    "drove voter turnout up by"
)

COMPLETE_SECTION_CONTENT = (
    "We carry $2M general liability insurance as required by Section 1.5 "
    "of the RFP, maintained continuously throughout the contract term."
)


def _rfp(rfp_id: str, **overrides) -> RfpRecord:
    fields = dict(
        id=rfp_id,
        title="Regional Tourism Marketing Services",
        client="City of Fernvale",
        dueDate="2026-09-01",
        receivedDate="2026-08-01",
        lastActivity="2026-08-05",
        lastActivityNote="note",
        goNoGo="go",
        description=" ".join(["background context sentence about the tourism scope"] * 6),
        pageLimit=None,
    )
    fields.update(overrides)
    return RfpRecord(**fields)


class _RealDbTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._db = Path(self._tmpdir.name) / "scan-rfp-truncation-repair.db"
        self._patchers = [
            patch.object(config.settings, "database_path", self._db),
            patch.object(repo, "_use_supabase", return_value=False),
            patch("app.services.rfp_repository._use_supabase", return_value=False),
            patch("app.services.supabase_db.use_supabase_db", return_value=False),
        ]
        for p in self._patchers:
            p.start()
        repo.init_proposal_db()

    async def asyncTearDown(self) -> None:
        for p in reversed(self._patchers):
            p.stop()
        self._tmpdir.cleanup()

    async def _seed(self, rfp_id: str) -> None:
        from app.services.rfp_repository import upsert_rfp

        upsert_rfp(_rfp(rfp_id))
        draft = ProposalDraft(
            rfpId=rfp_id,
            sections=[
                ProposalSection(
                    id="section-2-bio-jordan",
                    title="Jordan Ellis — Bio",
                    content=TRUNCATED_BIO_CONTENT,
                ),
                ProposalSection(
                    id="section-3-work-fernvale",
                    title="Case Study — City of Fernvale",
                    content=TRUNCATED_CASE_STUDY_CONTENT,
                ),
                ProposalSection(
                    id="sec-insurance",
                    title="Insurance",
                    content=COMPLETE_SECTION_CONTENT,
                ),
            ],
            updatedAt="2026-08-06T00:00:00Z",
        )
        await repo.asave_proposal_draft(draft)


def _fake_retrieve_for_section_with_evidence(entry, *, rfp_client="", start_index=1, claim=None):
    if entry.section_id == "section-2-bio-jordan":
        return [
            EvidenceItem(
                id="E1",
                source="Jordan_Ellis_Resume.pdf",
                excerpt=(
                    "Jordan Ellis holds a Project Management Professional "
                    "(PMP) certification issued by the Project Management "
                    "Institute in 2016."
                ),
                sectionIds=[entry.section_id],
                chunkKey="jordan-pmp",
            )
        ]
    return []


async def _async_fake_retrieve_for_section_with_evidence(entry, **kwargs):
    return _fake_retrieve_for_section_with_evidence(entry, **kwargs)


def _fake_chat_json(call_log: list[str]):
    """Stub for llm.chat_json keyed by node_name — never a live call."""

    async def _fake(messages, *, max_tokens=None, temperature=0.2, tier=None, node_name=None):
        call_log.append(node_name or "")
        if node_name == "scan_truncation_kb_repair":
            user_msg = messages[-1]["content"]
            if "Jordan Ellis" in user_msg:
                return ({"content": REPAIRED_BIO_CONTENT}, "openrouter")
            # No KB evidence for the case study — the model must not invent
            # the missing turnout figure. It returns a narrow [VERIFY] tag
            # instead of the real number, which still passes the "not
            # truncated" check (ends with terminal punctuation) but is
            # exercised here as an alternate acceptable outcome; this test
            # instead simulates a REJECTED completion (reads like a rewrite)
            # to prove the guard leaves it truncated.
            return (
                {
                    "content": (
                        "Our firm has extensive experience in municipal "
                        "branding work across the region."
                    )
                },
                "openrouter",
            )
        raise AssertionError(f"unexpected node_name in test: {node_name!r}")

    return _fake


class TruncationRepairDrivesRealEntryPointTests(_RealDbTestCase):
    async def test_kb_grounded_section_is_repaired_ungrounded_section_stays_truncated(
        self,
    ) -> None:
        from app.services.proposal_fulfill_rfp_gaps import run_fulfill_rfp_gaps

        rfp_id = "rfp-truncation-repair"
        await self._seed(rfp_id)

        call_log: list[str] = []
        with (
            patch("app.services.llm.is_configured", return_value=True),
            patch(
                "app.services.llm.chat_json",
                new=_fake_chat_json(call_log),
            ),
            patch(
                "app.services.proposal_intelligence.jit_retrieval.retrieve_for_section",
                new=_async_fake_retrieve_for_section_with_evidence,
            ),
        ):
            _review, _research, draft_after, report = await run_fulfill_rfp_gaps(
                rfp_id, mode="verify_scrub_only"
            )

        bio_after = next(s for s in draft_after.sections if s.id == "section-2-bio-jordan")
        case_study_after = next(
            s for s in draft_after.sections if s.id == "section-3-work-fernvale"
        )

        print("\n=== Task 12 — truncation repair through the real entry point ===")
        print(f"bio content = {bio_after.content!r}")
        print(f"case study content (rejected, unchanged) = {case_study_after.content!r}")
        print(f"truncationRepairedCount = {report.get('truncationRepairedCount')}")
        print(f"truncationRepairedSectionTitles = {report.get('truncationRepairedSectionTitles')}")
        print(f"truncatedSectionsCount = {report.get('truncatedSectionsCount')}")
        print(f"truncatedSectionTitles = {report.get('truncatedSectionTitles')}")
        print(f"llm calls = {call_log}")

        # The bio had KB evidence -> repaired with the real fact, no invention.
        self.assertEqual(bio_after.content, REPAIRED_BIO_CONTENT)
        self.assertIn("2016", bio_after.content)
        self.assertEqual(bio_after.status, "generated")

        # The case study had no KB evidence and the model's response reads
        # like a rewrite (fails the word-prefix guard) -> rejected, left
        # exactly as the truncated original, reported still-truncated.
        self.assertEqual(case_study_after.content, TRUNCATED_CASE_STUDY_CONTENT)

        # Reported separately: 1 repaired, 1 still truncated.
        self.assertEqual(report.get("truncationRepairedCount"), 1)
        self.assertEqual(
            report.get("truncationRepairedSectionTitles"), ["Jordan Ellis — Bio"]
        )
        self.assertEqual(report.get("truncatedSectionsCount"), 1)
        self.assertEqual(
            report.get("truncatedSectionTitles"), ["Case Study — City of Fernvale"]
        )

        # The complete, untruncated section was never touched and never
        # sent to the LLM at all.
        insurance_after = next(s for s in draft_after.sections if s.id == "sec-insurance")
        self.assertEqual(insurance_after.content, COMPLETE_SECTION_CONTENT)

        # LLM budget: exactly one call per truncated section (2 truncated
        # sections seeded -> exactly 2 calls, all node_name-routed).
        self.assertEqual(call_log, ["scan_truncation_kb_repair", "scan_truncation_kb_repair"])

    async def test_repaired_section_is_not_retouched_on_a_second_click(self) -> None:
        from app.services.proposal_fulfill_rfp_gaps import run_fulfill_rfp_gaps

        rfp_id = "rfp-truncation-repair-idempotent"
        await self._seed(rfp_id)

        call_log: list[str] = []
        with (
            patch("app.services.llm.is_configured", return_value=True),
            patch("app.services.llm.chat_json", new=_fake_chat_json(call_log)),
            patch(
                "app.services.proposal_intelligence.jit_retrieval.retrieve_for_section",
                new=_async_fake_retrieve_for_section_with_evidence,
            ),
        ):
            await run_fulfill_rfp_gaps(rfp_id, mode="verify_scrub_only")
            calls_after_first = list(call_log)

            _review2, _research2, draft_after2, report2 = await run_fulfill_rfp_gaps(
                rfp_id, mode="verify_scrub_only"
            )

        bio_after2 = next(s for s in draft_after2.sections if s.id == "section-2-bio-jordan")

        print("\n=== Task 12 — idempotence across two clicks ===")
        print(f"calls after first click = {calls_after_first}")
        print(f"calls after second click = {call_log}")

        self.assertEqual(bio_after2.content, REPAIRED_BIO_CONTENT, "must stay repaired")
        # Second click still tries the still-truncated case study (it is a
        # genuine retry candidate — nothing was fixed for it yet), but must
        # NOT re-send the already-repaired bio.
        self.assertEqual(
            call_log[len(calls_after_first) :],
            ["scan_truncation_kb_repair"],
            "second click must only retry the still-truncated section, never "
            "the already-repaired one",
        )
        self.assertEqual(report2.get("truncationRepairedCount"), 0)

    async def test_llm_not_configured_leaves_truncation_unchanged_and_reported(self) -> None:
        from app.services.proposal_fulfill_rfp_gaps import run_fulfill_rfp_gaps

        rfp_id = "rfp-truncation-repair-no-llm"
        await self._seed(rfp_id)

        with patch("app.services.llm.is_configured", return_value=False):
            _review, _research, draft_after, report = await run_fulfill_rfp_gaps(
                rfp_id, mode="verify_scrub_only"
            )

        bio_after = next(s for s in draft_after.sections if s.id == "section-2-bio-jordan")
        self.assertEqual(bio_after.content, TRUNCATED_BIO_CONTENT, "must not raise or half-edit")
        self.assertEqual(report.get("truncationRepairedCount"), 0)
        self.assertEqual(report.get("truncatedSectionsCount"), 2)


# ---------------------------------------------------------------------------
# Combined scenario, run through the real entry point exactly once: a MERGE
# (Fix 1) alongside truncation repair (Fix 2), together, so the banner the
# user actually sees can be built from real numbers instead of hand-picked
# ones. This is the scenario task-12's report string is built from.
# ---------------------------------------------------------------------------

TRUNCATED_BIO_2_CONTENT = (
    "Priya Nadar has managed capital improvement communications for over "
    "twelve years and is a Certified Public Communicator accredited by"
)
REPAIRED_BIO_2_CONTENT = (
    "Priya Nadar has managed capital improvement communications for over "
    "twelve years and is a Certified Public Communicator accredited by the "
    "National Association of Government Communicators."
)
TRUNCATED_CASE_STUDY_2_CONTENT = (
    "Our team delivered a multi-channel outreach campaign for the City of "
    "Umatilla that increased public meeting attendance by"
)


def _fake_chat_json_combined(call_log: list[str]):
    async def _fake(messages, *, max_tokens=None, temperature=0.2, tier=None, node_name=None):
        call_log.append(node_name or "")
        assert node_name == "scan_truncation_kb_repair"
        user_msg = messages[-1]["content"]
        if "Priya Nadar" in user_msg:
            return ({"content": REPAIRED_BIO_2_CONTENT}, "openrouter")
        # No KB evidence for this one — a response that reads like a rewrite
        # (fails the word-prefix guard), so it stays truncated rather than
        # risk an invented turnout number.
        return (
            {"content": "Our firm regularly runs public outreach campaigns for municipal clients."},
            "openrouter",
        )

    return _fake


async def _fake_retrieve_for_section_combined(entry, *, rfp_client="", start_index=1, claim=None):
    if entry.section_id == "section-2-bio-priya":
        return [
            EvidenceItem(
                id="E1",
                source="Priya_Nadar_Resume.pdf",
                excerpt=(
                    "Priya Nadar is a Certified Public Communicator "
                    "accredited by the National Association of Government "
                    "Communicators."
                ),
                sectionIds=[entry.section_id],
                chunkKey="priya-cpc",
            )
        ]
    return []


class MergeAndTruncationRepairCombinedTests(_RealDbTestCase):
    async def test_merged_section_never_reported_truncated_while_repair_runs_alongside_it(
        self,
    ) -> None:
        """(a) a merged-away section must NOT appear as truncated (Fix 1);
        (b) of three genuinely truncated sections, two are KB-repairable and
        one is not (Fix 2) — reported separately."""
        from app.models.proposal import ProposalResearchCache, RfpSectionMap
        from app.models.requirement_ledger import LedgerRequirement, RequirementLedger
        from app.services.proposal_fulfill_rfp_gaps import run_fulfill_rfp_gaps
        from app.services.rfp_repository import upsert_rfp

        rfp_id = "rfp-merge-and-truncation-combined"
        upsert_rfp(_rfp(rfp_id))

        rfp_sections = [
            RfpSectionMap(id="sec-a", title="Section 1.5", evaluationWeight=10),
            RfpSectionMap(id="sec-b", title="Attachments Checklist"),
            RfpSectionMap(id="sec-c", title="Contract Acknowledgment"),
        ]
        ledger = RequirementLedger(
            requirements=[
                LedgerRequirement(
                    id="r-dup",
                    text="Proof of insurance",
                    source="required_content",
                    mandatory=True,
                    satisfiedBy=["sec-a", "sec-b", "sec-c"],
                )
            ]
        )
        draft = ProposalDraft(
            rfpId=rfp_id,
            sections=[
                ProposalSection(
                    id="sec-a", title="Section 1.5",
                    content="We carry $2M general liability insurance as required by Section 1.5.",
                ),
                ProposalSection(
                    id="sec-b", title="Attachments Checklist",
                    content="Insurance coverage of $2M is maintained per Section 1.5.",
                ),
                ProposalSection(
                    id="sec-c", title="Contract Acknowledgment",
                    content="We acknowledge and carry $2M insurance coverage per the contract terms.",
                ),
                ProposalSection(
                    id="section-2-bio-jordan", title="Jordan Ellis — Bio",
                    content=TRUNCATED_BIO_CONTENT,
                ),
                ProposalSection(
                    id="section-2-bio-priya", title="Priya Nadar — Bio",
                    content=TRUNCATED_BIO_2_CONTENT,
                ),
                ProposalSection(
                    id="section-3-work-umatilla", title="Case Study — City of Umatilla",
                    content=TRUNCATED_CASE_STUDY_2_CONTENT,
                ),
            ],
            updatedAt="2026-08-06T00:00:00Z",
        )
        await repo.asave_proposal_draft(draft)
        await repo.asave_research_cache(
            ProposalResearchCache(
                rfpId=rfp_id,
                requirementLedger=ledger,
                rfpSections=rfp_sections,
                updatedAt="2026-08-06T00:00:00Z",
            )
        )

        call_log: list[str] = []

        def _dispatch_retrieve(entry, **kwargs):
            if entry.section_id == "section-2-bio-jordan":
                return _fake_retrieve_for_section_with_evidence(entry, **kwargs)
            return _fake_retrieve_for_section_combined(entry, **kwargs)

        async def _async_dispatch_retrieve(entry, **kwargs):
            result = _dispatch_retrieve(entry, **kwargs)
            if hasattr(result, "__await__"):
                return await result
            return result

        def _dispatch_chat_json(call_log_inner):
            fake_jordan = _fake_chat_json(call_log_inner)
            fake_priya = _fake_chat_json_combined(call_log_inner)

            async def _fake(messages, **kwargs):
                user_msg = messages[-1]["content"]
                if "Jordan Ellis" in user_msg:
                    return await fake_jordan(messages, **kwargs)
                return await fake_priya(messages, **kwargs)

            return _fake

        with (
            patch("app.services.llm.is_configured", return_value=True),
            patch("app.services.llm.chat_json", new=_dispatch_chat_json(call_log)),
            patch(
                "app.services.proposal_intelligence.jit_retrieval.retrieve_for_section",
                new=_async_dispatch_retrieve,
            ),
        ):
            _review, _research, draft_after, report = await run_fulfill_rfp_gaps(
                rfp_id, mode="verify_scrub_only"
            )

        print("\n=== Task 12 — combined MERGE + truncation-repair report (real entry point) ===")
        import json

        print(json.dumps(report, indent=2, default=str))

        # Fix 1: the MERGE cross-referenced sections must never appear as
        # truncated, even though this same scan also ran truncation repair.
        self.assertEqual(report.get("ledgerMergesApplied"), 1)
        self.assertEqual(report.get("ledgerMergesSectionTitles"), ["Section 1.5"])
        truncated_titles = report.get("truncatedSectionTitles") or []
        self.assertNotIn("Attachments Checklist", truncated_titles)
        self.assertNotIn("Contract Acknowledgment", truncated_titles)

        # Fix 2: 2 of the 3 genuinely truncated sections are repaired from
        # the KB; 1 is left truncated and reported separately.
        self.assertEqual(report.get("truncationRepairedCount"), 2)
        self.assertEqual(
            sorted(report.get("truncationRepairedSectionTitles") or []),
            sorted(["Jordan Ellis — Bio", "Priya Nadar — Bio"]),
        )
        self.assertEqual(report.get("truncatedSectionsCount"), 1)
        self.assertEqual(
            report.get("truncatedSectionTitles"), ["Case Study — City of Umatilla"]
        )


# ---------------------------------------------------------------------------
# Bug 2 regression: the reported symptom was truncation_repaired=8 /
# truncated_sections=9 naming the SAME 5 sections in both lists — a working
# repair reading as if nothing worked.
#
# Root cause: repair_truncated_sections_from_kb's own success gate
# (looks_truncated_for_fulfill — checks only whether the section's trailing
# cutoff now reads complete) is a NARROWER detector than the T1 rescan the
# caller runs afterward (scan_truncation_artifacts via scan_all_t1, which
# also flags an unbalanced paren/bracket or currency fragment ANYWHERE in the
# section, not just the tail). A completion can close out the cut-off
# sentence — passing the narrow gate, counted as "repaired" — while an
# unrelated unclosed "(" earlier in the same bio still trips the broader
# rescan, so the SAME section lands in both "repaired" and "still truncated".
# ---------------------------------------------------------------------------

# Contains an unclosed "(" that the KB completion (below) never closes — only
# the trailing sentence gets completed, exactly like a real completion model
# that finishes the cut-off clause without noticing an earlier stray paren.
TRUNCATED_BIO_UNCLOSED_PAREN_CONTENT = (
    "Renata Ibarra has managed capital improvement communications (over "
    "twelve years and is a Certified Public Communicator accredited by"
)
# A pure append of the original — passes the word-prefix guard — that closes
# the cut-off sentence with terminal punctuation (so it reads "repaired" by
# looks_truncated_for_fulfill) but the "(" opened above is STILL never
# closed, so the full-content T1 rescan (_unbalanced_parens) still flags it.
REPAIRED_BIO_UNCLOSED_PAREN_CONTENT = (
    TRUNCATED_BIO_UNCLOSED_PAREN_CONTENT
    + " the National Association of Government Communicators."
)

TRUNCATED_CASE_STUDY_3_CONTENT = (
    "Our team delivered a downtown streetscape improvement campaign for the "
    "City of Meridian that increased pedestrian foot traffic by"
)


def _fake_chat_json_double_count(call_log: list[str]):
    async def _fake(messages, *, max_tokens=None, temperature=0.2, tier=None, node_name=None):
        call_log.append(node_name or "")
        assert node_name == "scan_truncation_kb_repair"
        user_msg = messages[-1]["content"]
        if "Jordan Ellis" in user_msg:
            return ({"content": REPAIRED_BIO_CONTENT}, "openrouter")
        if "Renata Ibarra" in user_msg:
            return ({"content": REPAIRED_BIO_UNCLOSED_PAREN_CONTENT}, "openrouter")
        # Case study: no KB evidence, model responds with something that
        # reads like a rewrite (fails the word-prefix guard) -> rejected.
        return (
            {"content": "Our firm regularly runs downtown revitalization campaigns."},
            "openrouter",
        )

    return _fake


async def _fake_retrieve_for_section_double_count(entry, *, rfp_client="", start_index=1, claim=None):
    if entry.section_id == "section-2-bio-jordan":
        return _fake_retrieve_for_section_with_evidence(entry, rfp_client=rfp_client)
    if entry.section_id == "section-2-bio-renata":
        return [
            EvidenceItem(
                id="E1",
                source="Renata_Ibarra_Resume.pdf",
                excerpt=(
                    "Renata Ibarra is a Certified Public Communicator "
                    "accredited by the National Association of Government "
                    "Communicators."
                ),
                sectionIds=[entry.section_id],
                chunkKey="renata-cpc",
            )
        ]
    return []


class TruncationRepairAndFinalRescanDisjointTests(_RealDbTestCase):
    async def test_a_section_that_passes_repairs_own_check_but_still_trips_the_t1_rescan_is_not_double_counted(
        self,
    ) -> None:
        """3 truncated sections: Jordan is genuinely fully repaired; Renata's
        completion passes repair's own narrower check but still trips the
        broader T1 rescan (unrelated unclosed paren); the case study is
        rejected outright (reads like a rewrite). Must report repaired=1,
        still-truncated=2, and no title in both lists — the exact disjointness
        a real user's run violated (8 repaired / 9 truncated, same 5 names)."""
        from app.services.proposal_fulfill_rfp_gaps import run_fulfill_rfp_gaps

        rfp_id = "rfp-truncation-double-count"
        from app.services.rfp_repository import upsert_rfp

        upsert_rfp(_rfp(rfp_id))
        draft = ProposalDraft(
            rfpId=rfp_id,
            sections=[
                ProposalSection(
                    id="section-2-bio-jordan", title="Jordan Ellis — Bio",
                    content=TRUNCATED_BIO_CONTENT,
                ),
                ProposalSection(
                    id="section-2-bio-renata", title="Renata Ibarra — Bio",
                    content=TRUNCATED_BIO_UNCLOSED_PAREN_CONTENT,
                ),
                ProposalSection(
                    id="section-3-work-meridian", title="Case Study — City of Meridian",
                    content=TRUNCATED_CASE_STUDY_3_CONTENT,
                ),
            ],
            updatedAt="2026-08-06T00:00:00Z",
        )
        await repo.asave_proposal_draft(draft)

        call_log: list[str] = []
        with (
            patch("app.services.llm.is_configured", return_value=True),
            patch(
                "app.services.llm.chat_json",
                new=_fake_chat_json_double_count(call_log),
            ),
            patch(
                "app.services.proposal_intelligence.jit_retrieval.retrieve_for_section",
                new=_fake_retrieve_for_section_double_count,
            ),
        ):
            _review, _research, draft_after, report = await run_fulfill_rfp_gaps(
                rfp_id, mode="verify_scrub_only"
            )

        print("\n=== Bug 2 — repaired vs. still-truncated must be disjoint ===")
        print(f"truncationRepairedCount = {report.get('truncationRepairedCount')}")
        print(f"truncationRepairedSectionTitles = {report.get('truncationRepairedSectionTitles')}")
        print(f"truncatedSectionsCount = {report.get('truncatedSectionsCount')}")
        print(f"truncatedSectionTitles = {report.get('truncatedSectionTitles')}")
        for line in report.get("logs", []):
            if "truncation" in line.casefold():
                print(f"  log: {line}")

        renata_after = next(
            s for s in draft_after.sections if s.id == "section-2-bio-renata"
        )
        # The completion DID apply (word-prefix guard passed) — content is
        # updated even though it still trips the broader rescan.
        self.assertEqual(renata_after.content, REPAIRED_BIO_UNCLOSED_PAREN_CONTENT)

        repaired_titles = report.get("truncationRepairedSectionTitles") or []
        truncated_titles = report.get("truncatedSectionTitles") or []

        self.assertEqual(report.get("truncationRepairedCount"), 1)
        self.assertEqual(repaired_titles, ["Jordan Ellis — Bio"])
        self.assertEqual(report.get("truncatedSectionsCount"), 2)
        self.assertEqual(
            sorted(truncated_titles),
            sorted(["Renata Ibarra — Bio", "Case Study — City of Meridian"]),
        )

        # The disjointness contract itself: no title in both lists.
        self.assertFalse(
            set(repaired_titles) & set(truncated_titles),
            f"repaired and still-truncated overlap: "
            f"{set(repaired_titles) & set(truncated_titles)}",
        )
        self.assertNotIn("Renata Ibarra — Bio", repaired_titles)
        self.assertIn("Renata Ibarra — Bio", truncated_titles)


if __name__ == "__main__":
    unittest.main()
