"""Bid-scope outline filtering through derive_legacy_fields."""

from types import SimpleNamespace

from app.services.proposal_bid_scope import (
    enforce_bid_scope_for_rfp,
    filter_outline_sections_for_bid_scope,
)
from app.services.proposal_intelligence.schemas import (
    OutlineSection,
    ProposalExecutionPlan,
    ProposalOutline,
    WritingIntelligence,
)


def test_filter_outline_keeps_shared_and_selected_track():
    sections = [
        OutlineSection(id="1", title="Cover", track=""),
        OutlineSection(id="2", title="Facility", track="III.A VIC"),
        OutlineSection(id="3", title="VIS", track="III.B VIS"),
    ]
    out = filter_outline_sections_for_bid_scope(sections, ["III.B VIS"])
    assert [s.id for s in out] == ["1", "3"]


def test_enforce_bid_scope_blocks_unlocked_multi_track():
    rfp = SimpleNamespace(
        go_no_go_analysis={
            "availableTracks": ["III.A", "III.B"],
            "capabilityMatrix": [],
        },
        selected_tracks=[],
        bid_scope_locked_at=None,
    )
    try:
        enforce_bid_scope_for_rfp(rfp)
        raised = False
    except ValueError:
        raised = True
    assert raised is True


def test_enforce_bid_scope_allows_single_track_rfp():
    rfp = SimpleNamespace(
        go_no_go_analysis={"availableTracks": ["Only One"]},
        selected_tracks=[],
        bid_scope_locked_at=None,
    )
    enforce_bid_scope_for_rfp(rfp)


def test_derive_legacy_filters_by_selected_tracks():
    from app.services.proposal_intelligence.assembler import derive_legacy_fields

    plan = ProposalExecutionPlan(
        rfpId="r1",
        writing=WritingIntelligence(
            proposalOutline=ProposalOutline(
                sections=[
                    OutlineSection(id="a", title="Cover", order=1, track=""),
                    OutlineSection(id="b", title="Role A", order=2, track="III.A"),
                    OutlineSection(id="c", title="Role B", order=3, track="III.B"),
                ]
            )
        ),
    )
    legacy = derive_legacy_fields(plan, selected_tracks=["III.B"])
    titles = [s.title for s in legacy["rfpSections"]]
    assert "Role A" not in titles
    assert "Cover" in titles
    assert "Role B" in titles
