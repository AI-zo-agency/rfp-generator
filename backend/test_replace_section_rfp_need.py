"""Replace section topic with a different RFP need — detectors / guards."""

from __future__ import annotations

import unittest

from app.services.proposal_section_editor import (
    _user_asks_replace_section_for_other_rfp_need,
)


class ReplaceSectionRfpNeedAskTests(unittest.TestCase):
    def test_detects_user_ask(self) -> None:
        msg = (
            "can you just replace section 15 with some other new section? "
            "according to needs of rfp?? Scan rfp again and replace that "
            "section with some other need sof rfp"
        )
        self.assertTrue(_user_asks_replace_section_for_other_rfp_need(msg))

    def test_polish_same_section_not_replace_topic(self) -> None:
        self.assertFalse(
            _user_asks_replace_section_for_other_rfp_need(
                "make the tourism experience section tighter and clearer"
            )
        )

    def test_verify_fill_not_replace_topic(self) -> None:
        self.assertFalse(
            _user_asks_replace_section_for_other_rfp_need(
                "Cross-check Percent-Time against KB and replace with VERIFY"
            )
        )


if __name__ == "__main__":
    unittest.main()
