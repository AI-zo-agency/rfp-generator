"""Tests: compulsory Brand & Writing Standards rev 6 + interesting proposal voice."""

from __future__ import annotations

from app.services.proposal_brand_voice import (
    INTERESTING_PROPOSAL_ANSWER_BLOCK,
    format_brand_voice_block,
    load_writing_standards,
    load_writing_standards_rev6,
)
from app.services.proposal_drafting_graph import DRAFT_BATCH_PROMPT, _build_draft_prompt_zones
from app.services.proposal_langchain_agents import SECTION_REPAIR_SYSTEM, SENIOR_EDITOR_SYSTEM
from app.services.proposal_presubmit_autofix import SURGICAL_FIX_PROMPT


class TestCompulsoryRev6:
    def test_load_returns_rev6(self) -> None:
        text = load_writing_standards()
        assert "rev 6" in text.casefold()
        assert "COMPULSORY" in text
        assert "negation-contrast" in text.casefold() or "negation" in text.casefold()

    def test_rev6_alias_matches(self) -> None:
        assert "rev 6" in load_writing_standards_rev6().casefold()

    def test_brand_voice_block_leads_with_rev6(self) -> None:
        block = format_brand_voice_block({"tone": "direct"}, register="narrative")
        assert "rev 6" in block.casefold()
        assert "INTERESTING PROPOSAL ANSWER" in block
        # Dead-on-arrival older revs must not govern.
        assert "rev 3 · July 2026) · COMPULSORY" not in block

    def test_find_rev6_violations_detects_hard_bans(self) -> None:
        from app.services.proposal_voice_enforcement import (
            find_rev6_voice_violations,
            scrub_rev6_voice_patterns,
        )

        dirty = (
            "We deliver robust work rather than fluff — worth noting that "
            "this is seamless. That's the kind of partner we are."
        )
        hits = find_rev6_voice_violations(dirty)
        assert any("em dash" in h for h in hits)
        assert any("rather than" in h for h in hits)
        cleaned, _ = scrub_rev6_voice_patterns(dirty)
        cleaned = cleaned.replace("—", ",")
        remaining = find_rev6_voice_violations(cleaned)
        assert len(remaining) < len(hits)

    def test_apply_compulsory_rev6_strips_banned_phrases(self) -> None:
        from app.models.proposal import ProposalSection
        from app.services.proposal_voice_enforcement import (
            apply_compulsory_rev6_to_section,
            find_rev6_voice_violations,
        )

        section = ProposalSection(
            id="rfp-structure-executive-summary",
            title="Executive Summary",
            content=(
                "We deliver robust work rather than fluff — worth noting that "
                "this is seamless. That's the kind of partner we are."
            ),
            status="generated",
            mode="write",
        )
        cleaned, logs = apply_compulsory_rev6_to_section(section)
        assert logs or cleaned.content != section.content
        assert "—" not in (cleaned.content or "")
        remaining = find_rev6_voice_violations(cleaned.content or "")
        assert not any("em dash" in h for h in remaining)

    def test_contradiction_rewrite_prompts_include_rev6(self) -> None:
        from pathlib import Path

        for rel in (
            "app/services/proposal_scan_rfp_contradictions.py",
            "app/services/proposal_manuscript_fact_contradictions.py",
            "app/services/proposal_manuscript_budget_contradictions.py",
        ):
            src = (
                Path(__file__).resolve().parents[1] / rel
            ).read_text(encoding="utf-8")
            assert "CHAT_REV6_VOICE_HARD_RULES" in src
            assert "apply_compulsory_rev6_to_section" in src


