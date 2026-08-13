"""Readiness rating for a scanned proposal draft.

    readiness = Σ(score/5 × weight) / Σ(weight)
                − open MANUAL FILL penalty
                − unresolved-after-3-rounds penalty

Replaces a `ready_to_submit: bool` that reads true on nearly every draft. The value of
this number is not in printing 95%; it is in printing 72% and naming the four things
standing between the draft and submission.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from app.models.proposal import ManualFillFlag

logger = logging.getLogger(__name__)

MAX_SCORE = 5

# With any disqualifying item open, the bid can be thrown out regardless of prose
# quality, so the number must not read "ready" however well the draft scores.
DQ_CEILING = 85

# Deductions are per-item with caps, so a long tail of small gaps cannot alone drive
# the score to zero and drown out the criterion scores.
SCORED_FLAG_PENALTY = 2
SCORED_FLAG_PENALTY_CAP = 20
UNRESOLVED_PENALTY = 1
UNRESOLVED_PENALTY_CAP = 10

READY_THRESHOLD = 90

Confidence = Literal["high", "low"]


@dataclass(frozen=True)
class CriterionScore:
    """One evaluator verdict: how well a section serves a scored criterion."""

    section_id: str
    criterion: str
    score: int
    weight: float | None = None


@dataclass(frozen=True)
class ReadinessResult:
    score: int
    confidence: Confidence
    confidence_note: str
    verdict: str
    ready: bool
    open_disqualifying: int
    open_scored: int
    unresolved: int
    # False when no criterion scores existed to average. `score` is then 0 because
    # nothing was measured, which is not the same claim as "this draft scores zero" —
    # callers must render the difference rather than print a damning percentage.
    measured: bool = True


def _weighted_base(scores: list[CriterionScore]) -> tuple[float, int]:
    """Return (0..100 base, count of scores missing a weight).

    Sections whose weight is None fall back to an equal share rather than zero: many
    RFPs never publish weights, and treating "unpublished" as "worthless" would erase
    real sections from the number. The caller reports the degradation as low confidence.
    """
    missing = sum(1 for s in scores if s.weight is None)
    if not scores:
        return 0.0, 0

    usable = [(s, s.weight) for s in scores if s.weight is not None and s.weight > 0]
    total_weight = sum(w for _s, w in usable)

    if not usable or total_weight <= 0:
        # No usable weights at all — plain mean.
        mean = sum(_clamp_score(s.score) for s in scores) / len(scores)
        return (mean / MAX_SCORE) * 100, missing

    if missing:
        # Mixed: weight what we can, and let unweighted entries carry the average
        # weight so they still move the number in proportion to their presence.
        avg_weight = total_weight / len(usable)
        pairs = [(s, s.weight if s.weight is not None else avg_weight) for s in scores]
    else:
        pairs = [(s, w) for s, w in usable]

    denom = sum(w for _s, w in pairs)
    if denom <= 0:
        mean = sum(_clamp_score(s.score) for s in scores) / len(scores)
        return (mean / MAX_SCORE) * 100, missing

    numer = sum((_clamp_score(s.score) / MAX_SCORE) * w for s, w in pairs)
    return (numer / denom) * 100, missing


def _clamp_score(score: int) -> int:
    return max(0, min(MAX_SCORE, int(score)))


def _verdict_line(
    *, score: int, dq: int, scored: int, unresolved: int, ready: bool
) -> str:
    """One line a human can act on, not a label."""
    if dq:
        item = "item" if dq == 1 else "items"
        return f"Not ready: {dq} disqualifying {item} open."
    if ready and not scored and not unresolved:
        return f"Ready to submit: {score}%."
    parts: list[str] = []
    if scored:
        parts.append(f"{scored} scored gap{'s' if scored != 1 else ''}")
    if unresolved:
        parts.append(f"{unresolved} unverified claim{'s' if unresolved != 1 else ''}")
    if not parts:
        return f"Not ready: {score}% — criterion scores below target."
    return f"Not ready: {score}% — {', '.join(parts)} outstanding."


def compute_readiness(
    *,
    scores: list[CriterionScore],
    flags: list[ManualFillFlag],
    unresolved: int = 0,
) -> ReadinessResult:
    """Score the draft, subtracting what remains open rather than hiding it."""
    base, missing_weights = _weighted_base(scores)

    open_dq = sum(1 for f in flags if f.criticality == "disqualifying")
    open_scored = sum(1 for f in flags if f.criticality == "scored")
    # "optional" is deliberately absent: those are removed from the manuscript and
    # from the report, so they must not move the number either.

    penalty = min(open_scored * SCORED_FLAG_PENALTY, SCORED_FLAG_PENALTY_CAP)
    penalty += min(max(0, unresolved) * UNRESOLVED_PENALTY, UNRESOLVED_PENALTY_CAP)

    score = int(round(max(0.0, base - penalty)))
    if open_dq:
        # A ceiling, never a floor — a weak draft with a DQ item does not get raised.
        score = min(score, DQ_CEILING)
    score = max(0, min(100, score))

    total = len(scores)
    if not total or missing_weights * 2 > total:
        confidence: Confidence = "low"
    else:
        confidence = "high"

    if not total:
        note = "no scored criteria available"
    elif missing_weights:
        note = f"weights unpublished for {missing_weights} of {total} sections"
    else:
        note = f"weights published for all {total} sections"

    measured = bool(scores)
    ready = score >= READY_THRESHOLD and not open_dq and measured
    if not measured:
        gaps = open_dq + open_scored
        verdict = (
            "Readiness not scored — no evaluator criteria available"
            + (f"; {gaps} open submission gap{'s' if gaps != 1 else ''}." if gaps else ".")
        )
    else:
        verdict = _verdict_line(
            score=score,
            dq=open_dq,
            scored=open_scored,
            unresolved=unresolved,
            ready=ready,
        )

    logger.info(
        "readiness score=%s confidence=%s dq=%s scored=%s unresolved=%s",
        score,
        confidence,
        open_dq,
        open_scored,
        unresolved,
    )
    return ReadinessResult(
        score=score,
        confidence=confidence,
        confidence_note=note,
        verdict=verdict,
        ready=ready,
        open_disqualifying=open_dq,
        open_scored=open_scored,
        unresolved=unresolved,
        measured=measured,
    )
