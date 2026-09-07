"""A section that ends on a promise it never fills is incomplete — at ANY length.

Live cases that shipped undetected, all far under the 350-char floor that
_looks_truncated_prose requires, so no detector saw them and no repair ran:

  §23  "...withhold from disclosure.\\n\\n**Confidential and/or Proprietary
        Declaration**"                       <- heading, nothing under it
  §26  "...below we cross-reference only what each engagement proves..."
  §28  References Form: intro, then no references
"""

from __future__ import annotations

from app.services.proposal_fulfill_truncation_repair import (
    ends_on_an_unfilled_promise,
    looks_truncated_for_fulfill,
)

LIVE_23 = (
    "We reviewed our submittal for any material that Arizona public records "
    "law would allow us to withhold from disclosure.\n\n"
    "**Confidential and/or Proprietary Declaration**"
)


def test_the_live_case_is_detected():
    assert ends_on_an_unfilled_promise(LIVE_23)
    assert looks_truncated_for_fulfill(LIVE_23)


def test_short_length_does_not_hide_it():
    # 150 chars — under both the 350 prose floor and the 60-char floor path.
    assert len(LIVE_23) < 350
    assert looks_truncated_for_fulfill(LIVE_23)


def test_every_promise_shape():
    for body in (
        "Complete intro sentence.\n\n## Monthly Performance Dashboard",
        "Our experience maps to the three service groups below:",
        "Fees follow.\n\n| Phase | Scope | Fee |\n| --- | --- | ---: |",
        "Intro.\n\n**Required Insurance Coverage**",
    ):
        assert ends_on_an_unfilled_promise(body), body


def test_complete_sections_are_left_alone():
    for body in (
        "We complete the required Appendix disclosures below as part of this "
        "Statement of Qualifications.",
        "**Total proposed investment:** $2,200",
        "| Phase | Scope | Fee |\n| --- | --- | ---: |\n| Discovery | Interviews. | $50,000 |",
    ):
        assert not ends_on_an_unfilled_promise(body), body


def test_a_manual_fill_handoff_is_not_broken():
    # A tag is a deliberate, complete handoff to a human — flagging it as
    # truncated would send the repair pass to overwrite the human's cue.
    body = (
        "The references below reflect completed engagements.\n\n"
        "[MANUAL FILL: Sonja — supply verified client references from ClientList.]"
    )
    assert not ends_on_an_unfilled_promise(body)


def test_empty_and_garbage_never_raise():
    for body in ("", "   ", "\n\n", None):
        assert ends_on_an_unfilled_promise(body) is False
