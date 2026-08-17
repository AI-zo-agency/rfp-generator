"""Forms & Attachments integrity: LLM judges meaning, then verbatim quote replace.

No regex / synonym maps for carriers, hourly claims, or contact language.
Facts come from structured budget + manuscript sections. The model names the
false substring; we replace it only if that quote appears in the draft.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app.models.proposal import ProposalBudget, ProposalDraft, ProposalResearchCache, ProposalSection
from app.services import llm
from app.services.proposal_budget_content import find_budget_section_index

logger = logging.getLogger(__name__)

_SYSTEM = """You audit ONE Required Forms & Attachments section against FACTS.

Find every status-claim mismatch in this section (table rows AND insurance prose).
Judge by meaning — not keyword lists.

Flag when:
- The section names a specific insurance carrier, NAIC number, or policy issuer
  that is NOT stated in INSURANCE FACTS (Section 1.5 / companyfacts). Generic
  "A-rated carriers" without a named company is OK.
- A Cost / Pricing row claims hourly labor-category rates are provided, but
  BUDGET FACTS say the manuscript is fixed/phased with no bindable hourly table.
- A References row claims full/complete contact information is provided, but
  REFERENCE FACTS show blank or incomplete contacts (missing phone/email).

Do NOT invent new dollar amounts, carrier names, NAIC numbers, or contacts.
Replacements must be honest: MANUAL FILL / pending when the fact is unverified.

Return JSON only:
{
  "issues": [
    {
      "code": "insurance_carrier_unverified|cost_row_hourly_mismatch|references_row_contact_mismatch|other",
      "summary": "one sentence of what is false",
      "verbatimQuote": "exact substring copied from the SECTION (must match character-for-character)",
      "replacement": "text that replaces that substring; empty string deletes it",
      "fixAction": "replace|none"
    }
  ]
}

