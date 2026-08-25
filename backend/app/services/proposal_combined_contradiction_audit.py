"""One combined LLM detection call for all three contradiction dimensions.

Complete & Clean Draft used to run THREE separate full-manuscript audits back to
back (verified-fact, RFP-requirement, cross-section budget). Each re-sent the
whole manuscript digest, so the manuscript was shipped to the model three times.

This detector sends the manuscript ONCE and returns all three finding lists.
Each downstream pass (unchanged) then applies its own findings via
``precomputed_raw`` — so the per-finding rewrite logic and its tests are
untouched; only the detection call is consolidated.
"""

from __future__ import annotations

import logging
from typing import Any

from app.models.proposal import ProposalDraft, ProposalResearchCache
from app.models.rfp import RfpRecord
from app.services import llm
from app.services.proposal_scan_rfp_contradictions import _manuscript_digest

logger = logging.getLogger(__name__)

_COMBINED_SYSTEM = """You are a proposal QA editor for zö agency. In ONE pass over
the manuscript, find contradictions in THREE independent dimensions and return
each in its own array. Be precise; only flag REAL issues.

Also obey any "## STANDING CORRECTIONS" block: a correction (e.g. a person's new
title or a retirement) overrides the knowledge base AND the roster, and a named
person's title/role/status that conflicts with a correction is a factContradiction
(severity critical, fixAction rewrite to the correction's wording).

1) factContradictions — manuscript vs VERIFIED COMPANY FACTS + internal consistency:
   - Contact email / phone / website conflicting with companyfacts.
   - Team size / headcount that conflicts with companyfacts, or DIFFERENT team-size
     numbers in different sections.
   - Founded year, legal name, tenure, or agency certifications not in companyfacts.
   - Signed insurance/exception forms marked "Compliant" / "meets or exceeds" when
     Section 1.5 / companyfacts do not support that coverage type or dollar limit.
   - Invented past technical capability, state business registration, case-study
     name/URL, or case-study metrics with no evidence in the manuscript/KB.
   - Bio "Role on this engagement" that contradicts the org chart.
   Each: {sectionId, sectionTitle, verifiedFact, manuscriptContradiction, severity,
   fixAction, rewriteInstruction}. verifiedFact = what companyfacts/another section
   authoritatively says. Never invent replacement numbers.

2) rfpContradictions — manuscript vs the RFP's OWN requirements:
   - Schedule/timeline overrunning the RFP award→launch/contract window.
   - Budget/price claims above an RFP ceiling, or that rewrite a forbidden form.
   - Draft denies a requirement the RFP states, or violates eligibility/submission
     rules, named criteria, page limits, or mandatory deliverables.
   Each: {sectionId, sectionTitle, rfpRequirement, manuscriptContradiction, severity,
   fixAction, rewriteInstruction}. Also collect (top level):
   attachmentNeeds (physically signed PDFs the human must attach) and
   complianceReminders (deadlines / labelling rules) — these are NOT contradictions.

3) budgetContradictions — cross-section budget/hours/fee consistency:
   - Double-billed coordination (two fee lines with overlapping scope).
   - Hours-vs-fee mismatch, phase table not summing to the stated total, or one
     section stating a total another section contradicts.
   Each: {sectionId, sectionTitle, relatedSectionId, canonicalFact,
   manuscriptContradiction, severity, fixAction, rewriteInstruction}.
   canonicalFact = what the manuscript authoritatively sums to elsewhere.

NEVER invent dollar amounts, dates, signature IDs, notary numbers, or client facts.
severity ∈ critical|major|minor. fixAction ∈ rewrite|verify|human.

Return ONLY JSON:
{
  "factContradictions": [...],
  "rfpContradictions": [...],
  "budgetContradictions": [...],
  "attachmentNeeds": ["..."],
  "complianceReminders": ["..."],
  "summary": "one sentence"
}"""


async def detect_all_contradictions(
    draft: ProposalDraft,
    *,
    rfp: RfpRecord,
    rfp_text: str,
    research: ProposalResearchCache | None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]] | None:
    """One LLM call → (fact_raw, rfp_raw, budget_raw), each shaped for the
    matching pass's ``_parse_findings``. Returns None when detection could not
    run (caller then falls back to the three separate passes).
    """
    if not llm.is_configured():
        return None
    digest = _manuscript_digest(draft, max_chars=34_000)
    if not digest.strip():
        return None

    # Verified company facts (fact dimension) — cheap KB retrieval, not an LLM call.
    verified_corpus = ""
    try:
        from app.services.proposal_manuscript_fact_contradictions import (
            _fetch_verified_facts_corpus,
        )

        verified_corpus, _sources = await _fetch_verified_facts_corpus()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Combined audit: verified corpus fetch failed: %s", exc)

    user = (
        f"Client: {rfp.client}\nRFP title: {rfp.title}\n"
        f"Due date: {getattr(rfp, 'due_date', None) or 'unknown'}\n\n"
        f"VERIFIED COMPANY FACTS (01_companyfacts_verified — authoritative):\n"
        f"{verified_corpus or '(corpus unavailable — still flag cross-section conflicts)'}\n\n"
        f"RFP TEXT (authoritative for dimension 2):\n{(rfp_text or '')[:38_000]}\n\n"
        f"FULL MANUSCRIPT (check EVERY tab):\n{digest}"
    )
    try:
        raw, _ = await llm.chat_json(
            [
                {"role": "system", "content": _COMBINED_SYSTEM},
                {"role": "user", "content": user},
            ],
            max_tokens=6144,
            temperature=0.0,
            node_name="combined_contradiction_audit",
            rfp_id=rfp.id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Combined contradiction audit failed: %s", exc)
        return None
    if not isinstance(raw, dict):
        return None

    summary = str(raw.get("summary") or "").strip()

    def _list(key: str, *alts: str) -> list[Any]:
        for k in (key, *alts):
            val = raw.get(k)
            if isinstance(val, list):
                return val
        return []

    fact_raw: dict[str, Any] = {
        "contradictions": _list("factContradictions", "fact_contradictions"),
        "summary": summary,
    }
    rfp_raw: dict[str, Any] = {
        "contradictions": _list("rfpContradictions", "rfp_contradictions"),
        "attachmentNeeds": _list("attachmentNeeds", "attachment_needs"),
        "complianceReminders": _list("complianceReminders", "compliance_reminders"),
        "summary": summary,
    }
    budget_raw: dict[str, Any] = {
        "contradictions": _list("budgetContradictions", "budget_contradictions"),
        "summary": summary,
    }
    return fact_raw, rfp_raw, budget_raw
