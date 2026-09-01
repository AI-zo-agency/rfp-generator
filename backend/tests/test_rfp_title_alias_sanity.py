"""_titles_are_same_ask must never trust an alias with nothing in common with
the RFP title it claims to restate.

Regression for a real incident: extract_rfp_scored_section_specs (an LLM
call) listed "Qualifications and Experience of the Firm" as a sameAskAs
alias for an RFP-mandated title of "RESPONSE FILE" — sharing zero tokens,
zero topical overlap. apply_rfp_mandated_section_titles trusted it blindly
and relabeled a section holding real "three satisfactory references" content
to "RESPONSE FILE", while a completely different section (an RFP-minimum
signature-page stub) got relabeled to "Qualifications and Experience of the
Firm" in the same run — two sections silently swapped identities, each now
mislabeled relative to its own content.
"""

from __future__ import annotations

import unittest

from app.services.proposal_fulfill_rfp_structure import _titles_are_same_ask


class TitleAliasSanityTests(unittest.TestCase):
    def test_hallucinated_alias_with_no_shared_tokens_is_rejected(self) -> None:
        """Exact real-world repro: the alias the model actually returned."""
        self.assertFalse(
            _titles_are_same_ask(
                "RESPONSE FILE",
                "Qualifications and Experience of the Firm",
                aliases=["Qualifications and Experience of the Firm"],
            )
        )

    def test_legitimate_alias_sharing_a_real_token_is_accepted(self) -> None:
        self.assertTrue(
            _titles_are_same_ask(
                "Cost Proposal",
                "COST FILE",
                aliases=["COST FILE"],
            )
        )

    def test_direct_title_match_still_works_without_any_alias(self) -> None:
        self.assertTrue(
            _titles_are_same_ask(
                "Cover Letter",
                "Cover Letter",
                aliases=[],
            )
        )

    def test_unrelated_titles_with_no_alias_are_rejected(self) -> None:
        self.assertFalse(
            _titles_are_same_ask(
                "RESPONSE FILE",
                "Qualifications and Experience of the Firm",
                aliases=[],
            )
        )


if __name__ == "__main__":
    unittest.main()
