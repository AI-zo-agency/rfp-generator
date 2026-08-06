"""proposal_case_study_fit: rank zö case studies against what the RFP asks
the vendor to DO, instead of accepting any case study as "similar work".

Written first against HEAD 180b083 (they fail: the module does not exist yet).

Real defect reproduced by `test_digital_ad_candidate_ranks_above_weak_fits`:
a digital-advertising-only RFP got Deschutes Brewery (pure brand messaging,
no digital component) and a website redesign cited alongside the one genuine
match (an Oregon Employment Department geofencing/digital-ad case study) —
all three presented with equal confidence. The fix must rank the genuine
match first and flag the other two as weak fits rather than silently
including them as if they were proof of the same capability.

No live network calls: `chat_json` and `supermemory.search_documents` /
`supermemory.is_configured` are stubbed throughout.
"""

from __future__ import annotations

import unittest
from unittest import mock

from app.services import proposal_case_study_fit as fit
from app.services.llm import LlmError

DIGITAL_AD_CAPABILITY = "digital advertising campaigns including geofencing and targeted digital ads"

OREGON_HIT = {
    "id": "doc-oregon",
    "customId": "03_CS_Oregon_Employment_Department_Geofencing.pdf",
    "content": (
        "Oregon Employment Department digital advertising campaigns used geofencing "
        "and programmatic display ads, running targeted digital ads across paid media "
        "channels to reach job seekers statewide."
    ),
    "metadata": {"fileName": "03_CS_Oregon_Employment_Department_Geofencing.pdf"},
}

DESCHUTES_HIT = {
    "id": "doc-deschutes",
    "customId": "03_CS_Deschutes_Brewery_Brand_Messaging.pdf",
    "content": (
        "Deschutes Brewery brand messaging refresh: new tone of voice, positioning "
        "statement, creative workshops, and an internal culture book establishing "
        "consumer message architecture and brand pillars."
    ),
    "metadata": {"fileName": "03_CS_Deschutes_Brewery_Brand_Messaging.pdf"},
}

WEBSITE_HIT = {
    "id": "doc-website",
    "customId": "03_CS_City_Website_Redesign.pdf",
    "content": (
        "Website redesign and UX overhaul for a public agency: new CMS platform, "
        "sitemap restructuring, and accessibility upgrades for mobile and desktop visitors."
    ),
    "metadata": {"fileName": "03_CS_City_Website_Redesign.pdf"},
}

THREE_CANDIDATE_HITS = [DESCHUTES_HIT, OREGON_HIT, WEBSITE_HIT]


def _planner_response(capability: str, queries: list[str], keywords: list[str]) -> dict:
    return {
        "capabilities": [
            {"capability": capability, "queries": queries, "matchKeywords": keywords}
        ]
    }


def _digital_ad_plan() -> dict:
    return _planner_response(
        DIGITAL_AD_CAPABILITY,
        queries=[
            "zö agency 03_CS geofencing digital advertising campaign case study",
            "zö agency 03_CS programmatic display digital ad case study",
        ],
        keywords=["geofencing", "digital ad", "programmatic", "display ads", "paid media", "ppc"],
    )


class DigitalAdRankingTests(unittest.IsolatedAsyncioTestCase):
    """The observed defect: two of three cited case studies didn't demonstrate
    the required capability, and nothing said so."""

    async def test_digital_ad_candidate_ranks_above_weak_fits(self) -> None:
        with mock.patch.object(
            fit, "chat_json", new=mock.AsyncMock(return_value=(_digital_ad_plan(), "openrouter"))
        ), mock.patch.object(
            fit.supermemory, "is_configured", return_value=True
        ), mock.patch.object(
            fit.supermemory,
            "search_documents",
            new=mock.AsyncMock(return_value=THREE_CANDIDATE_HITS),
        ):
            report = await fit.assess_case_study_fit(
                [DIGITAL_AD_CAPABILITY],
                rfp_client="Metro Transit Authority",
                rfp_sector="government",
                rfp_title="Digital Advertising Services RFP",
            )

        result = report.result_for(DIGITAL_AD_CAPABILITY)
        self.assertIsNotNone(result)
        self.assertFalse(result.gap, "a genuine digital-ad case study exists; this is not a gap")
        self.assertGreaterEqual(len(result.candidates), 2)

        top = result.candidates[0]
        self.assertIn("Oregon", top.source)
        self.assertEqual(top.fit_label, "strong_fit")

        deschutes = next(c for c in result.candidates if "Deschutes" in c.source)
        self.assertEqual(
            deschutes.fit_label,
            "weak_fit",
            "brand-messaging case study has no digital-ad component and must not read as a fit",
        )
        self.assertLess(deschutes.fit_score, top.fit_score)


class NoCandidateDemonstratesCapabilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_gap_reported_when_nothing_matches(self) -> None:
        capability = "broadcast television commercial production"
        plan = _planner_response(
            capability,
            queries=["zö agency 03_CS broadcast television commercial case study"],
            keywords=["broadcast", "television commercial", "tv spot", "30 second spot"],
        )
        with mock.patch.object(
            fit, "chat_json", new=mock.AsyncMock(return_value=(plan, "openrouter"))
        ), mock.patch.object(
            fit.supermemory, "is_configured", return_value=True
        ), mock.patch.object(
            fit.supermemory,
            "search_documents",
            new=mock.AsyncMock(return_value=THREE_CANDIDATE_HITS),
        ):
            report = await fit.assess_case_study_fit([capability])

        result = report.result_for(capability)
        self.assertIsNotNone(result)
        self.assertTrue(result.gap)
        self.assertTrue(result.gap_reason)
        self.assertFalse(
            any(c.fit_label == "strong_fit" for c in result.candidates),
            "no candidate may be presented as a fit when none demonstrates the capability",
        )


class QueryBuyerNameTests(unittest.IsolatedAsyncioTestCase):
    async def test_generated_queries_never_contain_buyer_name(self) -> None:
        client = "Metro Transit Authority"
        # A misbehaving planner call that violates its own instructions —
        # the cleanup step must catch this, not merely hope the prompt works.
        plan = _planner_response(
            DIGITAL_AD_CAPABILITY,
            queries=[
                f"zö agency 03_CS digital advertising case study for {client}",
                "zö agency 03_CS geofencing digital ad case study",
            ],
            keywords=["geofencing", "digital ad"],
        )
        with mock.patch.object(
            fit, "chat_json", new=mock.AsyncMock(return_value=(plan, "openrouter"))
        ), mock.patch.object(
            fit.supermemory, "is_configured", return_value=True
        ), mock.patch.object(
            fit.supermemory,
            "search_documents",
            new=mock.AsyncMock(return_value=THREE_CANDIDATE_HITS),
        ):
            report = await fit.assess_case_study_fit(
                [DIGITAL_AD_CAPABILITY], rfp_client=client, rfp_title="Digital Ad RFP"
            )

        result = report.result_for(DIGITAL_AD_CAPABILITY)
        self.assertIsNotNone(result)
        for query in result.queries_used:
            self.assertNotIn(client.casefold(), query.casefold())


class GracefulDegradationTests(unittest.IsolatedAsyncioTestCase):
    async def test_llm_failure_returns_gap_without_raising(self) -> None:
        search = mock.AsyncMock(return_value=THREE_CANDIDATE_HITS)
        with mock.patch.object(
            fit, "chat_json", new=mock.AsyncMock(side_effect=LlmError("all providers failed"))
        ), mock.patch.object(
            fit.supermemory, "is_configured", return_value=True
        ), mock.patch.object(fit.supermemory, "search_documents", new=search):
            report = await fit.assess_case_study_fit([DIGITAL_AD_CAPABILITY])

        result = report.result_for(DIGITAL_AD_CAPABILITY)
        self.assertIsNotNone(result)
        self.assertTrue(result.gap)
        self.assertEqual(result.candidates, [])
        search.assert_not_awaited()

    async def test_supermemory_failure_returns_gap_without_raising(self) -> None:
        with mock.patch.object(
            fit, "chat_json", new=mock.AsyncMock(return_value=(_digital_ad_plan(), "openrouter"))
        ), mock.patch.object(
            fit.supermemory, "is_configured", return_value=True
        ), mock.patch.object(
            fit.supermemory,
            "search_documents",
            new=mock.AsyncMock(side_effect=fit.supermemory.SupermemoryError("down")),
        ):
            report = await fit.assess_case_study_fit([DIGITAL_AD_CAPABILITY])

        result = report.result_for(DIGITAL_AD_CAPABILITY)
        self.assertIsNotNone(result)
        self.assertTrue(result.gap)
        self.assertEqual(result.candidates, [])

    async def test_supermemory_not_configured_returns_gap_without_raising(self) -> None:
        with mock.patch.object(
            fit, "chat_json", new=mock.AsyncMock(return_value=(_digital_ad_plan(), "openrouter"))
        ), mock.patch.object(fit.supermemory, "is_configured", return_value=False):
            report = await fit.assess_case_study_fit([DIGITAL_AD_CAPABILITY])

        result = report.result_for(DIGITAL_AD_CAPABILITY)
        self.assertIsNotNone(result)
        self.assertTrue(result.gap)


class SingleLlmCallBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_exactly_one_llm_call_for_several_capabilities(self) -> None:
        capabilities = [
            DIGITAL_AD_CAPABILITY,
            "brand messaging and identity development",
            "website redesign and CMS migration",
        ]
        plan = {
            "capabilities": [
                {"capability": c, "queries": [f"zö agency 03_CS {c}"], "matchKeywords": [c.split()[0]]}
                for c in capabilities
            ]
        }
        chat = mock.AsyncMock(return_value=(plan, "openrouter"))
        with mock.patch.object(fit, "chat_json", new=chat), mock.patch.object(
            fit.supermemory, "is_configured", return_value=True
        ), mock.patch.object(
            fit.supermemory,
            "search_documents",
            new=mock.AsyncMock(return_value=THREE_CANDIDATE_HITS),
        ):
            report = await fit.assess_case_study_fit(
                capabilities, rfp_client="Metro Transit Authority"
            )

        chat.assert_awaited_once()
        self.assertEqual(report.llm_call_count, 1)
        self.assertEqual(len(report.results), len(capabilities))


if __name__ == "__main__":
    unittest.main()
