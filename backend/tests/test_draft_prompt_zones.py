"""Drafting-prompt cache zones.

The prompt-cache saving depends on one property: zone_a must be byte-identical
across every batch of a run, and zone_b must grow by appending. If a future edit
drops a batch-dependent block into zone_a, the prompt stays correct but every
cache hit disappears silently — no test failure, no error, just a bigger bill.
These tests are what make that failure loud.
"""

from __future__ import annotations

from typing import Any

from app.services.proposal_drafting_graph import _build_draft_prompt_zones


def _state(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "rfp_client": "City of Testville",
        "rfp_sector": "Public Sector",
        "rfp_location": "Testville, TS",
        "rfp_title": "Marketing Services RFP",
        "brand_voice": {"tone": "direct"},
        "zo_sections_context": "ZO CONTEXT BODY " * 40,
        "drafted_sections": [],
        "rfp_sections": [],
        "writing_avoidances": [],
        "loss_lessons": [],
        "proof_points": [],
    }
    base.update(overrides)
    return base


def _payload(section_id: str, *, title: str, register: str = "narrative") -> dict[str, Any]:
    return {
        "sectionId": section_id,
        "title": title,
        "register": register,
        "requirements": [],
        "zoMode": "write",
        "wordTarget": 400,
        "uncoveredRequirements": [],
        "evidence": "Evidence for " + section_id,
        "planContext": "",
        "evidencePolicy": "",
        "evidencePolicyReason": "",
    }


def _batch(section_id: str, *, title: str) -> list[dict[str, Any]]:
    return [{"id": section_id, "title": title}]


def _drafted(section_id: str, *, title: str, content: str) -> dict[str, Any]:
    return {"id": section_id, "title": title, "content": content}


class TestZoneAStability:
    def test_identical_across_different_batches_of_the_same_run(self) -> None:
        state = _state()
        a1, _, _ = _build_draft_prompt_zones(
            batch=_batch("1.1", title="Approach"),
            batch_payload=[_payload("1.1", title="Approach")],
            state=state,
        )
        a2, _, _ = _build_draft_prompt_zones(
            batch=_batch("2.4", title="Team Qualifications"),
            batch_payload=[_payload("2.4", title="Team Qualifications")],
            state=state,
        )
        assert a1 == a2

    def test_unaffected_by_register_mix(self) -> None:
        """Register blocks are batch-dependent and must not leak into zone_a."""
        state = _state()
        a_narrative, _, _ = _build_draft_prompt_zones(
            batch=_batch("1.1", title="Approach"),
            batch_payload=[_payload("1.1", title="Approach", register="narrative")],
            state=state,
        )
        a_procurement, _, _ = _build_draft_prompt_zones(
            batch=_batch("9.1", title="Forms"),
            batch_payload=[_payload("9.1", title="Forms", register="procurement")],
            state=state,
        )
        assert a_narrative == a_procurement

    def test_unaffected_by_prior_sections_growing(self) -> None:
        batch = _batch("3.1", title="Schedule")
        payload = [_payload("3.1", title="Schedule")]
        a_empty, _, _ = _build_draft_prompt_zones(
            batch=batch, batch_payload=payload, state=_state()
        )
        a_full, _, _ = _build_draft_prompt_zones(
            batch=batch,
            batch_payload=payload,
            state=_state(
                drafted_sections=[
                    _drafted("1.1", title="Approach", content="Approach body text."),
                    _drafted("2.1", title="Team", content="Team body text."),
                ]
            ),
        )
        assert a_empty == a_full

    def test_carries_the_run_level_blocks(self) -> None:
        zone_a, _, _ = _build_draft_prompt_zones(
            batch=_batch("1.1", title="Approach"),
            batch_payload=[_payload("1.1", title="Approach")],
            state=_state(),
        )
        assert "City of Testville" in zone_a
        assert "ZO CONTEXT BODY" in zone_a
        assert "ALREADY WRITTEN" in zone_a


class TestZoneBAppendOnly:
    def test_grows_by_appending_so_the_earlier_copy_stays_a_prefix(self) -> None:
        batch = _batch("3.1", title="Schedule")
        payload = [_payload("3.1", title="Schedule")]
        first = [_drafted("1.1", title="Approach", content="Approach body text.")]
        second = first + [_drafted("2.1", title="Team", content="Team body text.")]

        _, b1, _ = _build_draft_prompt_zones(
            batch=batch, batch_payload=payload, state=_state(drafted_sections=first)
        )
        _, b2, _ = _build_draft_prompt_zones(
            batch=batch, batch_payload=payload, state=_state(drafted_sections=second)
        )
        assert b1
        assert b2.startswith(b1.rstrip("\n"))

    def test_empty_when_nothing_drafted_yet(self) -> None:
        _, zone_b, _ = _build_draft_prompt_zones(
            batch=_batch("1.1", title="Approach"),
            batch_payload=[_payload("1.1", title="Approach")],
            state=_state(),
        )
        assert zone_b == ""


class TestZoneCVolatile:
    def test_holds_the_batch_payload_and_register_blocks(self) -> None:
        _, _, zone_c = _build_draft_prompt_zones(
            batch=_batch("1.1", title="Approach"),
            batch_payload=[_payload("1.1", title="Approach")],
            state=_state(),
        )
        assert "Sections to draft:" in zone_c
        assert "NARRATIVE sections in this batch" in zone_c

    def test_batch_payload_never_reaches_the_cached_zones(self) -> None:
        zone_a, zone_b, _ = _build_draft_prompt_zones(
            batch=_batch("7.7", title="Distinctive Section Title"),
            batch_payload=[_payload("7.7", title="Distinctive Section Title")],
            state=_state(),
        )
        assert "Distinctive Section Title" not in zone_a + zone_b
        assert "Sections to draft:" not in zone_a + zone_b


class TestNothingDropped:
    def test_every_block_survives_the_regrouping(self) -> None:
        """Reordering must not lose content — the one way this could hurt quality."""
        from app.services.proposal_budget_slots import money_slots_prompt_hint
        from app.services.proposal_section_dedup import format_anti_duplication_rules

        zone_a, zone_b, zone_c = _build_draft_prompt_zones(
            batch=_batch("1.1", title="Approach"),
            batch_payload=[_payload("1.1", title="Approach")],
            state=_state(
                drafted_sections=[
                    _drafted("0.1", title="Cover", content="Cover body text.")
                ]
            ),
        )
        whole = zone_a + zone_b + zone_c

        for expected in (
            "City of Testville",
            "Marketing Services RFP",
            "ZO CONTEXT BODY",
            "ALREADY WRITTEN",
            format_anti_duplication_rules(),
            money_slots_prompt_hint(),
            "ALREADY COVERED IN OTHER SECTIONS",
            "NARRATIVE sections in this batch",
            "Sections to draft:",
        ):
            assert expected in whole
