import unittest

from app.services.proposal_voice_enforcement import enforce_narrative_voice


class RegisterSwapTests(unittest.TestCase):
    def test_third_person_vendor_becomes_we(self):
        cleaned = enforce_narrative_voice(
            "The Vendor delivers a plan in six weeks.",
            section_id="approach",
            title="Approach",
            zo_mode="write",
        )
        self.assertIn("We deliver", cleaned)
        self.assertNotIn("The Vendor", cleaned)
        self.assertIn("six weeks", cleaned)
