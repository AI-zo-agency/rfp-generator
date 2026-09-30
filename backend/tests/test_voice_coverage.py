"""voice_coverage_gaps and the Review issue for sections the voice pass never looked at."""

import pytest

from app.models.proposal import ProposalDraft, ProposalSection
from app.models.rfp import RfpRecord
from app.services import proposal_voice_llm as pv, proposal_voice_pass as vp
from app.services import proposal_presubmit_review as review

TEXT = "This paragraph has plenty of words in it."
MESSAGE = "Voice pass has not reviewed this section"


@pytest.fixture(autouse=True)
def _rev(monkeypatch):
    monkeypatch.setattr(vp, "voice_standards_for", lambda rfp_id=None: ("STD", "r7"))


def _section(id_, mode="write", content=TEXT):
    return ProposalSection(id=id_, title=id_.title(), mode=mode, content=content)


def _draft(sections, **kw):
    return ProposalDraft(rfpId="r1", sections=sections, updatedAt="2026-09-30T00:00:00Z", **kw)


def _rfp():
    return RfpRecord(
        id="r1", title="T", client="C", dueDate="2026-12-01", receivedDate="2026-09-01",
        lastActivity="2026-09-01", lastActivityNote="n",
    )


def test_gaps_reviewed_unreviewed_nonwrite_and_empty():
    d = _draft([_section("a"), _section("b"), _section("c", mode="pull"), _section("d", content=" "), _section("e", content="# Heading only")])
    assert vp.voice_coverage_gaps(d) == ["a", "b"]
    d = d.model_copy(update={"voice_reviewed": [pv.block_hash(TEXT, "r7")]})
    assert vp.voice_coverage_gaps(d) == []


def test_partly_reviewed_section_is_a_gap():
    two = f"{TEXT}\n\nA second paragraph with plenty of words."
    d = _draft([_section("a", content=two)], voiceReviewed=[pv.block_hash(TEXT, "r7")])
    assert vp.voice_coverage_gaps(d) == ["a"]


def _messages(issues):
    return [i for i in issues if i.message == MESSAGE]


def test_review_flags_unreviewed_section_then_clears():
    d = _draft([_section("a")])
    got = _messages(review.run_presubmit_review(rfp=_rfp(), draft=d, research=None).issues)
    assert [(i.section_id, i.severity, i.category) for i in got] == [("a", "info", "voice")]

    d = d.model_copy(update={"voice_reviewed": [pv.block_hash(TEXT, "r7")]})
    assert _messages(review.run_presubmit_review(rfp=_rfp(), draft=d, research=None).issues) == []


def test_section_scan_used_by_autofix_never_reports_coverage():
    assert _messages(review.scan_section_issues(section=_section("a"), rfp=_rfp())) == []
