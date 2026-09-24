"""Opportunity JSON extraction resilience (trailing commas / truncate)."""

from __future__ import annotations

import unittest

from agent1_tools import _extract_json_object, _strip_trailing_commas


class OpportunityJsonExtractTests(unittest.TestCase):
    def test_trailing_commas_parse(self) -> None:
        raw = """```json
{
  "understanding": {"buyerClient": "City of Newport Beach",},
  "compliance": {"items": [],},
  "scope": {"mandatory": ["Website"], "optional": [], "notes": "",},
}
```"""
        parsed = _extract_json_object(raw)
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed["understanding"]["buyerClient"], "City of Newport Beach")
        self.assertEqual(parsed["scope"]["mandatory"], ["Website"])

    def test_truncated_closed(self) -> None:
        raw = """{
  "understanding": {"buyerClient": "City of Newport Beach"},
  "compliance": {"items": [{"id": "c1", "requirement": "Due date", "mandatory": true}]},
  "scope": {"mandatory": ["SOW item"], "optional": [], "notes": ""
"""
        parsed = _extract_json_object(raw)
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertIn("understanding", parsed)
        self.assertIn("scope", parsed)

    def test_strip_trailing_commas_helper(self) -> None:
        s = '{"a": 1, "b": [2, 3,],}'
        self.assertEqual(_strip_trailing_commas(s), '{"a": 1, "b": [2, 3]}')


if __name__ == "__main__":
    unittest.main()
