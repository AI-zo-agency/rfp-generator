"""Readiness rating: weight-proportional, residue subtracted, DQ hard-capped, honest.

A rating that reads 95% on every draft carries no information. These tests pin the four
properties that make the number worth printing.
"""

from __future__ import annotations

from app.models.proposal import ManualFillFlag
from app.services.proposal_readiness import CriterionScore, compute_readiness


def _flag(criticality: str, tag: str = "item") -> ManualFillFlag:
    return ManualFillFlag(
        sectionId="s1",
        sectionTitle="Forms",
        kind="manual_fill",
        tag=tag,
        criticality=criticality,
    )


def _perfect(weight: float | None = 1.0) -> CriterionScore:
    return CriterionScore(section_id="s1", criterion="Approach", score=5, weight=weight)


class TestWeightProportional:
    def test_all_perfect_is_full_score(self):
        r = compute_readiness(scores=[_perfect(), _perfect()], flags=[], unresolved=0)
        assert r.score == 100

    def test_all_zero_is_zero(self):
        r = compute_readiness(
            scores=[CriterionScore("s1", "A", 0, 1.0)], flags=[], unresolved=0
        )
        assert r.score == 0

    def test_heavy_section_dominates_the_number(self):
        """A polished boilerplate tab cannot offset a weak scored one."""
        r = compute_readiness(
            scores=[
                CriterionScore("s1", "Approach", 0, 70.0),
                CriterionScore("s2", "Boilerplate", 5, 30.0),
            ],
            flags=[],
            unresolved=0,
        )
        assert r.score == 30

    def test_weight_zero_sections_do_not_crash_or_count(self):
        r = compute_readiness(
            scores=[CriterionScore("s1", "A", 5, 0.0), CriterionScore("s2", "B", 0, 0.0)],
            flags=[],
            unresolved=0,
        )
        assert 0 <= r.score <= 100


class TestResidueIsSubtracted:
    def test_open_scored_flags_deduct(self):
        clean = compute_readiness(scores=[_perfect()], flags=[], unresolved=0)
        dirty = compute_readiness(
            scores=[_perfect()], flags=[_flag("scored"), _flag("scored")], unresolved=0
        )
        assert dirty.score < clean.score

    def test_optional_flags_do_not_deduct(self):
        """Optional items are removed from the manuscript and absent from the report."""
        clean = compute_readiness(scores=[_perfect()], flags=[], unresolved=0)
        with_optional = compute_readiness(
            scores=[_perfect()], flags=[_flag("optional")], unresolved=0
        )
        assert with_optional.score == clean.score

    def test_unresolved_claims_deduct(self):
        clean = compute_readiness(scores=[_perfect()], flags=[], unresolved=0)
        residual = compute_readiness(scores=[_perfect()], flags=[], unresolved=5)
        assert residual.score < clean.score

    def test_score_never_goes_negative(self):
        r = compute_readiness(
            scores=[CriterionScore("s1", "A", 0, 1.0)],
            flags=[_flag("scored", f"t{i}") for i in range(50)],
            unresolved=50,
        )
        assert r.score == 0


class TestDisqualifyingHardCap:
    def test_open_dq_caps_a_perfect_draft(self):
        r = compute_readiness(scores=[_perfect()], flags=[_flag("disqualifying")], unresolved=0)
        assert r.score <= 85

    def test_dq_cap_does_not_raise_a_low_score(self):
        """The cap is a ceiling, never a floor."""
        r = compute_readiness(
            scores=[CriterionScore("s1", "A", 1, 1.0)],
            flags=[_flag("disqualifying")],
            unresolved=0,
        )
        assert r.score <= 20

    def test_dq_is_named_in_the_verdict(self):
        r = compute_readiness(scores=[_perfect()], flags=[_flag("disqualifying")], unresolved=0)
        assert "disqualifying" in r.verdict.casefold()
        assert not r.ready


class TestConfidenceIsStated:
    def test_full_weights_is_high_confidence(self):
        r = compute_readiness(scores=[_perfect(1.0), _perfect(2.0)], flags=[], unresolved=0)
        assert r.confidence == "high"

    def test_mostly_missing_weights_is_low_confidence_and_says_so(self):
        scores = [_perfect(None) for _ in range(6)] + [_perfect(1.0) for _ in range(3)]
        r = compute_readiness(scores=scores, flags=[], unresolved=0)
        assert r.confidence == "low"
        assert "6 of 9" in r.confidence_note

    def test_unweighted_degrades_to_plain_mean_not_zero(self):
        """Missing weights must not silently zero out a section."""
        r = compute_readiness(
            scores=[CriterionScore("s1", "A", 5, None), CriterionScore("s2", "B", 0, None)],
            flags=[],
            unresolved=0,
        )
        assert r.score == 50
        assert r.confidence == "low"


class TestDegenerateInputs:
    def test_no_scores_is_zero_with_low_confidence(self):
        r = compute_readiness(scores=[], flags=[], unresolved=0)
        assert r.score == 0
        assert r.confidence == "low"
        assert not r.ready

    def test_out_of_range_scores_are_clamped(self):
        r = compute_readiness(
            scores=[CriterionScore("s1", "A", 99, 1.0)], flags=[], unresolved=0
        )
        assert r.score == 100
