"""LLM Scan pass: manuscript vs RFP requirements for real contradictions.

No regex matching of RFP obligations — the model reads RFP excerpt + manuscript
digest and returns structured contradictions. Safe rewrites are applied only when
the model supplies a full replacement section that does not invent facts/numbers.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app.models.proposal import ProposalDraft, ProposalSection
from app.models.rfp import RfpRecord
from app.services import llm

logger = logging.getLogger(__name__)

# Section 1 Builder assembles these directly from CompanyTruth / the Master
# Team Roster — "Facts/table only" (see SECTION_SPECS in section_1_builder.py).
# They carry no RFP-response narrative, so they cannot logically contain the
# kind of contradiction these audits look for (schedule overruns, budget
# ceilings, eligibility prose). Burning a rewrite call "fixing" a false
# positive here is pure waste and risks corrupting already-correct content
# (the exact "Business Information wiped" failure mode seen before).
STATIC_COMPANY_FACT_SECTION_IDS = frozenset(
    {
        "section-1-org-structure",
        "section-1-business-info",
        "section-1-certifications",
        "section-1-insurance",
    }
)

_SYSTEM = """You are a proposal compliance editor for zö agency.

TASK: Compare the proposal MANUSCRIPT against the RFP. Find ONLY real
contradictions — places where the draft conflicts with, denies, or invents the
opposite of an RFP requirement.

IS a contradiction (flag these):
- Schedule/timeline that overruns the RFP award→launch / contract window
- Budget/price claims above an RFP ceiling or that rewrite a forbidden form
- Draft denies a requirement the RFP clearly states (e.g. "RFP does not require references")
- Eligibility / submission rules the draft violates in prose
- Named criteria, page limits, or mandatory deliverables the draft explicitly contradicts

NOT a contradiction (do NOT flag):
- Missing KB proof / "unverified capability" / thin case studies (Go/No-Go, not RFP vs draft)
- Physical signed PDFs the human must attach (list under attachmentNeeds only)
- Soft quality or style suggestions
- Deadlines / question cutoffs as manuscript contradictions (list under complianceReminders)

NEVER invent dollar amounts, dates, signature IDs, notary numbers, or client facts.
Prefer fixAction=rewrite for critical/major issues. Only use verify when a single
discrete unknown field is missing — never "fix" a contradiction by sprinkling
[VERIFY] into every table cell (especially Estimated Hours / fee tables).
For missing staff hours: rewrite to use labor-category / Guide_Pricing language
OR omit the hours column and explain the compensation model — do not invent hours.

