"""Designer-compact acceptance — shorter layout must not be rejected as regression."""

from app.models.proposal import ProposalSection
from app.services.proposal_consistency import patch_improves_section
from app.services.proposal_manuscript_compact import (
    is_designer_compact_improvement,
    section_needs_designer_compact,
)
from app.models.rfp import RfpRecord


def _section(**kwargs) -> ProposalSection:
    base = {
        "id": "rfp-1",
        "title": "Approach & Timetable",
        "required": True,
        "custom": True,
        "source": "rfp",
        "mode": "write",
        "status": "generated",
        "word_target": 500,
    }
    base.update(kwargs)
    return ProposalSection.model_construct(**base)


def test_essay_wall_triggers_compact() -> None:
    essay = (
        "## Phase 1\n*Activities:*\n"
        + "- " + " ".join(["activity"] * 20) + "\n"
        + "## Phase 2\n*Deliverables:*\n"
        + "- " + " ".join(["deliverable"] * 20) + "\n"
        + "## Phase 3\nMore prose " * 80
    )
    section = _section(content=essay)
    assert section_needs_designer_compact(section)


def test_compact_improvement_accepted() -> None:
    before = _section(content="## Phase 1\n" + ("long essay paragraph. " * 200))
    after = _section(
        content=(
            "We deliver in seven gated phases.\n\n"
            "| Phase | Weeks | Activities | Deliverables |\n"
            "| --- | --- | --- | --- |\n"
            "| 1 | 6 | Discovery | Report |\n\n"
            "[DESIGNER NOTE: Gantt — phases 1–7 with review periods]"
        )
    )
    assert is_designer_compact_improvement(before, after)


def test_patch_improves_section_accepts_designer_compact() -> None:
    before = _section(content="## Phase 1\n" + ("word " * 900))
    after = _section(
        content=(
            "We deliver through phased gates with written acceptance at each milestone.\n\n"
            "| Phase | Weeks | Key deliverables |\n"
            "| --- | --- | --- |\n"
            "| 1 | 6 | Discovery report, strategy sign-off |\n"
            "| 2 | 6 | Brand standards guide |\n\n"
            "[DESIGNER NOTE: Gantt — phases 1–7 with review periods]"
        )
    )
    rfp = RfpRecord.model_construct(
        id="x",
        title="Test",
        client="Client",
        sector="gov",
        status="active",
        go_no_go="go",
    )
    assert patch_improves_section(
        before, after, rfp=rfp, designer_compact=True
    )