class TestReviewFixAntiEcho:
    def test_surgical_fix_forbids_rfp_echo(self) -> None:
        assert "ANTI-RFP-ECHO" in SURGICAL_FIX_PROMPT
        assert "NEVER restate the RFP" in SURGICAL_FIX_PROMPT

    def test_surgical_fix_rev6_is_compulsory(self) -> None:
        lower = SURGICAL_FIX_PROMPT.casefold()
        assert "compulsory" in lower
        assert "rev 6" in lower
        assert "negation-contrast" in lower or "em dash" in lower

    def test_presubmit_rev6_violations_are_critical(self) -> None:
        from app.models.proposal import ProposalDraft, ProposalSection
        from app.models.rfp import RfpRecord
        from app.services.proposal_presubmit_review import _scan_voice

        draft = ProposalDraft(
            rfpId="r1",
            updatedAt="t",
            sections=[
                ProposalSection(
                    id="rfp-structure-executive-summary",
                    title="Executive Summary",
                    content=(
                        "We deliver robust work rather than fluff — worth noting "
                        "that this is seamless."
                    ),
                    status="generated",
                    mode="write",
                )
            ],
        )
        issues = _scan_voice(draft)
        rev6 = [i for i in issues if "Rev 6" in (i.message or "")]
        assert rev6, "expected Rev 6 voice issues"
        assert all(i.severity == "critical" for i in rev6)
        assert all(i.category == "voice" for i in rev6)

    def test_autofix_deterministic_scrubs_rev6(self) -> None:
        from app.models.proposal import ProposalSection
        from app.models.rfp import RfpRecord
        from app.services.proposal_presubmit_autofix import _apply_deterministic_fixes

        section = ProposalSection(
            id="rfp-structure-executive-summary",
            title="Executive Summary",
            content="We deliver robust work rather than fluff — worth noting that.",
            status="generated",
            mode="write",
        )
        rfp = RfpRecord(
            id="r1",
            title="T",
            client="City",
            sector="gov",
            dueDate="2026-12-01",
            receivedDate="2026-01-01",
            lastActivity="2026-01-01",
            lastActivityNote="test",
        )
        cleaned, methods = _apply_deterministic_fixes(section, rfp)
        assert methods  # voice_register and/or rev6_voice
        assert "—" not in cleaned
        assert "rather than" not in cleaned.casefold()
        assert "robust" not in cleaned.casefold() or "worth noting" not in cleaned.casefold()

    def test_section_repair_forbids_rfp_echo(self) -> None:
        lower = SECTION_REPAIR_SYSTEM.casefold()
        assert "anti-rfp-echo" in lower or "never restate the rfp" in lower

    def test_senior_editor_forbids_echo_in_rewrite_briefs(self) -> None:
        lower = SENIOR_EDITOR_SYSTEM.casefold()
        assert "anti-rfp-echo" in lower or "never paraphrase the rfp" in lower

    def test_draft_batch_includes_interesting_and_rev6(self) -> None:
        lower = DRAFT_BATCH_PROMPT.casefold()
        assert "anti-rfp-echo" in lower
        zone_a, _, _ = _build_draft_prompt_zones(
            batch=[{"id": "1", "title": "Executive Summary"}],
            batch_payload=[
                {
                    "sectionId": "1",
                    "title": "Executive Summary",
                    "register": "narrative",
                    "requirements": [],
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
                "rfp_client": "Test",
                "rfp_sector": "Festivals",
                "rfp_location": "",
                "rfp_title": "RFP",
                "brand_voice": {},
                "zo_sections_context": "",
                "drafted_sections": [],
                "rfp_sections": [],
                "writing_avoidances": [],
                "loss_lessons": [],
                "proof_points": [],
            },
        )
        assert "INTERESTING PROPOSAL ANSWER" in zone_a or "interesting proposal" in zone_a.casefold()
        assert "rev 6" in zone_a.casefold() or "ANTI-RFP-ECHO" in zone_a

    def test_cover_letter_register_uses_won_form_models(self) -> None:
        block = format_brand_voice_block({"tone": "direct"}, register="cover_letter")
        lower = block.casefold()
        assert "cover letter" in lower or "signed passage" in lower
        assert "06_won" in lower or "won-proposal" in lower or "won proposal" in lower
        assert "form" in lower or "structure" in lower

        _, _, zone_c = _build_draft_prompt_zones(
            batch=[{"id": "cover", "title": "Cover Letter"}],
            batch_payload=[
                {
                    "sectionId": "cover",
                    "title": "Cover Letter",
                    "register": "cover_letter",
                    "requirements": [],
                    "zoMode": "write",
                    "wordTarget": 400,
                    "uncoveredRequirements": [],
                    "evidence": "[E1] 06_WON_Sample.pdf\nDear Selection Committee…",
                    "planContext": "",
                    "evidencePolicy": "retrieve_then_write",
                    "evidencePolicyReason": "cover_letter_won_exemplars",
                }
            ],
            state={
                "rfp_client": "RTA",
                "rfp_sector": "transit",
                "rfp_location": "",
                "rfp_title": "Mobility Outreach",
                "brand_voice": {},
                "zo_sections_context": "",
                "drafted_sections": [],
                "rfp_sections": [],
                "writing_avoidances": [],
                "loss_lessons": [],
                "proof_points": [],
            },
        )
        assert "COVER LETTER" in zone_c
        assert "WON EXEMPLAR" in zone_c or "06_WON" in zone_c

    def test_draft_batch_cover_letter_rule_mentions_won_form(self) -> None:
        lower = DRAFT_BATCH_PROMPT.casefold()
        assert "cover letter" in lower
        assert "06_won" in lower or "won cover" in lower or "form / structure" in lower
