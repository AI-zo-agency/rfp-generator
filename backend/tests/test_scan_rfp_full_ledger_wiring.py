"""Full Scan RFP body wires requirement ledger + thorough budget steps."""

from __future__ import annotations

import inspect
import unittest

from app.services import proposal_fulfill_rfp_gaps as fulfill_mod


class FullScanStepWiringTests(unittest.TestCase):
    def test_full_body_calls_ledger_and_budget_meta(self) -> None:
        source = inspect.getsource(fulfill_mod._run_fulfill_rfp_gaps_body)
        self.assertIn("run_scan_coverage_orchestrator", source)
        self.assertIn("DQ & gov-policy gate (agentic loop)", source)
        self.assertIn("budget_meta", source)


if __name__ == "__main__":
    unittest.main()
