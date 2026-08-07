"""step_trace must tolerate summary fields that overlap explicit kwargs."""

from __future__ import annotations

from unittest.mock import patch

from app.core.step_debug_logger import step_trace, summarize_sections
from app.models.proposal import ProposalSection


def test_senior_editor_start_kwargs_do_not_collide() -> None:
    """Bug: verify_tag_count=… + **summarize_sections(…) → TypeError, Phase 3.6 abort."""
    sections = [
        ProposalSection(
            id="s1",
            title="A",
            content="Real prose about our work for Medford. " * 8,
            status="generated",
            source="generated",
        )
    ]
    summary = summarize_sections(sections)
    assert "verify_tag_count" in summary

    with patch("app.core.step_debug_logger._get_logger") as mock_logger:
        # Merged form used by the fixed self-edit loop — must not raise.
        step_trace(
            "senior_editor_start",
            rfp_id="rfp-test",
            **{**summary, "verify_tag_count": 3},
        )
        mock_logger.return_value.info.assert_called()
