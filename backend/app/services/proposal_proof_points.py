"""Proof-point matcher: RFP requirements → verified case studies."""

from __future__ import annotations

import logging
import re
from typing import Any

from app.models.proposal import EvidenceItem, ProofPoint, RfpSectionMap
from app.models.rfp import RfpRecord
from app.services import llm
from app.services.llm import LlmError
from app.services.proposal_knowledge_base_tools import search_knowledge_base
from app.services.proposal_langchain import _provider_name

logger = logging.getLogger(__name__)

_CASE_SOURCE_RE = re.compile(
    r"03[_\s-]?cs|case[\s_-]?stud|won[\s_-]?proposal|client[\s_-]?story",
    re.I,
)

PROOF_POINT_PROMPT = """Match zö agency case studies to RFP requirements for a winning proposal.

Use ONLY case study excerpts provided. Each match must cite a real 03_CS_ or won-proposal client.

For each requirement, return the best proof point with a first-person narrative hook (we/our — never "The Vendor").

Return ONLY JSON:
{
  "proofPoints": [
    {
      "requirement": "exact requirement text",
      "caseStudy": "Client / project name",
      "kbSource": "filename or source label from excerpts",
      "narrativeHook": "We built... / We led... (1-2 sentences, outcome-focused)",
      "relevance": "high|medium|low",
      "sectionIds": ["rfp-sec-5"],
      "evaluationWeight": 40
    }
  ]
}

Max 12 proof points. Prioritize highest evaluationWeight sections first."""


async def _fetch_case_study_excerpts(
    *,
    rfp: RfpRecord,
    evidence_corpus: list[EvidenceItem] | None = None,
) -> tuple[str, list[str]]:
    queries = [
        f"zö agency case studies {rfp.sector} {rfp.client} public sector marketing campaign outcomes",
        f"zö agency 03_CS {rfp.sector} campaign results outcomes",
        f"zö agency won proposal {rfp.sector} case study",
    ]

    for query in queries:
        for category in ("case_studies", None):
            kwargs: dict[str, Any] = {"limit": 12, "max_chars": 14_000}
            if category:
                case_text, case_sources = await search_knowledge_base(
                    query, category=category, **kwargs
                )
            else:
                case_text, case_sources = await search_knowledge_base(query, **kwargs)

            if case_text.startswith("(") and "not configured" in case_text.lower():
                continue
            if len(case_text.strip()) > 200:
                return case_text, case_sources

    if evidence_corpus:
        cs_items = [
            e
            for e in evidence_corpus
            if _CASE_SOURCE_RE.search(e.source or "") or _CASE_SOURCE_RE.search(e.excerpt[:400])
        ]
        if not cs_items:
            cs_items = [
                e
                for e in evidence_corpus
                if "03" in (e.source or "") and len(e.excerpt or "") > 80
            ][:12]
        if cs_items:
            text = "\n\n".join(
                f"{item.source}\n{item.excerpt[:2000]}" for item in cs_items[:12]
            )
            sources = list(dict.fromkeys(item.source for item in cs_items[:10]))
            logger.info(
                "Proof points using %d case-study items from evidence corpus for %s",
                len(cs_items),
                rfp.id,
            )
            return text, sources

    return "", []


async def build_proof_points_for_rfp(
    *,
    rfp: RfpRecord,
    rfp_context: str,
    rfp_sections: list[RfpSectionMap],
    evidence_corpus: list[EvidenceItem] | None = None,
) -> list[ProofPoint]:
    if not llm.is_configured():
        logger.warning("Proof points skipped — LLM not configured")
        return []

    case_text, case_sources = await _fetch_case_study_excerpts(
        rfp=rfp,
        evidence_corpus=evidence_corpus,
    )
    if not case_text.strip():
        logger.warning("Proof points skipped for %s — no case study excerpts in KB", rfp.id)
        return []

    requirements_payload: list[dict[str, Any]] = []
    for section in rfp_sections:
        weight = section.evaluation_weight
        for req in (section.requirements or [])[:4]:
            requirements_payload.append(
                {
                    "sectionId": section.id,
                    "sectionTitle": section.title,
                    "evaluationWeight": weight,
                    "requirement": req,
                }
            )

    if not requirements_payload:
        for section in sorted(
            rfp_sections,
            key=lambda s: -(s.evaluation_weight or 0),
        )[:6]:
            requirements_payload.append(
                {
                    "sectionId": section.id,
                    "sectionTitle": section.title,
                    "evaluationWeight": section.evaluation_weight,
                    "requirement": section.title,
                }
            )

    requirements_payload = requirements_payload[:24]

    try:
        raw, _provider = await llm.chat_json(
            [
                {"role": "system", "content": PROOF_POINT_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"Client: {rfp.client}\n"
                        f"Sector: {rfp.sector}\n"
                        f"RFP: {rfp.title}\n\n"
                        f"Requirements to match:\n{requirements_payload}\n\n"
                        f"Case study KB excerpts:\n{case_text[:12_000]}\n\n"
                        f"Sources: {', '.join(case_sources[:8])}\n\n"
                        f"RFP excerpt:\n{rfp_context[:6000]}"
                    ),
                },
            ],
            max_tokens=4096,
            temperature=0.25,
        )
    except LlmError as exc:
        logger.warning("Proof point matching failed: %s", exc)
        return []

    items = raw.get("proofPoints") or raw.get("proof_points") or []
    if not isinstance(items, list):
        return []

    proof_points: list[ProofPoint] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            proof_points.append(ProofPoint.model_validate(item))
        except Exception:
            logger.debug("Skipped invalid proof point", exc_info=True)

    logger.info(
        "Proof points for %s: %d matches (provider=%s)",
        rfp.id,
        len(proof_points),
        _provider_name(),
    )
    return proof_points
