"""Anti-RFP-echo: proposal prose must answer, never paraphrase the buyer's ask."""

from __future__ import annotations

from app.services.proposal_anti_rfp_echo import (
    ANTI_RFP_ECHO_RULES,
    format_requirements_coverage_block,
    strip_rfp_requirement_echo_sentences,
)
from app.services.proposal_drafting_graph import DRAFT_BATCH_PROMPT, _build_draft_prompt_zones
from app.services.proposal_drafting_prompts import DESIGNER_READY_BLOCK, GLOBAL_AGENT_PROMPT_RULES
from app.services.proposal_section_dedup import ANTI_DUPLICATION_RULES


GILROY_OPENING = (
    "Gilroy Garlic Festival Association is not asking us to build something new. "
    "You built it: a rebuilt website, active social channels, and a sponsorship "
    "program ready for the next tier of investment ahead of your 50th anniversary "
    "in 2028. What you need now is a partner who optimizes, manages, and grows "
    "those assets without disrupting what already works."
)

GILROY_REQUIREMENTS = [
    "Do not build something new; optimize the rebuilt website, active social "
    "channels, and sponsorship program ahead of the 50th anniversary in 2028.",
    "Website maintenance, security, and conversion optimization for ticket, "
    "merchandise, and vendor-application flows.",
]

PROPOSAL_ANSWER = (
    "We will run festival-week conversion checks on ticket and vendor flows, "
    "then ship a year-round content calendar that pulls volunteer stories into "
    "Instagram and Facebook without a redesign. Rock the Locks taught us how to "
    "hand off mid-season without dropping on-site capture."
)


class TestAntiRfpEchoRulesExist:
    def test_shared_block_forbids_restating_rfp(self) -> None:
        lower = ANTI_RFP_ECHO_RULES.casefold()
        assert "never restate" in lower or "do not restate" in lower
        assert "proposal answer" in lower or "answer the ask" in lower
        assert "paraphrase" in lower

    def test_draft_batch_prompt_no_longer_allows_rfp_restatement(self) -> None:
        lower = DRAFT_BATCH_PROMPT.casefold()
        assert "restate rfp requirements" not in lower
        assert "restate the client's goals" not in lower
        assert "show we read the rfp" not in lower
        assert "anti-rfp-echo" in lower or "never restate the rfp" in lower

    def test_global_and_designer_blocks_carry_anti_echo(self) -> None:
        joined = f"{GLOBAL_AGENT_PROMPT_RULES}\n{DESIGNER_READY_BLOCK}".casefold()
        assert "never restate the rfp" in joined or "anti-rfp-echo" in joined

    def test_anti_duplication_does_not_tell_understanding_to_restate_client_goals(
        self,
    ) -> None:
        # Old text: "Understanding / Opportunity → client goals, constraints…"
        # That ownership line taught writers to open by paraphrasing the RFP.
        lower = ANTI_DUPLICATION_RULES.casefold()
        assert "client goals, constraints, audiences — not company bio" not in lower


class TestRequirementsCoverageBlock:
    def test_labels_requirements_as_unquotable_checklist(self) -> None:
        block = format_requirements_coverage_block(GILROY_REQUIREMENTS)
        lower = block.casefold()
        assert "coverage" in lower or "checklist" in lower
        assert "do not" in lower and ("quote" in lower or "paraphrase" in lower)
        assert "50th anniversary" in block


class TestStripRfpRequirementEcho:
    def test_gilroy_style_opening_is_removed(self) -> None:
        body = f"{GILROY_OPENING}\n\n{PROPOSAL_ANSWER}"
        out = strip_rfp_requirement_echo_sentences(body, GILROY_REQUIREMENTS)
        assert "not asking us to build" not in out.casefold()
        assert "rock the locks" in out.casefold()

    def test_proposal_only_prose_is_preserved(self) -> None:
        assert (
            strip_rfp_requirement_echo_sentences(PROPOSAL_ANSWER, GILROY_REQUIREMENTS)
            == PROPOSAL_ANSWER
        )

    def test_empty_requirements_leave_body_alone(self) -> None:
        assert strip_rfp_requirement_echo_sentences(GILROY_OPENING, []) == GILROY_OPENING


class TestDraftPromptZonesCarryAntiEcho:
    def test_zone_a_includes_anti_rfp_echo_rules(self) -> None:
        zone_a, _, _ = _build_draft_prompt_zones(
            batch=[{"id": "20", "title": "Executive Summary"}],
            batch_payload=[
                {
                    "sectionId": "20",
                    "title": "Executive Summary",
                    "register": "narrative",
                    "requirements": GILROY_REQUIREMENTS,
                    "zoMode": "write",
                    "wordTarget": 400,
                    "uncoveredRequirements": [],
                    "evidence": "",
                    "planContext": "",
                    "evidencePolicy": "",
                    "evidencePolicyReason": "",
                }
            ],
            state={
                "rfp_client": "Gilroy Garlic Festival Association",
                "rfp_sector": "Festivals",
                "rfp_location": "Gilroy, CA",
                "rfp_title": "Marketing Services",
                "brand_voice": {},
                "zo_sections_context": "",
                "drafted_sections": [],
                "rfp_sections": [],
                "writing_avoidances": [],
                "loss_lessons": [],
                "proof_points": [],
            },
        )
        assert "ANTI-RFP-ECHO" in zone_a or "Never restate the RFP" in zone_a
