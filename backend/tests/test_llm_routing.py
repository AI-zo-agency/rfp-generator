"""T3.1 / T3.2 — cap-aware routing and quality-critical Fireworks preference."""

from __future__ import annotations

import unittest

from app.services.llm_routing import (
    FIREWORKS_OUTPUT_TOKEN_CAP,
    is_quality_critical_node,
    resolve_fireworks_eligibility,
)


class FireworksCapRoutingTests(unittest.TestCase):
    def test_cap_constant(self) -> None:
        self.assertEqual(FIREWORKS_OUTPUT_TOKEN_CAP, 8192)

    def test_allows_fireworks_when_request_within_cap(self) -> None:
        decision = resolve_fireworks_eligibility(
            requested_max_tokens=4096,
            prefer_fireworks=True,
            node_name="plan_section_1",
            openrouter_available=True,
            gemini_available=False,
        )
        self.assertTrue(decision.allow_fireworks)
        self.assertFalse(decision.skip_prefer_fireworks)
        self.assertIsNone(decision.block_reason)

    def test_blocks_silent_underserve_when_request_exceeds_cap_and_alt_exists(self) -> None:
        """Never min(requested, 8192) on Fireworks when caller asked for more — route away."""
        decision = resolve_fireworks_eligibility(
            requested_max_tokens=12288,
            prefer_fireworks=True,
            node_name="plan_section_1",
            openrouter_available=True,
            gemini_available=False,
        )
        self.assertFalse(decision.allow_fireworks)
        self.assertEqual(decision.effective_cap_if_fireworks, FIREWORKS_OUTPUT_TOKEN_CAP)
        self.assertIn("exceeds", (decision.block_reason or "").lower())

    def test_raises_policy_when_over_cap_and_no_alternative(self) -> None:
        decision = resolve_fireworks_eligibility(
            requested_max_tokens=16384,
            prefer_fireworks=True,
            node_name="budget_planner",
            openrouter_available=False,
            gemini_available=False,
        )
        self.assertFalse(decision.allow_fireworks)
        self.assertTrue(decision.must_raise)
        self.assertIn("no alternative", (decision.block_reason or "").lower())

    def test_none_max_tokens_treated_as_default_within_cap(self) -> None:
        decision = resolve_fireworks_eligibility(
            requested_max_tokens=None,
            prefer_fireworks=True,
            node_name="select_team",
            openrouter_available=True,
            gemini_available=False,
        )
        self.assertTrue(decision.allow_fireworks)


class QualityCriticalPreferFireworksTests(unittest.TestCase):
    def test_draft_sections_are_quality_critical(self) -> None:
        self.assertTrue(is_quality_critical_node("draft_sections:rfp-sec-9"))
        self.assertTrue(is_quality_critical_node("draft_sections"))

    def test_company_truth_and_case_studies_critical(self) -> None:
        self.assertTrue(is_quality_critical_node("fetch_company_truth"))
        self.assertTrue(is_quality_critical_node("build_case_studies"))
        self.assertTrue(is_quality_critical_node("build_section_1_cq"))

    def test_light_planners_not_critical(self) -> None:
        self.assertFalse(is_quality_critical_node("plan_section_1"))
        self.assertFalse(is_quality_critical_node("select_team"))

    def test_unnamed_node_now_defaults_to_quality(self) -> None:
        # Deliberate inversion. This previously returned False, so any call site
        # that omitted node_name was served by the cheapest provider — which is
        # how the repair agents, [VERIFY] scrubber and KB fact-checker all ended
        # up on the economy model while the drafter used a better one. Unknown
        # nodes now fail toward quality and log a warning naming the node.
        self.assertTrue(is_quality_critical_node(""))
        self.assertTrue(is_quality_critical_node(None))
        self.assertTrue(is_quality_critical_node("node_added_next_week"))

    def test_quality_critical_ignores_prefer_fireworks(self) -> None:
        decision = resolve_fireworks_eligibility(
            requested_max_tokens=8192,
            prefer_fireworks=True,
            node_name="draft_sections:rfp-sec-1",
            openrouter_available=True,
            gemini_available=False,
        )
        self.assertTrue(decision.skip_prefer_fireworks)
        # Still allow Fireworks as fallback if within cap — just not forced primary
        self.assertTrue(decision.allow_fireworks)

    def test_quality_critical_over_cap_still_blocks_fireworks(self) -> None:
        decision = resolve_fireworks_eligibility(
            requested_max_tokens=12288,
            prefer_fireworks=True,
            node_name="draft_sections:rfp-sec-9",
            openrouter_available=True,
            gemini_available=False,
        )
        self.assertTrue(decision.skip_prefer_fireworks)
        self.assertFalse(decision.allow_fireworks)


if __name__ == "__main__":
    unittest.main()
