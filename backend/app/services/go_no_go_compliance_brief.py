"""One light LLM call for Compliance Snapshot + evaluation Our Position rows."""

from __future__ import annotations

import logging
from typing import Any

from app.models.go_no_go import GoNoGoCapabilityRow

logger = logging.getLogger(__name__)

_COMPLIANCE_BRIEF_PROMPT = """You write the analyst brief sections for a zö agency Go/No-Go review.

Return JSON only:
{
  "complianceSnapshot": [
    "Short paragraph on submission format (portal vs PDF, character limits per field, etc.).",
    "Short paragraph on geographic restrictions / resident preference (bonus vs disqualifier).",
    "Short paragraph on registration / licensing (pre-bid gate vs post-award).",
    "Short paragraph on required exhibits / boilerplate forms.",
    "Short paragraph on exceptions to standard terms if stated."
  ],
  "evaluationPositions": [
    {"section": "I. Background & Qualifications", "points": 200, "position": "Strong — ..."}
  ],
  "capabilitySummary": "1-3 sentences on overall capability match referencing strongest KB proof.",
  "evaluationSummary": "1-2 sentences on which sections are strong vs blocked (include point totals when known)."
}

Rules:
- Judge by meaning from the RFP excerpt — no keyword shortcuts.
- complianceSnapshot: 3-6 tight paragraphs, not bullet dumps. Flag portal/4000-char fields when present.
- evaluationPositions: one row per evaluation section listed in HARD FACTS. Match section names closely.
  position starts with Strong / Moderate / Weak / Blocked — then one clause why.
- Use capability matrix statuses: verified=Strong, partial=Needs honest framing, gap/blocked=Blocked or Weak.
- Do NOT invent evaluation point totals — use ONLY sections/points provided in HARD FACTS.
- If evaluation points not disclosed, evaluationPositions may be empty and evaluationSummary says weights undisclosed.
"""


async def fetch_compliance_brief(
    *,
    rfp_id: str,
    rfp_title: str,
    rfp_excerpt: str,
    hard_facts: dict[str, Any],
    capability_rows: list[GoNoGoCapabilityRow],
) -> dict[str, Any]:
    from app.services import llm

    if not llm.is_configured():
        return {}

    eval_lines = hard_facts.get("evaluation_lines") or []
    eval_block = "\n".join(f"- {line}" for line in eval_lines[:16]) or "(none extracted)"

    cap_lines = []
    for row in capability_rows[:20]:
        cap_lines.append(
            f"- {row.requirement}: {row.status}"
            + (f" — {row.evidence or row.kb_source}"[:120] if row.evidence or row.kb_source else "")
        )
    cap_block = "\n".join(cap_lines) or "(none)"

    messages = [
        {"role": "system", "content": _COMPLIANCE_BRIEF_PROMPT},
        {
            "role": "user",
            "content": (
                f"RFP: {rfp_title}\n\n"
                f"## HARD FACTS — evaluation sections\n{eval_block}\n\n"
                f"## Capability matrix (adjudicated)\n{cap_block}\n\n"
                f"## RFP excerpt\n{(rfp_excerpt or '')[:20_000]}"
            ),
        },
    ]
    try:
        raw, provider = await llm.chat_json(
            messages,
            max_tokens=1800,
            temperature=0.0,
            tier="light",
            node_name="go_no_go_compliance_brief",
            rfp_id=rfp_id,
        )
        if not isinstance(raw, dict):
            return {}
        logger.info("go_no_go compliance brief for %s via %s", rfp_id, provider)
        return raw
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "go_no_go compliance brief failed for %s: %s",
            rfp_id,
            str(exc)[:160],
        )
        return {}
