"""Attachment-scoped Fit refresh — no full RFP re-judge."""

from __future__ import annotations

from app.models.go_no_go import (
    GoNoGoAnalysis,
    GoNoGoDecisionMatrixRow,
    GoNoGoDimension,
    RfpPacketRead,
    UnresolvedReference,
)
from app.services.go_no_go_attachment_refresh import apply_attachment_judgment


def _dim() -> GoNoGoDimension:
    return GoNoGoDimension(summary="ok", scoreImpact="neutral")


def _prior() -> GoNoGoAnalysis:
    return GoNoGoAnalysis(
        fitScore=3,
        worthScore=3,
        recommendation="review",
        summary="Prior Fit summary.",
        scopeMatch=_dim(),
        sectorMatch=_dim(),
        compliance=_dim(),
        teamMatch=_dim(),
        decisionMatrix=[
            GoNoGoDecisionMatrixRow(
                dimension="Technical Capability Match", score=3, notes="ok"
            ),
            GoNoGoDecisionMatrixRow(
                dimension="Financial Viability", score=3, notes="ok"
            ),
        ],
        packetRead=RfpPacketRead(
            unresolvedReferences=[
                UnresolvedReference(
                    target="Exhibit A",
                    quote="See Exhibit A attached.",
                ),
                UnresolvedReference(
                    target="Attachment A",
                    quote="Agreement as Attachment A.",
                ),
            ]
        ),
    )


def test_random_pdf_keeps_flag_and_can_nudge_score():
    prior = _prior()
    updated = apply_attachment_judgment(
        prior,
        cite_target="Exhibit A",
        action="upload",
        raw={
            "bodyMatchesCite": False,
            "resolvedTargets": [],
            "fitScore": 3,
            "worthScore": 3,
            "recommendation": "review",
            "attachmentNote": "Attached file is unrelated to Exhibit A.",
            "decisionMatrix": [
                {
                    "dimension": "Technical Capability Match",
                    "score": 3,
                    "notes": "Still missing Exhibit A body.",
                }
            ],
        },
        cite_quote="See Exhibit A attached.",
    )
    targets = {r.target for r in updated.packet_read.unresolved_references}
    assert "Exhibit A" in targets
    assert "Attachment A" in targets
    assert "unrelated" in updated.summary.casefold() or "Exhibit A" in updated.summary


def test_real_exhibit_clears_only_that_cite():
    prior = _prior()
    updated = apply_attachment_judgment(
        prior,
        cite_target="Exhibit A",
        action="upload",
        raw={
            "bodyMatchesCite": True,
            "resolvedTargets": ["Exhibit A"],
            "fitScore": 4,
            "worthScore": 3,
            "recommendation": "go",
            "attachmentNote": "Exhibit A SOW now in packet.",
        },
    )
    targets = {r.target for r in updated.packet_read.unresolved_references}
    assert "Exhibit A" not in targets
    assert "Attachment A" in targets
    assert updated.fit_score == 4
    assert updated.recommendation == "go"


def test_delete_reflags_cite():
    prior = _prior()
    prior = prior.model_copy(
        update={
            "packet_read": RfpPacketRead(
                unresolvedReferences=[
                    UnresolvedReference(
                        target="Attachment A", quote="Agreement as Attachment A."
                    )
                ]
            )
        }
    )
    updated = apply_attachment_judgment(
        prior,
        cite_target="Exhibit A",
        action="delete",
        raw={
            "fitScore": 3,
            "worthScore": 3,
            "recommendation": "review",
            "attachmentNote": "Exhibit A removed.",
        },
        cite_quote="See Exhibit A.",
    )
    targets = {r.target for r in updated.packet_read.unresolved_references}
    assert "Exhibit A" in targets
    assert "Attachment A" in targets
