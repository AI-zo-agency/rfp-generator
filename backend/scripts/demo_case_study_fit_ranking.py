"""Demonstrate the real `assess_case_study_fit` ranking path on the exact
scenario from human QA of a generated proposal:

    "Case study fit is weak for a digital-only advertising RFP. Deschutes
    Brewery is a brand-messaging case study with no digital advertising
    component shown at all. Only the Oregon Employment case study is a
    genuine digital ad/geofencing match. For an RFP requiring 'examples of
    similar work' in digital advertising specifically, two of three case
    studies don't actually demonstrate that capability."

This calls the production `assess_case_study_fit` function directly (not a
re-implementation of the scoring logic) with the LLM query-planner call and
the Supermemory fetch stubbed — no live network calls — and prints the
ranked candidates + gap report so the fix can be eyeballed end to end, not
just asserted in a unit test.

Run: .venv/bin/python scripts/demo_case_study_fit_ranking.py
"""

from __future__ import annotations

import asyncio
import json
from unittest import mock

from app.services import proposal_case_study_fit as fit

DIGITAL_AD_CAPABILITY = (
    "digital advertising campaigns including geofencing and targeted digital ads"
)

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

PLANNER_RESPONSE = {
    "capabilities": [
        {
            "capability": DIGITAL_AD_CAPABILITY,
            "queries": [
                "zö agency 03_CS geofencing digital advertising campaign case study",
                "zö agency 03_CS programmatic display digital ad case study",
            ],
            "matchKeywords": [
                "geofencing",
                "digital ad",
                "programmatic",
                "display ads",
                "paid media",
                "ppc",
            ],
        }
    ]
}

# A second capability with no genuine KB match, to show the gap path too.
NO_MATCH_CAPABILITY = "broadcast television commercial production"
PLANNER_RESPONSE["capabilities"].append(
    {
        "capability": NO_MATCH_CAPABILITY,
        "queries": ["zö agency 03_CS broadcast television commercial case study"],
        "matchKeywords": ["broadcast", "television commercial", "tv spot", "30 second spot"],
    }
)


async def main() -> None:
    with mock.patch.object(
        fit, "chat_json", new=mock.AsyncMock(return_value=(PLANNER_RESPONSE, "openrouter"))
    ), mock.patch.object(fit.supermemory, "is_configured", return_value=True), mock.patch.object(
        fit.supermemory, "search_documents", new=mock.AsyncMock(return_value=THREE_CANDIDATE_HITS)
    ):
        report = await fit.assess_case_study_fit(
            [DIGITAL_AD_CAPABILITY, NO_MATCH_CAPABILITY],
            rfp_client="Metro Transit Authority",
            rfp_sector="government",
            rfp_title="Digital Advertising Services RFP",
        )

    print(f"provider={report.provider} llm_call_count={report.llm_call_count}\n")
    for result in report.results:
        print(f"CAPABILITY: {result.capability}")
        print(f"  gap={result.gap}")
        if result.gap_reason:
            print(f"  gap_reason: {result.gap_reason}")
        print(f"  queries_used: {result.queries_used}")
        for rank, candidate in enumerate(result.candidates, start=1):
            print(
                f"  #{rank} [{candidate.fit_label:11s} score={candidate.fit_score:.3f}] "
                f"{candidate.source}"
            )
            print(f"       matched_terms={candidate.matched_terms}")
        print()

    print("--- raw JSON ---")
    print(report.model_dump_json(indent=2))


if __name__ == "__main__":
    asyncio.run(main())
