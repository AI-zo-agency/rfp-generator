import unittest

from app.services.proposal_intelligence.schemas import (
    OutlineSection,
    ProposalOutline,
    RetrievalEntry,
)


class WritingAgentTests(unittest.TestCase):
    def test_retrieval_plan_has_no_excerpt_field(self) -> None:
        fields = set(RetrievalEntry.model_fields)
        self.assertNotIn("excerpt", fields)
        self.assertNotIn("content", fields)

    def test_outline_supports_nesting(self) -> None:
        outline = ProposalOutline(
            sections=[
                OutlineSection(
                    id="4", title="Approach", order=1, required=True, children=["4.1"]
                ),
                OutlineSection(
                    id="4.1", title="Discovery", order=2, required=True, parentId="4"
                ),
            ],
            confidence=0.9,
        )
        self.assertEqual(outline.sections[1].parent_id, "4")


if __name__ == "__main__":
    unittest.main()