Return ONLY JSON:
{
  "contradictions": [
    {
      "sectionId": "exact id from manuscript",
      "sectionTitle": "title",
      "rfpRequirement": "what the RFP requires",
      "manuscriptContradiction": "what the draft wrongly says/does",
      "severity": "critical|major|minor",
      "fixAction": "rewrite|verify|human",
      "rewriteInstruction": "if rewrite: how to fix without inventing facts; else empty"
    }
  ],
  "attachmentNeeds": ["physically signed cover letter PDF", "..."],
  "complianceReminders": ["Electronic PDF due …", "..."],
  "summary": "one sentence"
}"""


@dataclass
class ContradictionFinding:
    section_id: str
    section_title: str
    rfp_requirement: str
    manuscript_contradiction: str
    severity: str
    fix_action: str
    rewrite_instruction: str = ""

    def banner_line(self) -> str:
        return (
            f"{self.section_title or self.section_id}: "
            f"{self.manuscript_contradiction[:160]} "
            f"(RFP: {self.rfp_requirement[:120]})"
        )


@dataclass
class RfpContradictionScanResult:
    draft: ProposalDraft
    findings: list[ContradictionFinding] = field(default_factory=list)
    unresolved_findings: list[ContradictionFinding] = field(default_factory=list)
    attachment_needs: list[str] = field(default_factory=list)
    compliance_reminders: list[str] = field(default_factory=list)
    rewrites_applied: int = 0
    verify_tags_added: int = 0
    logs: list[str] = field(default_factory=list)
    summary: str = ""


def _manuscript_digest(draft: ProposalDraft, *, max_chars: int = 28_000) -> str:
    parts: list[str] = []
    used = 0
    for section in draft.sections:
        body = (section.content or "").strip()
        if not body:
            continue
        sid = section.id or ""
        body_cap = 400 if sid.startswith("section-2-bio-") else 2200
        block = (
            f"### id={section.id} | {section.title}\n"
            f"{body[:body_cap]}\n"
        )
        if used + len(block) > max_chars:
            break
        parts.append(block)
        used += len(block)
    return "\n".join(parts)


def _parse_findings(raw: dict[str, Any], draft: ProposalDraft) -> list[ContradictionFinding]:
    known = {s.id for s in draft.sections}
    out: list[ContradictionFinding] = []
    rows = raw.get("contradictions") if isinstance(raw, dict) else None
    if not isinstance(rows, list):
        return out
    for row in rows:
        if not isinstance(row, dict):
            continue
        sid = str(row.get("sectionId") or row.get("section_id") or "").strip()
        if sid and sid not in known:
            # Allow title-only match
            title = str(row.get("sectionTitle") or row.get("section_title") or "").strip()
            match = next(
                (
                    s
                    for s in draft.sections
                    if (s.title or "").casefold() == title.casefold()
                ),
                None,
            )
            sid = match.id if match else sid
        if not sid or sid not in known:
            continue
        req = str(row.get("rfpRequirement") or row.get("rfp_requirement") or "").strip()
        contra = str(
            row.get("manuscriptContradiction")
            or row.get("manuscript_contradiction")
            or ""
        ).strip()
        if not req or not contra:
            continue
        severity = str(row.get("severity") or "major").strip().casefold()
        if severity not in {"critical", "major", "minor"}:
            severity = "major"
        action = str(row.get("fixAction") or row.get("fix_action") or "human").strip().casefold()
        if action not in {"rewrite", "verify", "human"}:
            action = "human"
        section = next(s for s in draft.sections if s.id == sid)
        out.append(
            ContradictionFinding(
                section_id=sid,
                section_title=str(
                    row.get("sectionTitle") or row.get("section_title") or section.title or ""
                ),
                rfp_requirement=req[:400],
                manuscript_contradiction=contra[:400],
                severity=severity,
                fix_action=action,
                rewrite_instruction=str(
                    row.get("rewriteInstruction") or row.get("rewrite_instruction") or ""
                ).strip()[:800],
            )
        )
    return out


async def _rewrite_section_for_contradiction(
    section: ProposalSection,
    *,
    finding: ContradictionFinding,
    rfp_excerpt: str,
    rfp: RfpRecord,
) -> tuple[ProposalSection, bool, str]:
    if not llm.is_configured():
        return section, False, ""
    from app.services.proposal_section_patch import (
        TARGETED_EDIT_CONTRACT,
        apply_targeted_edits,
        parse_targeted_edits,
    )

    system = (
        "You fix ONE proposal section so it no longer contradicts the RFP — by "
        "patching only the offending text, never by rewriting the section.\n"
        "Keep brand voice. Do not invent numbers, dates, signature IDs, clients, "
        "or dollars absent from the RFP excerpt or current draft.\n"
        "If a date/figure is unknown, prefer removing the invented column/claim "
        "(replace with empty text) or one precise [VERIFY: specific field] — never "
        "fabricate and never fill an Estimated Hours column with [VERIFY] in every row.\n"
        "For schedule overruns: replace the invented multi-week calendar span with a "
        "short dates/milestones line using timing within the RFP award→launch window.\n"
        "For fee/hours contradictions: replace the fabricated hour grid span with "
        "transparent compensation / pass-through language and Guide_Pricing labor "
        "categories when present.\n"
        "These rules govern how you write; they are never content. The [VERIFY: ...] "
        "tag is the only trace of a gap; never explain or preface it.\n"
        + TARGETED_EDIT_CONTRACT
    )
    user = (
        f"Client: {rfp.client}\nRFP: {rfp.title}\n"
        f"Section: {section.title} (id={section.id})\n\n"
        f"RFP requirement:\n{finding.rfp_requirement}\n\n"
        f"Contradiction:\n{finding.manuscript_contradiction}\n\n"
        f"Fix instruction:\n{finding.rewrite_instruction or 'Resolve the contradiction.'}\n\n"
        f"RFP excerpt:\n{rfp_excerpt[:14_000]}\n\n"
        f"Current section (copy `find` text verbatim from here):\n{(section.content or '')[:10_000]}"
    )
    try:
        raw, _ = await llm.chat_json(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            max_tokens=16000,
            temperature=0.0,
            node_name=f"scan_rfp_contradiction_rewrite:{section.id}",
            rfp_id=rfp.id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Contradiction patch failed for %s: %s", section.id, exc)
        return section, False, ""
    if not isinstance(raw, dict):
        return section, False, ""

    edits = parse_targeted_edits(raw)
    new_body, applied, changed, reason = apply_targeted_edits(section.content or "", edits)
    if not changed:
        # No verbatim span matched (or the patch was refused) — do NOT fall back
        # to a whole-section rewrite. Leave the body untouched; the caller tags
        # VERIFY instead, so a good section is never regenerated to fix one span.
        return section, False, str(raw.get("notes") or reason)
    return (
        section.model_copy(update={"content": new_body, "status": "generated"}),
        True,
        str(raw.get("notes") or f"patched {applied} span(s) to resolve RFP contradiction"),
    )


def _append_verify_note(section: ProposalSection, finding: ContradictionFinding) -> ProposalSection:
    from app.services.proposal_manual_flags import format_verify_tag

    # Sanitize interiors — a raw ] in LLM contradiction text would make
    # VERIFY_TAG_RE match only through the first ], and optional scrub would
    # leave a gibberish ``… RFP requires: …]`` orphan in the manuscript.
    note = format_verify_tag(
        f"resolve RFP contradiction — {finding.manuscript_contradiction[:180]} "
        f"| RFP requires: {finding.rfp_requirement[:140]}"
    )
    body = section.content or ""
    if note[:60].casefold() in body.casefold():
        return section
    new_body = f"{note}\n\n{body}".strip()
    return section.model_copy(update={"content": new_body, "status": "generated"})


async def run_scan_rfp_contradiction_pass(
    draft: ProposalDraft,
    *,
    rfp: RfpRecord,
    rfp_text: str,
    use_llm: bool = True,
    precomputed_raw: dict[str, Any] | None = None,
) -> RfpContradictionScanResult:
    """LLM manuscript-vs-RFP contradiction scan + safe repairs.

    ``precomputed_raw`` (from the combined contradiction detector): the parsed
    audit JSON for THIS dimension, so this pass skips its own detection LLM call
    and goes straight to parse + apply.
    """
    result = RfpContradictionScanResult(draft=draft)
    if not use_llm or not llm.is_configured():
        result.logs.append("RFP contradiction scan skipped (LLM unavailable).")
        return result

    digest = _manuscript_digest(draft)
    if not digest.strip() or len((rfp_text or "").strip()) < 200:
        result.logs.append("RFP contradiction scan skipped (insufficient RFP/manuscript text).")
        return result

    if precomputed_raw is not None:
        raw: Any = precomputed_raw
    else:
        user = (
            f"Client: {rfp.client}\nRFP title: {rfp.title}\n"
            f"Due date: {getattr(rfp, 'due_date', None) or 'unknown'}\n\n"
            f"RFP text (authoritative):\n{(rfp_text or '')[:40_000]}\n\n"
            f"Manuscript digest:\n{digest}"
        )
        try:
            raw, _ = await llm.chat_json(
                [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": user}],
                max_tokens=16000,
                temperature=0.0,
                node_name="scan_rfp_contradiction_audit",
                rfp_id=rfp.id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("RFP contradiction audit failed: %s", exc)
            result.logs.append(f"RFP contradiction audit failed: {exc}")
            return result

    if not isinstance(raw, dict):
        result.logs.append("RFP contradiction audit returned non-object JSON.")
        return result

    findings = _parse_findings(raw, draft)
    result.findings = findings
    result.summary = str(raw.get("summary") or "").strip()
    for item in raw.get("attachmentNeeds") or raw.get("attachment_needs") or []:
        if isinstance(item, str) and item.strip():
            result.attachment_needs.append(item.strip()[:200])
    for item in raw.get("complianceReminders") or raw.get("compliance_reminders") or []:
        if isinstance(item, str) and item.strip():
            result.compliance_reminders.append(item.strip()[:200])

    if not findings:
        result.logs.append("RFP contradiction scan: no manuscript-vs-RFP contradictions found.")
        return result

    result.logs.append(
        f"RFP contradiction scan: {len(findings)} contradiction(s) "
        f"({sum(1 for f in findings if f.severity == 'critical')} critical)."
    )

    sections = list(draft.sections)
    by_id = {s.id: i for i, s in enumerate(sections)}
    rfp_excerpt = (rfp_text or "")[:20_000]

    fixed_ids: set[str] = set()
    for finding in findings:
        idx = by_id.get(finding.section_id)
        if idx is None:
            continue
        if finding.section_id in STATIC_COMPANY_FACT_SECTION_IDS:
            result.logs.append(
                f"{finding.section_id}: skipped rewrite — protected static "
                "company-fact section (likely false positive)"
            )
            continue
        section = sections[idx]
        # Always attempt a real rewrite for critical/major — tagging VERIFY is
        # a fallback only when rewrite fails (never the primary "fix").
        if finding.severity in {"critical", "major"}:
            updated, changed, notes = await _rewrite_section_for_contradiction(
                section,
                finding=finding,
                rfp_excerpt=rfp_excerpt,
                rfp=rfp,
            )
            if changed:
                sections[idx] = updated
                result.rewrites_applied += 1
                fixed_ids.add(finding.section_id)
                result.logs.append(
                    f"{finding.section_id}: FIXED contradiction by rewrite"
                    + (f" — {notes}" if notes else "")
                )
                continue
        if finding.severity != "minor":
            sections[idx] = _append_verify_note(sections[idx], finding)
            result.verify_tags_added += 1
            result.logs.append(
                f"{finding.section_id}: rewrite failed — tagged VERIFY "
                f"(human must resolve): {finding.manuscript_contradiction[:120]}"
            )

    result.draft = draft.model_copy(update={"sections": sections})
    result.unresolved_findings = [
        f for f in findings if f.section_id not in fixed_ids and f.severity != "minor"
    ]
    return result
