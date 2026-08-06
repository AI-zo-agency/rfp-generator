"""Task 10: the requirement-ledger ADD stub gets DRAFTED, not left as a
[MANUAL FILL] placeholder.

Root defect (verified against HEAD 9861115 by driving the real Scan-RFP
button): reconcile_requirement_ledger's ADD path (proposal_rfp_compliance.py)
is deliberately pure/synchronous/zero-LLM — see the module note above it —
so the section it adds for a missing mandatory requirement is always a
deterministic ``[MANUAL FILL: Sonja — <requirement>]`` stub with
``status="outline"``. That is correct in isolation, but it means the real
Scan-RFP button ships a stub where a scored "Technical Approach" should be —
not a clean proposal.

``draft_added_requirement_sections`` (same module) is the fix: an async,
best-effort pass the real entry point (``run_verify_scrub_only_scan`` via
``run_fulfill_rfp_gaps(rfp_id, mode="verify_scrub_only")``) runs immediately
after the reconciler, scoped to exactly the sections THIS pass's
``applied_additions`` just created. It retrieves KB evidence
(``retrieve_for_section``, zero LLM calls), plans queries
(``REFINE_QUERIES_PROMPT``) and drafts the section
(``SECTION_REDRAFT_PROMPT``) — the same prompts an ordinary section redraft
uses — through ``llm.chat_json_soft`` with explicit ``node_name``s so the
call count is exactly bounded: one query-planning call + one drafting call
per added section, never more, and it degrades to the existing placeholder
(never raises) on any failure.

This file drives the REAL entry point end to end (mirrors
test_scan_rfp_reconciler_wiring.py's pattern: sqlite instead of Supabase, no
mocking of reconcile_requirement_ledger itself) with the LLM and KB retrieval
stubbed — never a live network call — and proves:

  1. The added section comes back with real prose, status="generated", no
     [MANUAL FILL] marker, and the exact evidence fact appears in it.
  2. Exactly 2 LLM calls happen for the one added section (query planning +
     drafting) — no more.
  3. A second click is a genuine no-op: 0 further LLM calls, 0 further
     additions, and the drafted content is byte-for-byte stable.
  4. When the LLM fails, the button still returns the [MANUAL FILL]
     placeholder untouched instead of raising.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core import config
from app.models.proposal import (
    EvidenceItem,
    ProposalDraft,
    ProposalResearchCache,
    ProposalSection,
)
from app.models.requirement_ledger import LedgerRequirement, RequirementLedger
from app.models.rfp import RfpRecord
from app.services import proposal_repository as repo

EXISTING_APPROACH_CONTENT = "\n\n".join(
    " ".join(["existing"] * 40) for _ in range(4)
)  # 160w, unrelated section already in the draft


def _rfp(rfp_id: str, **overrides) -> RfpRecord:
    fields = dict(
        id=rfp_id,
        title="Regional Transit Marketing Services",
        client="Metro Transit Authority",
        dueDate="2026-09-01",
        receivedDate="2026-08-01",
        lastActivity="2026-08-05",
        lastActivityNote="note",
        goNoGo="go",
        description=" ".join(["background context sentence about the transit marketing scope"] * 6),
        pageLimit=None,
    )
    fields.update(overrides)
    return RfpRecord(**fields)


def _req(rid: str, text: str, **kw) -> LedgerRequirement:
    kw.setdefault("source", "scored_criterion")
    kw.setdefault("mandatory", True)
    kw.setdefault("satisfiedBy", [])
    return LedgerRequirement(id=rid, text=text, **kw)


_DRAFTED_TECHNICAL_APPROACH = (
    "We meet this requirement with a phased technical approach built on our "
    "verified transit marketing delivery record. Our team ran a comparable "
    "regional transit awareness campaign for Riverside Metro, driving a 32% "
    "increase in verified ridership over the contract term, and we bring the "
    "same discovery-to-launch method here: stakeholder discovery, creative "
    "development, media buy, and a measured optimization cadence.\n\n"
    "Our approach begins with a two-week discovery sprint where we confirm "
    "route-level ridership goals with the authority's staff, then moves into "
    "concept development or our creative team, media planning for our buying "
    "team, and a 90-day optimization loop once campaign elements launch. "
    "Weekly reporting keeps the authority's team aligned on pacing against "
    "the ridership targets set during discovery, and our account lead "
    "remains the single point of contact throughout delivery."
)


async def _fake_resolve_voice_context(*, rfp, rfp_context, brand_voice):
    """Deterministic stand-in for the Supermemory-backed voice resolver."""
    return (
        {
            "zoCoreVoice": (
                "zö agency writes as a confident, human-centered marketing "
                "partner: direct, warm, first-person (we/our)."
            ),
            "tone": "professional",
            "formality": "semi-formal",
            "voiceGuidelines": ["First person we/our.", "Never invent facts."],
        },
        "",
    )


class _RealDbTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._db = Path(self._tmpdir.name) / "scan-rfp-ledger-add-drafting.db"
        self._patchers = [
            patch.object(config.settings, "database_path", self._db),
            patch.object(repo, "_use_supabase", return_value=False),
            patch("app.services.rfp_repository._use_supabase", return_value=False),
            patch("app.services.supabase_db.use_supabase_db", return_value=False),
            # Hermeticity: resolve_voice_context reaches Supermemory
            # (fetch_zo_voice_excerpt) for the zö voice excerpt. Unstubbed
            # that is a LIVE network call — the whole reason this file ran in
            # ~36s instead of ~1s. The voice block's *content* is not what
            # these tests assert; the drafted prose is.
            patch(
                "app.services.proposal_brand_voice.resolve_voice_context",
                new=_fake_resolve_voice_context,
            ),
        ]
        for p in self._patchers:
            p.start()
        repo.init_proposal_db()

    async def asyncTearDown(self) -> None:
        for p in reversed(self._patchers):
            p.stop()
        self._tmpdir.cleanup()

    async def _seed_missing_technical_approach(self, rfp_id: str) -> None:
        from app.services.rfp_repository import upsert_rfp

        upsert_rfp(_rfp(rfp_id))
        ledger = RequirementLedger(
            requirements=[
                _req(
                    "r-technical-approach",
                    "Technical Approach: describe your method for delivering "
                    "the regional transit marketing campaign",
                    points=30.0,
                )
            ]
        )
        draft = ProposalDraft(
            rfpId=rfp_id,
            sections=[
                ProposalSection(
                    id="s1", title="Company Overview", content=EXISTING_APPROACH_CONTENT
                )
            ],
            updatedAt="2026-08-06T00:00:00Z",
        )
        await repo.asave_proposal_draft(draft)
        await repo.asave_research_cache(
            ProposalResearchCache(
                rfpId=rfp_id, requirementLedger=ledger, updatedAt="2026-08-06T00:00:00Z"
            )
        )


def _fake_chat_json_soft(call_log: list[str]):
    """Stub for llm.chat_json_soft keyed by node_name — never a live call."""

    async def _fake(messages, *, max_tokens=None, temperature=0.2, tier="heavy",
                     node_name=None, rfp_id=None, run_id=None):
        call_log.append(node_name or "")
        if node_name == "ledger_add_query_planner":
            return (
                {"queries": ["zö agency 03_CS transit marketing case study"]},
                "openrouter",
            )
        if node_name == "ledger_add_section_draft":
            return ({"content": _DRAFTED_TECHNICAL_APPROACH}, "openrouter")
        raise AssertionError(f"unexpected node_name in test: {node_name!r}")

    return _fake


async def _fake_retrieve_for_section(entry, *, rfp_client="", start_index=1, claim=None):
    return [
        EvidenceItem(
            id="E1",
            source="03_CS_Riverside_Metro_Transit.pdf",
            excerpt=(
                "zö agency ran a regional transit awareness campaign for "
                "Riverside Metro, driving a 32% increase in verified "
                "ridership over the contract term."
            ),
            sectionIds=[entry.section_id],
            chunkKey="riverside-metro-cs",
        )
    ]


class LedgerAddDraftingDrivesRealEntryPointTests(_RealDbTestCase):
    async def test_added_technical_approach_section_is_drafted_not_a_stub(self) -> None:
        from app.services.proposal_fulfill_rfp_gaps import run_fulfill_rfp_gaps

        rfp_id = "rfp-ledger-add-drafting"
        await self._seed_missing_technical_approach(rfp_id)

        call_log: list[str] = []
        with (
            patch("app.services.llm.is_configured", return_value=True),
            patch(
                "app.services.llm.chat_json_soft",
                new=_fake_chat_json_soft(call_log),
            ),
            patch(
                "app.services.proposal_intelligence.jit_retrieval.retrieve_for_section",
                new=_fake_retrieve_for_section,
            ),
        ):
            _review, _research, draft_after, report = await run_fulfill_rfp_gaps(
                rfp_id, mode="verify_scrub_only"
            )

        added = next(
            s for s in draft_after.sections if s.id == "ledger-r-technical-approach"
        )
        words = (added.content or "").split()

        print("\n=== Task 10 — added section after real Scan-RFP entry point ===")
        print(f"status = {added.status!r}")
        print(f"word_count = {len(words)}")
        print(f"first_300_chars = {added.content[:300]!r}")
        print(f"llm calls for this section = {call_log}")

        # HARD requirement: real prose, not a stub.
        self.assertNotIn("[MANUAL FILL", added.content)
        self.assertEqual(added.status, "generated")
        self.assertGreaterEqual(len(words), 120)
        self.assertIn("Riverside Metro", added.content)
        self.assertIn("32%", added.content)

        # LLM budget: exactly one query-planning call + one drafting call for
        # the one added section — never more.
        self.assertEqual(call_log, ["ledger_add_query_planner", "ledger_add_section_draft"])
        self.assertEqual(report.get("ledgerAdditionsApplied"), 1)
        self.assertFalse(
            report.get("humanDecisionGaps"),
            "a successfully-drafted section must not still be reported as needing "
            f"human content: {report.get('humanDecisionGaps')!r}",
        )

        # ---- Idempotence through the REAL entry point: second click is a no-op ----
        with (
            patch("app.services.llm.is_configured", return_value=True),
            patch(
                "app.services.llm.chat_json_soft",
                new=_fake_chat_json_soft(call_log),
            ),
            patch(
                "app.services.proposal_intelligence.jit_retrieval.retrieve_for_section",
                new=_fake_retrieve_for_section,
            ),
        ):
            _review2, _research2, draft_after2, report2 = await run_fulfill_rfp_gaps(
                rfp_id, mode="verify_scrub_only"
            )

        added2 = next(
            s for s in draft_after2.sections if s.id == "ledger-r-technical-approach"
        )
        print("\n=== Task 10 — second click (idempotence) ===")
        print(f"total llm calls after second click = {call_log}")
        print(f"word_count pass2 = {len((added2.content or '').split())}")

        self.assertEqual(report2.get("ledgerAdditionsApplied"), 0)
        self.assertEqual(
            call_log,
            ["ledger_add_query_planner", "ledger_add_section_draft"],
            "second click must not fire any new LLM calls for the same section",
        )
        self.assertEqual(added2.content, added.content, "content must be stable across passes")
        self.assertEqual(added2.status, "generated")
        self.assertEqual(
            len(draft_after2.sections),
            len(draft_after.sections),
            "must not duplicate the added section",
        )

    async def test_llm_failure_degrades_to_the_placeholder_and_never_raises(self) -> None:
        from app.services.proposal_fulfill_rfp_gaps import run_fulfill_rfp_gaps

        rfp_id = "rfp-ledger-add-drafting-fails"
        await self._seed_missing_technical_approach(rfp_id)

        async def _failing_chat_json_soft(messages, **kwargs):
            return ({}, "failed")

        with (
            patch("app.services.llm.is_configured", return_value=True),
            # Belt-and-braces hermeticity: the planner failure short-circuits
            # before retrieval, but never leave a live path reachable.
            patch(
                "app.services.proposal_intelligence.jit_retrieval.retrieve_for_section",
                new=_fake_retrieve_for_section,
            ),
            patch(
                "app.services.llm.chat_json_soft",
                new=_failing_chat_json_soft,
            ),
        ):
            _review, _research, draft_after, report = await run_fulfill_rfp_gaps(
                rfp_id, mode="verify_scrub_only"
            )

        added = next(
            s for s in draft_after.sections if s.id == "ledger-r-technical-approach"
        )
        print("\n=== Task 10 — LLM failure degrades to placeholder ===")
        print(f"status = {added.status!r}")
        print(f"content[:200] = {added.content[:200]!r}")

        self.assertIn("[MANUAL FILL", added.content)
        self.assertEqual(added.status, "outline")
        self.assertEqual(report.get("ledgerAdditionsApplied"), 1)
        self.assertTrue(
            any("technical approach" in g.casefold() for g in report.get("humanDecisionGaps", [])),
            report.get("humanDecisionGaps"),
        )


if __name__ == "__main__":
    unittest.main()
