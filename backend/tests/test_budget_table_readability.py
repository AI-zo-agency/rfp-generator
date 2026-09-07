"""Budget fee table: no em dashes, and Scope reads as prose.

Live defect: the Scope cell was "; ".join(line-item descriptions), and every
description repeated its own phase label — so the Phase column was restated
inside the Scope cell, joined by semicolons, with em dashes throughout:

  Phase: "Group 1 — Media Planning and Advertising"
  Scope: "Group 1 Media Planning & Advertising — Digital Campaign Strategy
          covering paid search...; Group 1 Media Planning & Advertising —
          Monthly Digital Advertising"
"""

from __future__ import annotations

from app.services.proposal_budget_content import _no_em_dash, _scope_sentence

EM, EN = "—", "–"


def test_no_em_or_en_dashes_survive():
    for raw in (
        "Group 1 — Media Planning and Advertising",
        "Phase 2 – Brand Systems",
        "A — B — C",
    ):
        out = _no_em_dash(raw)
        assert EM not in out and EN not in out, out


def test_label_dash_becomes_a_colon():
    assert _no_em_dash("Group 1 — Media Planning and Advertising") == (
        "Group 1: Media Planning and Advertising"
    )


def test_scope_does_not_restate_the_phase_column():
    scope = _scope_sentence(
        "Group 1: Media Planning and Advertising",
        [
            "Group 1 Media Planning & Advertising — Digital Campaign Strategy "
            "covering paid search, paid social, and programmatic planning "
            "(RFP §1, Group 1)",
            "Group 1 Media Planning & Advertising — Monthly Digital Advertising",
        ],
    )
    # "&" vs "and" must not defeat the prefix strip.
    assert "Media Planning" not in scope, scope
    assert scope.startswith("Digital Campaign Strategy"), scope
    assert "Monthly Digital Advertising" in scope
    assert EM not in scope and ";" not in scope
    assert scope.endswith(".")


def test_scope_keeps_real_detail_and_dedupes():
    scope = _scope_sentence("Brand", ["Logo suite", "Brand guidelines", "Logo suite"])
    assert scope == "Logo suite. Brand guidelines."


def test_empty_descriptions_fall_back_without_crashing():
    assert _scope_sentence("Anything", []) == "Professional services"
    assert _no_em_dash("") == ""
