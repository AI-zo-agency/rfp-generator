"""AI money intelligence — Pass A currency triage + Pass B budget integrity.

Pass 0 (deterministic RFP money constraints) lives in
`evidence_trust.rfp_money_constraints` and is applied during budget reconcile /
grounding. This module handles residual `$` noise and narrative integrity.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.models.proposal import (
    PreSubmitIssue,
    ProposalBudget,
    ProposalDraft,
    ProposalSection,
)
from app.services import llm
from app.services.llm import LlmError
from app.services.proposal_budget_content import find_budget_section_index
from app.services.proposal_budget_validation import _USD_IN_TEXT_RE
from app.services.proposal_consistency import (
    _is_non_bid_currency_context,
    allowed_budget_amounts,
)

logger = logging.getLogger(__name__)

_PASS_A_PROMPT = """You triage dollar amounts found outside the canonical budget table.
For each candidate, decide if it is a bid/fee claim that must match the ledger,
or non-bid context (tuition, RFP allocation restatement, sample reallocation,
insurance limit, marketing copy).

Return JSON only:
{
  "judgments": [
    {
      "sectionId": "...",
      "amount": "$1,234",
      "kind": "rfp_cap|marketing_copy|insurance|tuition|bid_fee|sample_reallocation|other_non_bid",
      "isBidClaim": false,
      "rationale": "short"
    }
  ]
}
Prefer bid_fee when the amount is within ~5% of a line item or agency fee and the
excerpt asserts our proposed price. Prefer non-bid when the excerpt restates an
RFP allocation/ceiling that matches rfpBudgetCap / rfpMediaOrProgramEnvelope.
"""


def collect_currency_candidates(
    draft: ProposalDraft,
    budget: ProposalBudget,
    *,
    max_candidates: int = 40,
) -> list[dict[str, Any]]:
    """Deterministic `$` candidates not already in the allowed ledger set."""
    allowed = allowed_budget_amounts(budget)
    budget_idx = find_budget_section_index(draft.sections)
    out: list[dict[str, Any]] = []
    for index, section in enumerate(draft.sections):
        if budget_idx is not None and index == budget_idx:
            continue
        body = section.content or ""
        if not body.strip():
            continue
        for match in _USD_IN_TEXT_RE.finditer(body):
            raw = match.group(0)
            try:
                amount = float(raw.replace("$", "").replace(",", ""))
            except ValueError:
                continue
            if amount <= 0:
                continue
            if any(abs(amount - a) <= max(1.0, a * 0.02) for a in allowed):
                continue
            if _is_non_bid_currency_context(body, match.start(), match.end()):
                continue
            start = max(0, match.start() - 80)
            end = min(len(body), match.end() + 80)
            out.append(
                {
                    "sectionId": section.id,
                    "sectionTitle": section.title or "",
                    "amount": raw,
                    "amountValue": round(amount, 2),
                    "excerpt": re.sub(r"\s+", " ", body[start:end]).strip(),
                }
            )
    out.sort(key=lambda r: float(r["amountValue"]), reverse=True)
    return out[:max_candidates]


async def run_currency_triage_pass_a(
    *,
    draft: ProposalDraft,
    budget: ProposalBudget,
) -> list[PreSubmitIssue]:
    """Pass A: AI triage of `$` candidates. Fail open (no regex free_currency) on LLM error."""
    candidates = collect_currency_candidates(draft, budget)
    if not candidates:
        logger.info("money_intelligence Pass A skipped — zero candidates")
        return []

    from app.services.proposal_budget_sync import _canonical_budget_facts

    canonical = _canonical_budget_facts(budget)
    try:
        raw, _provider = await llm.chat_json(
            [
                {"role": "system", "content": _PASS_A_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"=== CANONICAL BUDGET ===\n{canonical}\n\n"
                        f"=== CANDIDATES ===\n{candidates}"
                    ),
                },
            ],
            max_tokens=4096,
            temperature=0.0,
            tier="light",
            node_name="money_intelligence_pass_a",
        )
    except (LlmError, TypeError, Exception) as exc:
        logger.warning(
            "money_intelligence Pass A failed open (no free_currency criticals): %s",
            exc,
        )
        return []

    issues: list[PreSubmitIssue] = []
    by_id = {s.id: s for s in draft.sections}
    for row in raw.get("judgments") or []:
        if not isinstance(row, dict):
            continue
        if not row.get("isBidClaim"):
            continue
        sid = str(row.get("sectionId") or "")
        amount = str(row.get("amount") or "")
        section = by_id.get(sid)
        issues.append(
            PreSubmitIssue(
                severity="critical",
                category="consistency",
                message=(
                    f"[T5:free_currency] Bid/fee claim {amount} does not match "
                    f"canonical budget (AI triage: {row.get('kind') or 'bid_fee'})"
                ),
                sectionId=sid or None,
                sectionTitle=(section.title if section else None),
                excerpt=str(row.get("rationale") or amount)[:160],
            )
        )
    logger.info(
        "money_intelligence Pass A candidates=%s bid_claims=%s",
        len(candidates),
        len(issues),
    )
    return issues


_PASS_B_PROMPT = """You review ONLY the pricing/budget narrative for label/math integrity.
Canonical totals are provided — do not invent a new budget.

