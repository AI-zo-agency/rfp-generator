from app.models.proposal import ProposalDraft, ProposalSection, VoiceFinding
from app.services.proposal_repository import _carry_voice_state


def _draft(**kw):
    return ProposalDraft(
        rfpId="r1",
        sections=[ProposalSection(id="a", title="A", content="x")],
        updatedAt="2026-09-30T00:00:00Z",
        **kw,
    )


def _finding():
    return VoiceFinding(sectionId="a", find="f", rule="r", kind="needs_human", detail="d")


def test_voice_state_round_trips_through_json():
    d = _draft(voiceReviewed=["h1", "h2"], voiceFindings=[_finding()])
    back = ProposalDraft.model_validate_json(d.model_dump_json(by_alias=True))
    assert back.voice_reviewed == ["h1", "h2"]
    assert back.voice_findings[0].find == "f"


def test_old_payload_without_voice_fields_loads():
    d = ProposalDraft.model_validate({"rfpId": "r1", "sections": [], "updatedAt": "x"})
    assert d.voice_reviewed == [] and d.voice_findings == []


def test_carry_copies_from_existing_when_incoming_is_empty():
    existing = _draft(voiceReviewed=["h1"], voiceFindings=[_finding()])
    incoming = _draft()
    _carry_voice_state(incoming, existing)
    assert incoming.voice_reviewed == ["h1"]
    assert len(incoming.voice_findings) == 1


def test_carry_never_overwrites_incoming_state():
    existing = _draft(voiceReviewed=["old"])
    incoming = _draft(voiceReviewed=["new"])
    _carry_voice_state(incoming, existing)
    assert incoming.voice_reviewed == ["new"]


def test_carry_with_no_existing_draft_is_a_no_op():
    incoming = _draft()
    _carry_voice_state(incoming, None)
    assert incoming.voice_reviewed == []
