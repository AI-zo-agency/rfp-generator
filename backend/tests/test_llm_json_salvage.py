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


if __name__ == "__main__":
    unittest.main()
