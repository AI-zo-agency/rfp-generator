"""Tests for optional [VERIFY] scrub intent + helpers."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app.services.proposal_verify_optional_scrub import (
    count_verify_tags,
    scrub_optional_verify_tags,
    user_asks_scrub_optional_verify,
)


class TestVerifyOptionalScrubIntent(unittest.TestCase):
    def test_remove_verify_tags(self) -> None:
        self.assertTrue(
            user_asks_scrub_optional_verify("remove verify tags from this section")
        )
        self.assertTrue(
            user_asks_scrub_optional_verify("Strip all [VERIFY] placeholders")
        )
        self.assertTrue(
            user_asks_scrub_optional_verify(
                "clean verify tags if not needed for the RFP"
            )
        )

    def test_fill_is_not_scrub(self) -> None:
        self.assertFalse(
            user_asks_scrub_optional_verify("Fill [VERIFY] tags from KB only.")
        )
        self.assertFalse(
            user_asks_scrub_optional_verify("resolve verify tags with knowledge base")
        )

    def test_count_tags(self) -> None:
        text = (
            "A [VERIFY: subcontractor name] and [VERIFY: backup] plus [VERIFY]"
        )
        self.assertEqual(count_verify_tags(text), 3)


class TestVerifyOptionalScrubAsync(unittest.IsolatedAsyncioTestCase):
    async def test_noop_when_no_tags(self) -> None:
        result = await scrub_optional_verify_tags(
            "Clean prose with no gaps.",
            section_title="Scope",
            rfp_text="RFP requires methodology.",
        )
        self.assertFalse(result.changed)
        self.assertEqual(result.tags_before, 0)

    async def test_scrub_removes_optional_via_llm(self) -> None:
        body = (
            "Subcontractor (Role)\n"
            "[VERIFY: subcontractor name, mobile app partner]\tPaaS build\n"
            "[VERIFY: backup mobile partner]\tWCAG\n"
        )
        fake = {
            "content": (
                "Subcontractor roles are filled by vetted partners as needed "
                "for PaaS mobile build and WCAG accessibility — specific "
                "named firms confirmed at kickoff if subcontracting is used."
            ),
            "keptRequiredCount": 0,
            "note": "Removed optional named-subcontractor VERIFY tags; RFP does not require names.",
        }
        with (
            patch(
                "app.services.proposal_verify_optional_scrub.llm.is_configured",
                return_value=True,
            ),
            patch(
                "app.services.proposal_verify_optional_scrub.llm.chat_json",
                new=AsyncMock(return_value=(fake, "test")),
            ),
        ):
            result = await scrub_optional_verify_tags(
                body,
                section_title="Services Performed by Subcontractors",
                rfp_text="Vendor may subcontract with County approval. Names optional.",
            )
        self.assertTrue(result.changed)
        self.assertEqual(result.tags_before, 2)
        self.assertEqual(result.tags_after, 0)
        self.assertNotIn("[VERIFY", result.content)

    async def test_scrub_call_is_routed_with_its_registered_node_name(self) -> None:
        """Regression: scrub_optional_verify_tags used to call llm.chat_json
        without node_name, so the router (llm_routing.classify_node) fell back
        to "unknown", defaulted to the quality tier, and logged a routing
        warning on every call — even though "verify_optional_scrub" is
        already registered in llm_routing.py."""
        fake = {
            "content": "Rewritten section body long enough to pass the guard.",
            "keptRequiredCount": 0,
            "note": "note",
        }
        mock_chat_json = AsyncMock(return_value=(fake, "test"))
        with (
            patch(
                "app.services.proposal_verify_optional_scrub.llm.is_configured",
                return_value=True,
            ),
            patch(
                "app.services.proposal_verify_optional_scrub.llm.chat_json",
                new=mock_chat_json,
            ),
            patch(
                "app.services.kb_rag_retrieve.retrieve_for_question",
                new=AsyncMock(return_value=("", [], [])),
            ),
        ):
            await scrub_optional_verify_tags(
                "Body with [VERIFY: subcontractor name] to scrub.",
                section_title="Scope",
                rfp_text="RFP text.",
            )
        self.assertEqual(
            mock_chat_json.call_args.kwargs.get("node_name"),
            "verify_optional_scrub",
        )


if __name__ == "__main__":
    unittest.main()