verbatimQuote MUST appear exactly in the section. If you cannot copy an exact
span, omit the issue. List ALL issues in one pass — do not stop after one.
"""


@dataclass
class FormsIntegrityFinding:
    code: str
    summary: str
    fixed: bool = False
    verbatim_quote: str = ""
    replacement: str = ""


@dataclass
class FormsIntegrityResult:
    content: str
    findings: list[FormsIntegrityFinding] = field(default_factory=list)
    fix_logs: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.fix_logs)


def section_is_forms_attachments(section: ProposalSection) -> bool:
    sid = (section.id or "").casefold()
    if sid in {"rfp-req-forms-attachments", "forms-attachments"}:
        return True
    title = (section.title or "").casefold()
    return "forms" in title and "attachment" in title


def apply_verbatim_replacements(
    content: str,
    findings: list[FormsIntegrityFinding],
) -> FormsIntegrityResult:
    """Mechanical apply: replace only quotes that appear verbatim in the draft."""
    updated = content or ""
    logs: list[str] = []
    for finding in findings:
        quote = (finding.verbatim_quote or "").strip()
        if not quote or quote not in updated:
            finding.fixed = False
            if quote:
                logger.info(
                    "forms_integrity quote not found code=%s quote=%r",
                    finding.code,
                    quote[:80],
                )
            continue
        updated = updated.replace(quote, finding.replacement or "", 1)
        finding.fixed = True
        logs.append(finding.summary or finding.code)
    return FormsIntegrityResult(content=updated, findings=findings, fix_logs=logs)


def _insurance_facts(draft: ProposalDraft, research: ProposalResearchCache | None) -> str:
    chunks: list[str] = []
    for section in draft.sections:
        title = (section.title or "").casefold()
        sid = (section.id or "").casefold()
        if sid == "section-1-insurance" or title.startswith("1.5"):
            chunks.append(f"{section.title}:\n{section.content or ''}")
    if research is not None:
        for item in research.evidence_corpus or []:
            src = item.source or ""
            text = item.excerpt or ""
            if "companyfacts" in src.casefold():
                chunks.append(f"companyfacts ({src}):\n{text}")
    return "\n\n".join(chunks).strip() or "(no Section 1.5 / companyfacts insurance text)"


def _reference_facts(draft: ProposalDraft) -> str:
    chunks: list[str] = []
    for section in draft.sections:
        if section_is_forms_attachments(section):
            continue
        title = (section.title or "").casefold()
        sid = (section.id or "").casefold()
        if "reference" not in title and "reference" not in sid:
            continue
        chunks.append(f"{section.title}:\n{section.content or ''}")
    return "\n\n".join(chunks).strip() or "(no References section with contact entries)"


def _budget_facts(draft: ProposalDraft, research: ProposalResearchCache | None) -> str:
    budget: ProposalBudget | None = research.budget if research else None
    lines: list[str] = []
    if budget is not None:
        fmt = (budget.budget_format or "").strip() or "unknown"
        items = budget.line_items or []
        bindable = [
            it
            for it in items
            if isinstance(it.rate, (int, float)) and it.rate and (it.unit or "").casefold() in {"hour", "hr", "hourly"}
        ]
        # Also count personnel_loading rows with numeric rates
        numeric_rates = [
            it for it in items if isinstance(it.rate, (int, float)) and it.rate
        ]
        lines.append(f"budgetFormat: {fmt}")
        lines.append(f"lineItemCount: {len(items)}")
        lines.append(f"numericRateCount: {len(numeric_rates)}")
        lines.append(f"hourlyUnitRateCount: {len(bindable)}")
        if fmt.casefold() == "phased":
            lines.append(
                "Manuscript Cost Proposal is fixed project phases — there is no "
                "approved hourly labor-category table unless numericRateCount shows hourly units."
            )
        elif fmt.casefold() == "personnel_loading" and len(numeric_rates) < 3:
            lines.append(
                "Personnel-loading format selected but hourly rates are not bindable yet."
            )
    idx = find_budget_section_index(list(draft.sections))
    if idx is not None:
        cost = draft.sections[idx]
        lines.append(f"Cost section title: {cost.title}")
        lines.append((cost.content or "")[:2500])
    return "\n".join(lines).strip() or "(no canonical budget)"


def _findings_from_payload(raw: Any) -> list[FormsIntegrityFinding]:
    if not isinstance(raw, dict):
        return []
    issues = raw.get("issues") or []
    if not isinstance(issues, list):
        return []
    out: list[FormsIntegrityFinding] = []
    for item in issues:
        if not isinstance(item, dict):
            continue
        quote = str(item.get("verbatimQuote") or item.get("verbatim_quote") or "").strip()
        action = str(item.get("fixAction") or item.get("fix_action") or "replace").casefold()
        if action == "none":
            continue
        out.append(
            FormsIntegrityFinding(
                code=str(item.get("code") or "other"),
                summary=str(item.get("summary") or "").strip(),
                verbatim_quote=quote,
                replacement=str(item.get("replacement") or ""),
            )
        )
    return out


async def audit_and_repair_forms_attachments(
    content: str,
    *,
    draft: ProposalDraft,
    research: ProposalResearchCache | None,
) -> FormsIntegrityResult:
    """LLM-audit every forms mismatch, then apply verbatim replacements."""
    body = content or ""
    if not body.strip():
        return FormsIntegrityResult(content=body)

    user = (
        "=== SECTION ===\n"
        f"{body}\n\n"
        "=== INSURANCE FACTS (only named carriers/NAIC here may stay) ===\n"
        f"{_insurance_facts(draft, research)}\n\n"
        "=== BUDGET FACTS ===\n"
        f"{_budget_facts(draft, research)}\n\n"
        "=== REFERENCE FACTS ===\n"
        f"{_reference_facts(draft)}\n"
    )
    try:
        raw, _provider = await llm.chat_json(
            [
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": user[:24_000]},
            ],
            max_tokens=2500,
            temperature=0.1,
            node_name="forms_attachments_integrity",
        )
    except Exception:
        logger.exception("forms_attachments_integrity LLM audit failed")
        return FormsIntegrityResult(content=body)

    findings = _findings_from_payload(raw)
    if not findings:
        return FormsIntegrityResult(content=body)
    return apply_verbatim_replacements(body, findings)


def format_forms_integrity_reply(result: FormsIntegrityResult, *, section_title: str) -> str:
    if not result.findings:
        return (
            f"**{section_title}** — audited submission compliance against the manuscript. "
            "No status-claim mismatches found."
        )
    lines = [
        f"**{section_title} — compliance audit ({len(result.findings)} issue(s))**",
        "",
        "**Issues found:**",
    ]
    for i, finding in enumerate(result.findings, 1):
        status = "fixed" if finding.fixed else "needs manual review"
        lines.append(f"{i}. {finding.summary or finding.code} — *{status}*")
    if result.fix_logs:
        lines.extend(["", "**Fixes applied:**"])
        for log in result.fix_logs[:12]:
            lines.append(f"- {log}")
        if len(result.fix_logs) > 12:
            lines.append(f"- …and {len(result.fix_logs) - 12} more")
    elif not result.changed:
        lines.append("")
        lines.append("No automatic fixes applied — review manually or supply KB evidence.")
    return "\n".join(lines)
