"""Packet exhibit → draft section selection (RFP-scoped, no full rebuild)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.models.proposal import ProposalDraft, ProposalSection
from app.services import proposal_packet_exhibit_apply as apply_mod


def _section(sid: str, title: str, content: str) -> ProposalSection:
    return ProposalSection(id=sid, title=title, content=content)


def _draft(*sections: ProposalSection) -> ProposalDraft:
    return ProposalDraft(
        rfpId="rfp-a",
        sections=list(sections),
        updatedAt="2026-11-19T12:00:00Z",
    )


def test_heuristic_picks_clarify_and_mention_tabs():
    draft = _draft(
        _section("s1", "Approach", "See Exhibit A for the SOW tasks."),
        _section("s2", "Team", "Bios only."),
        _section(
            "s3",
            "Scope",
            "[MANUAL FILL: Sonja — RFP ambiguity — Exhibit A not in packet]",
        ),
    )
    research = SimpleNamespace(
        proposal_execution_plan=None,
        rfp_sections=[
            SimpleNamespace(id="s1", submission_instrument="narrative"),
            SimpleNamespace(id="s2", submission_instrument="narrative"),
            SimpleNamespace(id="s3", submission_instrument="clarify"),
        ],
    )
    hits = apply_mod.heuristic_section_ids_for_cite(
        draft, "Exhibit A", research=research  # type: ignore[arg-type]
    )
    assert "s1" in hits
    assert "s3" in hits
    assert "s2" not in hits


def test_apply_rejects_exhibit_from_other_rfp_scope():
    async def _run() -> None:
        with (
            patch.object(apply_mod, "get_packet_file_by_cite", return_value=None),
            patch.object(apply_mod, "list_packet_files", return_value=[]),
        ):
            result = await apply_mod.apply_packet_exhibit_to_draft(
                "rfp-a", "Exhibit A"
            )
        assert result.applied is False
        assert result.reason == "exhibit_not_on_this_rfp"

    asyncio.run(_run())


def test_apply_no_draft_is_noop():
    owned = SimpleNamespace(
        cite_target="Exhibit A",
        cite_quote="See Exhibit A",
        id="f1",
    )

    async def _run() -> None:
        with (
            patch.object(apply_mod, "get_packet_file_by_cite", return_value=owned),
            patch.object(
                apply_mod, "aget_proposal_draft", new=AsyncMock(return_value=None)
            ),
        ):
            result = await apply_mod.apply_packet_exhibit_to_draft(
                "rfp-a", "Exhibit A"
            )
        assert result.applied is False
        assert result.reason == "no_draft"

    asyncio.run(_run())


def test_select_falls_back_to_heuristic_when_llm_empty():
    draft = _draft(
        _section("s1", "Approach", "Depends on Exhibit A deliverables."),
        _section("s2", "Cover", "Dear Buyer,"),
    )

    async def _run() -> None:
        with patch.object(
            apply_mod,
            "safe_chat_json",
            new=AsyncMock(return_value=({"sectionIds": []}, "none")),
        ):
            ids = await apply_mod.select_sections_for_packet_cite(
                draft, "Exhibit A", exhibit_excerpt="SOW item 1"
            )
        assert ids == ["s1"]

    asyncio.run(_run())


def test_lock_key_is_rfp_scoped():
    assert apply_mod.apply_packet_exhibit_lock_key("rfp-1") == (
        "apply-packet-exhibit:rfp-1"
    )
    assert apply_mod.apply_packet_exhibit_lock_key("rfp-1") != (
        apply_mod.apply_packet_exhibit_lock_key("rfp-2")
    )
