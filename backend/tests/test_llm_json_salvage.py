"""Salvage excerpt JSON when the model puts raw markdown table rows in replacement."""

from __future__ import annotations

import unittest

from app.services.llm import _parse_json_response, _salvage_simple_content_payload


class ReplacementJsonSalvageTests(unittest.TestCase):
    def test_salvages_table_rows_with_raw_newlines(self) -> None:
        raw = '''```json
{
  "replacement": "Events conducted per island | 4+ per quarter |
| Total attendance | Track and report quarterly |
| Employer and employee enrollments per event | Track conversion rate |
'''
        parsed = _parse_json_response(raw)
        self.assertIn("replacement", parsed)
        self.assertIn("Events conducted per island", parsed["replacement"])
        self.assertIn("Total attendance", parsed["replacement"])

    def test_salvages_fenced_truncated_issues_array(self) -> None:
        raw = '''```json
{
  "issues": [
    {
      "code": "other",
      "summary": "Section contains only checkbox items for appendices but no actual insurance, cost/pricing, or referenc'''
        parsed = _parse_json_response(raw)
        self.assertIn("issues", parsed)
        self.assertEqual(parsed["issues"][0]["code"], "other")
        self.assertIn("checkbox", parsed["issues"][0]["summary"])

    def test_salvages_issues_with_raw_newlines_in_summary(self) -> None:
        raw = '''```json
{
  "issues": [
    {
      "code": "other",
      "summary": "Section has checkboxes
and no insurance prose",
      "verbatimQuote": "Staff Biographies",
      "replacement": "[MANUAL FILL]",
      "fixAction": "replace"
    }
  ]
}
```'''
        parsed = _parse_json_response(raw)
        self.assertEqual(len(parsed["issues"]), 1)
        self.assertIn("checkboxes", parsed["issues"][0]["summary"])
        self.assertEqual(parsed["issues"][0]["verbatimQuote"], "Staff Biographies")

    def test_salvage_helper_prefers_replacement_key(self) -> None:
        blob = '{"replacement": "a | b |\n| c | d |"}\n'
        out = _salvage_simple_content_payload(blob)
        assert out is not None
        self.assertIn("a | b |", out["replacement"])
        self.assertIn("c | d |", out["replacement"])

    def test_parses_complete_json_followed_by_trailing_chatty_prose(self) -> None:
        """A model can answer the schema in full AND then keep talking (e.g. an
        unsolicited clarifying question after the JSON) — the complete, valid
        JSON object must still parse, ignoring everything after it closes."""
        raw = '''```json
{
  "understoodAsk": "User wants a general improvement with no specifics.",
  "mode": "patch",
  "patches": [],
  "kbQueries": []
}
```

---

**I need more detail to help you effectively.**

Please clarify what improvements you would like:
- Content gaps?
- Clarity issues?
'''
        parsed = _parse_json_response(raw)
        self.assertEqual(parsed["mode"], "patch")
        self.assertEqual(parsed["patches"], [])
        self.assertEqual(parsed["kbQueries"], [])

    def test_still_repairs_genuinely_truncated_json(self) -> None:
        """Trailing-content isolation must not break the existing mid-generation
        truncation repair — a response with no closing brace at all still
        needs the LIFO-close fallback."""
        raw = '''```json
{
  "issues": [
    {
      "code": "other",
      "summary": "cut off mid'''
        parsed = _parse_json_response(raw)
        self.assertIn("issues", parsed)
        self.assertEqual(parsed["issues"][0]["code"], "other")

    def test_does_not_clobber_strategy_delivery_with_budget_salvage(self) -> None:
        """Valid strategy_delivery JSON must not be replaced by Stage-3 budget
        salvage just because it lacks top-level sections/lineItems and mentions
        budgetFormat (e.g. nested under budget)."""
        raw = """{
  "strategy": {"winningTheme": "Local CBSM", "confidence": 0.8},
  "deliveryPattern": {"patternsObserved": ["hybrid"], "confidence": 0.7},
  "deliveryModel": {"type": "Hybrid", "confidence": 0.7},
  "methodology": {"phases": [], "confidence": 0.5},
  "budget": {
    "pricingStrategy": "value",
    "pricingModel": "Fixed Fee",
    "pricingTiers": "Average",
    "budgetFormat": "phased",
    "confidence": 0.6
  },
  "risk": {"risks": [], "confidence": 0.5},
  "qa": {"approach": "x", "gates": [], "confidence": 0.5},
  "communication": {"cadence": "weekly", "channels": [], "reportingPlan": "", "confidence": 0.5},
  "training": {"trainingPlan": "", "transitionPlan": "", "confidence": 0.5}
}"""
        parsed = _parse_json_response(raw)
        self.assertEqual(parsed["strategy"]["winningTheme"], "Local CBSM")
        self.assertEqual(parsed["deliveryModel"]["type"], "Hybrid")
        self.assertNotIn("budgetFormat", parsed)  # must stay nested, not top-level wipe

    def test_classification_salvage_skips_truncated_opportunity_json(self) -> None:
        """Truncated opportunity JSON mentions industry but must not become a
        classification stub — that wiped Agent 1 and caused missing client 502s."""
        from app.services.llm import _salvage_classification_payload

        raw = '''{
  "understanding": {
    "client": "County of Example",
    "industry": "Public Sector",
    "projectType": "Website
'''
        self.assertIsNone(_salvage_classification_payload(raw))
        # Truncation closer may still recover a partial object — that's fine as
        # long as it isn't replaced by {industry, servicesRequested} only.
        parsed = _parse_json_response(raw)
        self.assertIn("understanding", parsed)
        self.assertNotIn("servicesRequested", parsed)


if __name__ == "__main__":
    unittest.main()
