"""Per-track segmentation for RFPs that split scope into tracks/lots.

Root cause this guards: the Go/No-Go capability matrix had no concept of RFP
tracks/lots. On a real two-track RFP (City of Santa Monica), every Track 2
requirement (media relations, crisis comms, spokesperson training) landed in
the SAME denominator as Track 1's (creative/video/brand) in
``calibrate_technical_capability_score``, collapsing a blended Technical
Capability score to 1/5 and producing NO-GO even though Track 1 alone was a
clear GO. These tests pin the segmentation helpers that let a caller score
each track on its own evidence.
"""

from __future__ import annotations

import unittest

from app.models.go_no_go import GoNoGoCapabilityRow
from app.services.go_no_go_capability import (
    calibrate_technical_capability_score,
    per_track_resource_scores,
    per_track_technical_scores,
    rows_for_track,
    tracks_in_rows,
)


def _row(
    requirement: str,
    status: str,
    *,
    core: bool = True,
    category: str = "technical",
    track: str = "",
) -> GoNoGoCapabilityRow:
    return GoNoGoCapabilityRow(
        requirement=requirement,
        status=status,
        isCore=core,
        category=category,
        track=track,
    )


class TracksInRowsTests(unittest.TestCase):
    def test_all_empty_tracks_returns_empty_list(self) -> None:
        rows = [
            _row("CMS implementation", "verified"),
            _row("Hosting", "gap"),
        ]
        self.assertEqual(tracks_in_rows(rows), [])

    def test_mixed_tracks_preserve_first_seen_order(self) -> None:
        rows = [
            _row("Video production", "verified", track="Track 1"),
            _row("Insurance", "verified", track=""),
            _row("Media relations", "gap", track="Track 2"),
            _row("Crisis comms", "gap", track="Track 2"),
            _row("Brand strategy", "verified", track="Track 1"),
        ]
        self.assertEqual(tracks_in_rows(rows), ["Track 1", "Track 2"])


class RowsForTrackTests(unittest.TestCase):
    def test_includes_own_track_and_track_agnostic_rows(self) -> None:
        rows = [
            _row("Video production", "verified", track="Track 1"),
            _row("Media relations", "gap", track="Track 2"),
            _row("Insurance certificate", "verified", category="compliance", track=""),
        ]
        out = rows_for_track(rows, "Track 1")
        names = {r.requirement for r in out}
        self.assertEqual(names, {"Video production", "Insurance certificate"})

    def test_excludes_other_tracks_rows(self) -> None:
        rows = [
            _row("Video production", "verified", track="Track 1"),
            _row("Media relations", "gap", track="Track 2"),
        ]
        out = rows_for_track(rows, "Track 1")
        self.assertNotIn(
            "Media relations", {r.requirement for r in out}
        )


class PerTrackScenarioTests(unittest.TestCase):
    """The real Santa Monica-style scenario: Track 1 strong, Track 2 weak.

    Track 2 carries many more (core) requirements than Track 1, exactly like
    the live RFP where Track 2's media-relations/crisis-comms/spokesperson-
    training scope out-numbered Track 1's creative/video/brand asks. Pooling
    both tracks into one denominator is what collapsed the blended score.
    """

    def _rows(self) -> list[GoNoGoCapabilityRow]:
        track_1 = [
            _row("Video production", "verified", core=False, track="Track 1"),
            _row("Brand strategy", "verified", core=False, track="Track 1"),
            _row("Creative direction", "verified", core=False, track="Track 1"),
        ]
        track_2 = [
            _row(f"Track 2 requirement {i}", "gap", core=True, track="Track 2")
            for i in range(15)
        ]
        return track_1 + track_2

    def test_track_1_scores_materially_higher_than_track_2(self) -> None:
        rows = self._rows()
        scores = per_track_technical_scores(rows)
        self.assertIn("Track 1", scores)
        self.assertIn("Track 2", scores)
        self.assertGreater(
            scores["Track 1"],
            scores["Track 2"],
            msg=f"expected Track 1 to score materially higher, got {scores}",
        )
        self.assertGreaterEqual(scores["Track 1"] - scores["Track 2"], 3)

    def test_blended_score_is_at_or_below_track_2_score(self) -> None:
        """Regression test for the bug: pooling tracks understates the strong one."""
        rows = self._rows()
        scores = per_track_technical_scores(rows)
        blended = calibrate_technical_capability_score(rows)
        self.assertIsNotNone(blended)
        self.assertLessEqual(blended, scores["Track 2"])


class SingleScopeRfpTests(unittest.TestCase):
    def test_per_track_scores_empty_when_all_tracks_blank(self) -> None:
        rows = [
            _row("CMS implementation", "verified"),
            _row("Hosting", "gap"),
            _row("Project manager assigned", "verified", category="role"),
        ]
        self.assertEqual(per_track_technical_scores(rows), {})
        self.assertEqual(per_track_resource_scores(rows), {})


if __name__ == "__main__":
    unittest.main()
