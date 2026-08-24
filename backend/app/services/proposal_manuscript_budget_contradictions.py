"""LLM pass: cross-section budget / hours / fee reconciliation.

Catches double-billed coordination lines, hours-table vs fee mismatch, and
phase-table vs rollup contradictions across Budget/Pricing and staffing tabs.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from app.models.proposal import ProposalDraft, ProposalResearchCache, ProposalSection
from app.models.rfp import RfpRecord
from app.services import llm
from app.services.proposal_budget_content import find_budget_section_index
from app.services.proposal_scan_rfp_contradictions import (
    STATIC_COMPANY_FACT_SECTION_IDS,
    _manuscript_digest,
)

logger = logging.getLogger(__name__)

_SYSTEM = """You are a proposal budget cross-section consistency editor for zö agency.

TASK: Read the FULL manuscript (especially Budget/Pricing, fee tables, phase
rollups, Monthly Capacity / hours / staffing tables) and find cross-section
contradictions or double-billing — NOT internal arithmetic that already sums.

IS a contradiction (flag these):
- DOUBLE-BILLED COORDINATION: two fee line items (e.g. "Planning & Account
  Management" AND "Project Management") with overlapping scope descriptions
  (both claim planning meetings, status reporting, workflow/capacity tracking).
  Flag as one merged scope or one line must be removed/clarified.
- HOURS vs FEE MISMATCH: a Monthly Capacity / hours allocation table totals
  N hours/month (or N×12/year) while the Budget section shows $X annual agency
  fee — compute blended $/hour and flag if implausibly low vs senior team pitched
  elsewhere OR if hours and fee were clearly generated independently.
- PHASE TABLE vs ROLLUP: phase line items do not sum to stated subtotals/total.
- CROSS-SECTION DOLLAR CLAIMS: one section states a total/fee that another
  section contradicts (different annual fee, different PM amount).

NOT a contradiction (do NOT flag):
- Intentionally aggressive Low-tier pricing when prose says so and math is consistent
- Client media pass-through separate from agency fee
- [VERIFY] / [PRICING FLAG] tags already marking Sonja review
- Duplicated Who We Are prose (Senior Editor dedupe handles)
- Company profile facts (team size, email — fact-contradiction pass handles)

fixAction rules:
- rewrite: safe deterministic fix (merge duplicate PM scope into one line,
  align hours table footnote with fee, fix rollup wording) — provide precise
  rewriteInstruction naming exact line items / cells / sections
- verify: add [PRICING FLAG: … — Sonja review required] when strategic pricing
  choice may be deliberate but a reviewer would notice the gap
- human: cannot auto-fix without Sonja (major scope/pricing strategy)

NEVER invent dollar amounts not supported by the manuscript or pricing guide excerpt.
When merging double-billed PM lines, prefer ONE $7,500 coordination line with
combined scope — do not add new totals.

Return ONLY JSON:
{
  "contradictions": [
    {
      "sectionId": "primary section to edit",
      "sectionTitle": "title",
      "relatedSectionId": "other section id if cross-section, else empty",
      "canonicalFact": "what the manuscript authoritatively sums to elsewhere",
      "manuscriptContradiction": "the cross-section gap in plain language",
      "severity": "critical|major|minor",
      "fixAction": "rewrite|verify|human",
      "rewriteInstruction": "imperative fix for rewriter; empty if verify/human"
    }
  ],
  "summary": "one sentence"
}"""


@dataclass
class BudgetContradictionFinding:
    section_id: str
    section_title: str
    related_section_id: str
    canonical_fact: str
    manuscript_contradiction: str
    severity: str
    fix_action: str
    rewrite_instruction: str = ""

    def banner_line(self) -> str:
        return (
            f"{self.section_title or self.section_id}: "
            f"{self.manuscript_contradiction[:160]}"
        )


@dataclass
class ManuscriptBudgetContradictionResult:
    draft: ProposalDraft
    findings: list[BudgetContradictionFinding] = field(default_factory=list)
    unresolved_findings: list[BudgetContradictionFinding] = field(default_factory=list)
    rewrites_applied: int = 0
    pricing_flags_added: int = 0
    logs: list[str] = field(default_factory=list)
    summary: str = ""


def _parse_findings(raw: dict[str, Any], draft: ProposalDraft) -> list[BudgetContradictionFinding]:
    known = {s.id for s in draft.sections}
    out: list[BudgetContradictionFinding] = []
    rows = raw.get("contradictions") if isinstance(raw, dict) else None
    if not isinstance(rows, list):
        return out
    for row in rows:
        if not isinstance(row, dict):
            continue
        sid = str(row.get("sectionId") or row.get("section_id") or "").strip()
        if sid and sid not in known:
            title = str(row.get("sectionTitle") or row.get("section_title") or "").strip()
            match = next(
                (s for s in draft.sections if (s.title or "").casefold() == title.casefold()),
                None,
            )
            sid = match.id if match else sid
        if not sid or sid not in known:
            continue
        canonical = str(
            row.get("canonicalFact")
            or row.get("canonical_fact")
            or row.get("verifiedFact")
            or ""
        ).strip()
        contra = str(
            row.get("manuscriptContradiction")
            or row.get("manuscript_contradiction")
            or ""
        ).strip()
        if not contra:
            continue
        severity = str(row.get("severity") or "major").strip().casefold()
        if severity not in {"critical", "major", "minor"}:
            severity = "major"
        action = str(row.get("fixAction") or row.get("fix_action") or "human").strip().casefold()
        if action not in {"rewrite", "verify", "human"}:
            action = "human"
        section = next(s for s in draft.sections if s.id == sid)
        out.append(
            BudgetContradictionFinding(
                section_id=sid,
                section_title=str(
                    row.get("sectionTitle") or row.get("section_title") or section.title or ""
                ),
                related_section_id=str(
                    row.get("relatedSectionId") or row.get("related_section_id") or ""
                ).strip(),
                canonical_fact=canonical[:400],
                manuscript_contradiction=contra[:400],
                severity=severity,
                fix_action=action,
                rewrite_instruction=str(
                    row.get("rewriteInstruction") or row.get("rewrite_instruction") or ""
                ).strip()[:900],
            )
        )
    return out


def _budget_canonical_block(research: ProposalResearchCache | None) -> str:
    if not research or not research.budget:
        return ""
    b = research.budget
    lines = [
        f"Agency fee subtotal: {b.agency_fee_subtotal}",
        f"Total client invoicing: {b.total_client_invoicing}",
        f"Lump sum total: {b.lump_sum_total}",
    ]
    for item in (b.line_items or [])[:24]:
        desc = (item.description or item.label or "").strip()
        ext = item.extended
        if desc and ext is not None:
            lines.append(f"- {desc}: ${ext:,.0f}")
    return "\n".join(lines)


def _is_budget_section(section: ProposalSection, draft: ProposalDraft) -> bool:
    idx = find_budget_section_index(draft.sections)
    if idx is None:
        return False
    return draft.sections[idx].id == section.id


def _append_pricing_flag(section: ProposalSection, finding: BudgetContradictionFinding) -> ProposalSection:
    note = (
        f"[PRICING FLAG: {finding.manuscript_contradiction[:200]} "
        f"— Sonja review required]"
    )
    body = section.content or ""
    if note[:50].casefold() in body.casefold():
        return section
    return section.model_copy(
        update={"content": f"{note}\n\n{body}".strip(), "status": "generated"}
    )


async def _rewrite_section_for_budget_contradiction(
    section: ProposalSection,
    *,
    finding: BudgetContradictionFinding,
    draft: ProposalDraft,
    rfp: RfpRecord,
    pricing_guide: str,
    canonical_budget: str,
) -> tuple[ProposalSection, bool, str]:
    if not llm.is_configured():
        return section, False, ""
    is_budget = _is_budget_section(section, draft)
    system = (
        "You fix ONE proposal section to resolve a cross-section budget contradiction.\n"
        "Preserve markdown tables and designer-ready layout.\n"
        "For Budget/Pricing: keep phase tables intact; merge duplicate PM/planning "
        "lines into ONE scoped line OR differentiate scopes clearly — never double-count.\n"
        "Do NOT invent dollar amounts. Use figures already in the draft or pricing guide.\n"
        "Return JSON: "
        '{"content": "full markdown", "changed": true/false, "notes": "one line"}'
    )
    related = ""
    if finding.related_section_id:
        rel = next((s for s in draft.sections if s.id == finding.related_section_id), None)
        if rel:
            related = f"\nRelated section ({rel.title}):\n{(rel.content or '')[:4000]}\n"
    user = (
        f"Client: {rfp.client}\nRFP: {rfp.title}\n"
        f"Section to edit: {section.title} (id={section.id})\n"
        f"Is canonical budget tab: {is_budget}\n\n"
        f"Canonical budget object (if any):\n{canonical_budget or '(none)'}\n\n"
        f"Pricing guide excerpt:\n{pricing_guide[:12_000]}\n\n"
        f"Cross-section fact:\n{finding.canonical_fact}\n\n"
        f"Contradiction:\n{finding.manuscript_contradiction}\n\n"
        f"Fix instruction:\n"
        f"{finding.rewrite_instruction or 'Resolve cross-section budget contradiction.'}\n"
        f"{related}\n"
        f"Current section:\n{(section.content or '')[:14_000]}"
    )
    try:
        raw, _ = await llm.chat_json(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            max_tokens=4096,
            temperature=0.0,
            node_name=f"manuscript_budget_contradiction_rewrite:{section.id}",
            rfp_id=rfp.id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Budget contradiction rewrite failed for %s: %s", section.id, exc)
        return section, False, ""
    if not isinstance(raw, dict):
        return section, False, ""
    content = str(raw.get("content") or "").strip()
    changed = bool(raw.get("changed")) and bool(content)
    if not changed or content == (section.content or "").strip():
        return section, False, str(raw.get("notes") or "")
    return (
        section.model_copy(update={"content": content, "status": "generated"}),
        True,
        str(raw.get("notes") or "rewrote for budget cross-section consistency"),
    )


async def run_manuscript_budget_contradiction_pass(
    draft: ProposalDraft,
    *,
    rfp: RfpRecord,
    research: ProposalResearchCache | None = None,
    use_llm: bool = True,
) -> ManuscriptBudgetContradictionResult:
    """LLM scan for budget/hours/fee cross-section contradictions."""
    result = ManuscriptBudgetContradictionResult(draft=draft)
    if not use_llm or not llm.is_configured():
        result.logs.append("Budget cross-section scan skipped (LLM unavailable).")
        return result

    digest = _manuscript_digest(draft, max_chars=36_000)
    if not digest.strip():
        result.logs.append("Budget cross-section scan skipped (empty manuscript).")
        return result

    if not re.search(
        r"(?is)\bbudget\b|\bpricing\b|\bfee\b|\$[\d,]+|\bhours?\b|\bcapacity\b",
        digest,
    ):
        result.logs.append("Budget cross-section scan skipped (no budget/hours content).")
        return result

    canonical_budget = _budget_canonical_block(research)
    pricing_guide = ""
    try:
        from app.services.proposal_pricing_service import fetch_pricing_guide_context

        pricing_guide, _ = await fetch_pricing_guide_context(rfp, focus_hint="PM floor coordination")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Pricing guide fetch for budget audit failed: %s", exc)

    user = (
        f"Client: {rfp.client}\nRFP title: {rfp.title}\n\n"
        f"CANONICAL BUDGET OBJECT (Stage 3 — authoritative line items if present):\n"
        f"{canonical_budget or '(not loaded — use manuscript fee tables)'}\n\n"
        f"PRICING GUIDE (00_Guide_Pricing — PM floors, tier rates):\n"
        f"{pricing_guide[:16_000] or '(unavailable)'}\n\n"
        f"FULL MANUSCRIPT (check Budget vs Capacity/Hours tabs together):\n{digest}"
    )
    try:
        raw, _ = await llm.chat_json(
            [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": user}],
            max_tokens=4096,
            temperature=0.0,
            node_name="manuscript_budget_contradiction_audit",
            rfp_id=rfp.id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Budget cross-section audit failed: %s", exc)
        result.logs.append(f"Budget cross-section audit failed: {exc}")
        return result

    if not isinstance(raw, dict):
        result.logs.append("Budget cross-section audit returned non-object JSON.")
        return result

    findings = _parse_findings(raw, draft)
    result.findings = findings
    result.summary = str(raw.get("summary") or "").strip()

    if not findings:
        result.logs.append("Budget cross-section scan: no contradictions found.")
        return result

    result.logs.append(
        f"Budget cross-section scan: {len(findings)} issue(s) "
        f"({sum(1 for f in findings if f.severity == 'critical')} critical)."
    )

    sections = list(draft.sections)
    by_id = {s.id: i for i, s in enumerate(sections)}
    fixed_ids: set[str] = set()

    for finding in findings:
        idx = by_id.get(finding.section_id)
        if idx is None:
            continue
        if finding.section_id in STATIC_COMPANY_FACT_SECTION_IDS:
            result.logs.append(
                f"{finding.section_id}: skipped budget-contradiction rewrite — "
                "protected static company-fact section (likely false positive)"
            )
            continue
        section = sections[idx]
        if finding.fix_action == "rewrite" and finding.severity in {"critical", "major"}:
            updated, changed, notes = await _rewrite_section_for_budget_contradiction(
                section,
                finding=finding,
                draft=draft,
                rfp=rfp,
                pricing_guide=pricing_guide,
                canonical_budget=canonical_budget,
            )
            if changed:
                sections[idx] = updated
                result.rewrites_applied += 1
                fixed_ids.add(finding.section_id)
                result.logs.append(
                    f"{finding.section_id}: FIXED budget cross-section by rewrite"
                    + (f" — {notes}" if notes else "")
                )
                continue
        if finding.fix_action in {"verify", "human"} or finding.severity != "minor":
            sections[idx] = _append_pricing_flag(sections[idx], finding)
            result.pricing_flags_added += 1
            result.logs.append(
                f"{finding.section_id}: PRICING FLAG — {finding.manuscript_contradiction[:120]}"
            )

    result.draft = draft.model_copy(update={"sections": sections})
    result.unresolved_findings = [
        f
        for f in findings
        if f.section_id not in fixed_ids and f.severity != "minor"
    ]
    return result