Report contradictions where:
- Labels say fees / pass-through / total but the dollars are swapped or identical incorrectly
- Option Terms assign the same dollar to pass-through, fees, and total
- Phase labels conflict with the short phase list (if provided)

Return JSON only:
{
  "findings": [
    {
      "sectionId": "...",
      "severity": "critical|warning",
      "code": "t5.budget_integrity",
      "message": "short",
      "excerpt": "short"
    }
  ]
}
"""


async def run_budget_integrity_pass_b(
    *,
    draft: ProposalDraft,
    budget: ProposalBudget,
) -> list[PreSubmitIssue]:
    """Pass B: bounded AI review of pricing/budget narrative integrity."""
    budget_idx = find_budget_section_index(draft.sections)
    sections: list[ProposalSection] = []
    for index, section in enumerate(draft.sections):
        title = (section.title or "").casefold()
        if budget_idx is not None and index == budget_idx:
            sections.append(section)
            continue
        if any(
            tok in title
            for tok in ("pricing", "cost", "fee", "budget", "investment", "quotation")
        ):
            sections.append(section)
    if not sections:
        return []

    from app.services.proposal_budget_sync import _canonical_budget_facts

    payload = [
        {
            "sectionId": s.id,
            "title": s.title,
            "content": (s.content or "")[:8000],
        }
        for s in sections[:6]
    ]
    messages = [
        {"role": "system", "content": _PASS_B_PROMPT},
        {
            "role": "user",
            "content": (
                f"=== CANONICAL ===\n{_canonical_budget_facts(budget)}\n\n"
                f"=== SECTIONS ===\n{payload}"
            ),
        },
    ]
    try:
        raw, _provider = await llm.chat_json(
            messages,
            max_tokens=4096,
            temperature=0.0,
            tier="light",
            node_name="money_intelligence_pass_b",
        )
    except (LlmError, TypeError, Exception) as exc:
        msg = str(exc).casefold()
        if "invalid json" not in msg and "truncated" not in msg:
            logger.warning("money_intelligence Pass B failed: %s", exc)
            return []
        # _PASS_B_PROMPT's findings array has no length cap — a proposal with
        # several pricing sections can produce more findings than 4096 tokens
        # holds, cutting the JSON off mid-object. One retry at double the
        # budget recovers those instead of silently dropping every Pass B
        # finding for the run (this is the failure the "Conflicting fee
        # totals..." cutoff in the logs was).
        logger.warning(
            "money_intelligence Pass B truncated at 4096 tokens — retrying at 8192: %s",
            exc,
        )
        try:
            raw, _provider = await llm.chat_json(
                messages,
                max_tokens=8192,
                temperature=0.0,
                tier="light",
                node_name="money_intelligence_pass_b",
            )
        except (LlmError, TypeError, Exception) as retry_exc:
            logger.warning("money_intelligence Pass B retry failed: %s", retry_exc)
            return []

    issues: list[PreSubmitIssue] = []
    for row in raw.get("findings") or []:
        if not isinstance(row, dict):
            continue
        sev = str(row.get("severity") or "warning").casefold()
        if sev not in {"critical", "warning"}:
            sev = "warning"
        issues.append(
            PreSubmitIssue(
                severity=sev,  # type: ignore[arg-type]
                category="consistency",
                message=str(row.get("message") or "Budget integrity finding")[:500],
                sectionId=str(row.get("sectionId") or "") or None,
                excerpt=str(row.get("excerpt") or "")[:160],
            )
        )
    return issues


async def run_money_intelligence(
    *,
    draft: ProposalDraft,
    budget: ProposalBudget | None,
) -> list[PreSubmitIssue]:
    """Run Pass A (+ Pass B when budget exists)."""
    if budget is None:
        return []
    issues = await run_currency_triage_pass_a(draft=draft, budget=budget)
    issues.extend(await run_budget_integrity_pass_b(draft=draft, budget=budget))
    return issues
