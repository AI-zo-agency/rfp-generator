"""The evidence trust gate must never kill the build.

Incident (Gilroy Garlic Festival, manual-1c59ee85): evidence selection retrieved
6 case-study candidates and the trust gate rejected all 6, so _build_case_studies
returned {} — zero Section 3 cards. The Sections 1-3 completeness check then
raised:

    Sections 1-3 incomplete after generation - missing: Section 3 (Our Work).
    Click Reset, then Draft Sections 1-3 again.

That advice cannot work. The gate is deterministic, so every retry rejects the
same candidates; the log shows the identical failure twice, 85 seconds apart,
each costing a full run. A gate admitting nothing is an ANSWER ("no verified
project may be cited"), not a crash — so Section 3 now states capability
generally, cites no project, and hands the human one explicit decision.
"""

from __future__ import annotations

from app.models.proposal import ProposalSection
from app.services.proposal_generator import (
    _is_optional_empty_shell,
    _is_section_placeholder_id,
    _recovery_section_for_group,
)
from app.services.proposal_sections_graph import _no_eligible_case_study_section

REASON = "6 candidate(s) rejected by the evidence trust gate"


def _fallback(sector: str = "event marketing") -> dict:
    return _no_eligible_case_study_section({"rfp_sector": sector}, REASON)


class TestFallbackSatisfiesTheCompletenessGate:
    """These are the exact predicates that failed the Gilroy run."""

    def test_id_is_a_section_3_card(self):
        assert _fallback()["id"].startswith("section-3-")

    def test_id_is_not_treated_as_a_placeholder(self):
        assert not _is_section_placeholder_id(_fallback()["id"])

    def test_it_is_not_an_optional_empty_shell(self):
        raw = _fallback()
        section = ProposalSection.model_validate(
            {**raw, "id": raw["id"], "title": raw["title"]}
        )
        assert not _is_optional_empty_shell(section)

    def test_group_has_content_now_passes(self):
        """The precise check that raised 'missing: Section 3 (Our Work)'."""
        raw = _fallback()
        section = ProposalSection.model_validate(raw)
        passes = (
            section.id.startswith("section-3-")
            and not _is_section_placeholder_id(section.id)
            and bool((section.content or "").strip())
            and not _is_optional_empty_shell(section)
        )
        assert passes, "fallback must satisfy _group_has_content('section-3-')"


class TestFallbackContentIsHonest:
    def test_it_names_no_project_or_client(self):
        """The whole reason the gate fired is that no project is verifiable."""
        body = _fallback()["content"]
        assert "case study library" in body
        # No fabricated specifics.
        for invented in ("Client:", "Outcome:", "increased by", "%"):
            assert invented not in body

    def test_it_carries_exactly_one_human_decision(self):
        body = _fallback()["content"]
        assert body.count("[MANUAL FILL") == 1

    def test_it_states_the_real_reason(self):
        assert REASON in _fallback()["content"]

    def test_it_is_not_empty(self):
        assert len(_fallback()["content"].strip()) > 100

    def test_it_carries_no_agent_instruction_prose(self):
        """The MANUAL FILL tag is the handoff convention; narration is not."""
        body = _fallback()["content"].casefold()
        for narration in (
            "this section is important",
            "the reconciler",
            "placeholder so",
            "retry",
            "click reset",
        ):
            assert narration not in body

    def test_sector_is_woven_in_when_known(self):
        assert "event marketing" in _fallback("event marketing")["content"]

    def test_missing_sector_still_reads_cleanly(self):
        body = _fallback("")["content"]
        assert "  " not in body, "no double space where the sector would go"
        assert "engagement" in body


class TestGateLevelRecoveryNet:
    """Last-resort net behind the per-builder fallbacks.

    Covers any future path that leaves a required group empty, not just the
    trust gate — one place instead of a bespoke fallback per builder.
    """

    def test_team_and_work_groups_recover(self):
        for label, prefix in (
            ("Section 2 (Team)", "section-2-"),
            ("Section 3 (Our Work)", "section-3-"),
        ):
            section = _recovery_section_for_group(label)
            assert section is not None, label
            assert section.id.startswith(prefix)
            assert not _is_section_placeholder_id(section.id)
            assert not _is_optional_empty_shell(section)
            assert (section.content or "").strip()

    def test_company_facts_still_raise(self):
        """An empty Section 1 is a KB configuration problem, not an answer —
        papering over it would hide a real defect."""
        assert _recovery_section_for_group("Section 1 (Company)") is None

    def test_recovery_cards_assert_nothing_unsupported(self):
        for label in ("Section 2 (Team)", "Section 3 (Our Work)"):
            body = _recovery_section_for_group(label).content
            assert body.count("[MANUAL FILL") == 1
            assert "nothing has been asserted here" in body

    def test_recovery_cards_carry_no_retry_advice(self):
        """The old message told users to retry a deterministic failure."""
        for label in ("Section 2 (Team)", "Section 3 (Our Work)"):
            body = _recovery_section_for_group(label).content.casefold()
            assert "click reset" not in body
            assert "try again" not in body
