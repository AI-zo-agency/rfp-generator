"""Whole-manuscript adversarial auditor (findings only; never rewrites content)."""

from __future__ import annotations

import hashlib
import logging
import re
from collections import OrderedDict
from datetime import datetime, timezone

from app.models.proposal import (
    AdversarialAuditFinding,
    PreSubmitIssue,
    ProposalAdversarialAudit,
    ProposalDraft,
    ProposalResearchCache,
)
from app.models.rfp import RfpRecord
from app.services import llm
from app.services.llm import LlmError
from app.services.proposal_presubmit_review import run_presubmit_review

logger = logging.getLogger(__name__)

_EXPLICIT_CODE_RE = re.compile(r"^\[(T\d+):([^\]]+)\]\s*", re.I)
_NON_CODE_CHARS_RE = re.compile(r"[^a-z0-9]+")
_AUDIT_PROMPT = """You are a whole-manuscript adversarial auditor for government proposal drafts.

Return JSON only:
{
  "findings": [
    {
      "severity": "critical|warning|info",
      "category": "fabrication|inconsistency|duplication|budget_money|note_leak|other",
      "code": "llm.<category>.<short_code>",
      "message": "finding only; do not rewrite",
      "sectionId": "string or null",
      "sectionTitle": "string or null",
      "excerpt": "short quote or null"
    }
  ]
}

Rules:
- Findings only. Never rewrite manuscript text.
- Do not repeat deterministic findings already listed unless needed to clarify a distinct residual defect.
- Focus on residual fabrication, inconsistency, duplication, budget/money, or note-leak defects a deterministic scan may miss.
- Never resolve, remove, or normalize [VERIFY], [MANUAL FILL], or legal attestation tags.
- If unsure, omit the finding.
"""


def _slug(text: str, *, fallback: str) -> str:
    slug = _NON_CODE_CHARS_RE.sub("_", text.casefold()).strip("_")
    return slug[:64] or fallback


def _normalize_issue_code(issue: PreSubmitIssue) -> str:
    message = (issue.message or "").strip()
    match = _EXPLICIT_CODE_RE.match(message)
    if match:
        family = match.group(1).casefold()
        raw = match.group(2).strip().casefold()
        if raw.startswith(f"{family}."):
            return raw
        return f"{family}.{raw.replace(':', '.').replace(' ', '_')}"
    category = (issue.category or "other").strip().casefold() or "other"
    return f"deterministic.{category}.{_slug(message, fallback='finding')}"


def _issue_to_finding(issue: PreSubmitIssue) -> AdversarialAuditFinding:
    message = (issue.message or "").strip()
    normalized = _EXPLICIT_CODE_RE.sub("", message, count=1).strip() or message
    return AdversarialAuditFinding(
        severity=issue.severity,
        category=issue.category or "other",
        code=_normalize_issue_code(issue),
        message=normalized,
        sectionId=issue.section_id,
        sectionTitle=issue.section_title,
        excerpt=issue.excerpt,
        source="deterministic",
    )


def _dedupe_findings(
    findings: list[AdversarialAuditFinding],
) -> list[AdversarialAuditFinding]:
    seen: set[tuple[str, str, str, str, str]] = set()
    deduped: list[AdversarialAuditFinding] = []
    for finding in findings:
        key = (
            finding.severity,
            finding.category,
            finding.code,
            finding.section_id or "",
            finding.message,
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(finding)
    return deduped


def _build_deterministic_findings(
    *,
    draft: ProposalDraft,
    research: ProposalResearchCache | None,
    rfp: RfpRecord,
) -> list[AdversarialAuditFinding]:
    review = run_presubmit_review(rfp=rfp, draft=draft, research=research)
    findings = [_issue_to_finding(issue) for issue in review.issues]
    deduped = _dedupe_findings(findings)
    logger.info(
        "manuscript_auditor deterministic_findings=%s critical=%s rfp_id=%s",
        len(deduped),
        sum(1 for finding in deduped if finding.severity == "critical"),
        rfp.id,
    )
    return deduped


def _deterministic_findings_markdown(findings: list[AdversarialAuditFinding]) -> str:
    if not findings:
        return "_No deterministic findings._"
    lines = []
    for finding in findings[:80]:
        line = (
            f"- [{finding.severity}/{finding.category}] code={finding.code}: "
            f"{finding.message}"
        )
        if finding.section_title:
            line += f" (section: {finding.section_title})"
        lines.append(line)
    if len(findings) > 80:
        lines.append(f"- ... and {len(findings) - 80} more")
    return "\n".join(lines)


def _parse_llm_finding(row: object) -> AdversarialAuditFinding | None:
    if not isinstance(row, dict):
        return None
    severity = str(row.get("severity") or "").strip().casefold()
    if severity not in {"critical", "warning", "info"}:
        return None
    message = str(row.get("message") or "").strip()
    if not message:
        return None
    category = str(row.get("category") or "other").strip().casefold() or "other"
    code = str(row.get("code") or "").strip()
    return AdversarialAuditFinding(
        severity=severity,
        category=category,
        code=code or f"llm.{category}.{_slug(message, fallback='finding')}",
        message=message,
        sectionId=row.get("sectionId"),
        sectionTitle=row.get("sectionTitle"),
        excerpt=(str(row.get("excerpt"))[:240] if row.get("excerpt") else None),
        source="llm",
    )


# Memoize the whole-manuscript LLM audit on the exact payload sent.
#
# The audit costs ~30k input tokens and fires 4-6 times per generation: once
# before the adversarial repair loop, once inside it, once after escalation,
# and again from _attach_phase4_manuscript_audit. Several of those run against
# a manuscript that has not changed since the previous call — the post-loop
# audit in particular re-audits a byte-identical draft. Keying on the rendered
# payload makes the skip exact rather than heuristic.
_AUDIT_CACHE_MAX = 8
_audit_cache: "OrderedDict[str, tuple[list[AdversarialAuditFinding], str | None]]" = (
    OrderedDict()
)


def clear_manuscript_audit_cache() -> None:
    """Drop memoized audits — for tests and for forced re-scan paths."""
    _audit_cache.clear()


def _audit_cache_get(
    key: str,
) -> tuple[list[AdversarialAuditFinding], str | None] | None:
    hit = _audit_cache.get(key)
    if hit is not None:
        _audit_cache.move_to_end(key)
    return hit


def _audit_cache_put(
    key: str, value: tuple[list[AdversarialAuditFinding], str | None]
) -> None:
    _audit_cache[key] = value
    _audit_cache.move_to_end(key)
    while len(_audit_cache) > _AUDIT_CACHE_MAX:
        _audit_cache.popitem(last=False)


async def _run_llm_residual_audit(
    *,
    draft: ProposalDraft,
    research: ProposalResearchCache | None,
    rfp: RfpRecord,
    deterministic_findings: list[AdversarialAuditFinding],
) -> tuple[list[AdversarialAuditFinding], str | None]:
    sections = [
        {
            "sectionId": section.id,
            "sectionTitle": section.title,
            "content": (section.content or "")[:9000],
        }
        for section in draft.sections
        if (section.content or "").strip()
    ]
    if not sections:
        return [], None

    user_content = (
        f"RFP ID: {rfp.id}\n"
        f"Client: {rfp.client}\n"
        f"Title: {rfp.title}\n"
        f"Has budget: {bool(research and research.budget)}\n\n"
        "=== DETERMINISTIC FINDINGS ALREADY KNOWN ===\n"
        f"{_deterministic_findings_markdown(deterministic_findings)}\n\n"
        f"=== MANUSCRIPT SECTIONS ===\n{sections}"
    )
    cache_key = hashlib.sha256(user_content.encode("utf-8")).hexdigest()
    cached = _audit_cache_get(cache_key)
    if cached is not None:
        logger.info(
            "manuscript_auditor cache_hit rfp_id=%s findings=%s — manuscript "
            "unchanged since last audit, skipping LLM call",
            rfp.id,
            len(cached[0]),
        )
        return cached

    try:
        raw, provider = await llm.chat_json(
            [
                {"role": "system", "content": _AUDIT_PROMPT},
                {"role": "user", "content": user_content},
            ],
            max_tokens=4096,
            temperature=0.0,
            tier="heavy",
            node_name="manuscript_auditor",
            rfp_id=rfp.id,
        )
    except LlmError as exc:
        logger.warning(
            "manuscript_auditor llm_failed rfp_id=%s error=%s",
            rfp.id,
            str(exc)[:200],
        )
        return [], None

    findings: list[AdversarialAuditFinding] = []
    for row in (raw.get("findings") or []):
        finding = _parse_llm_finding(row)
        if finding is None:
            continue
        findings.append(finding)
    deduped = _dedupe_findings(findings)
    logger.info(
        "manuscript_auditor llm_findings=%s critical=%s rfp_id=%s provider=%s",
        len(deduped),
        sum(1 for finding in deduped if finding.severity == "critical"),
        rfp.id,
        provider,
    )
    _audit_cache_put(cache_key, (deduped, provider))
    return deduped, provider


def _summary(findings: list[AdversarialAuditFinding]) -> str:
    critical = sum(1 for finding in findings if finding.severity == "critical")
    warning = sum(1 for finding in findings if finding.severity == "warning")
    info = sum(1 for finding in findings if finding.severity == "info")
    if not findings:
        return "No adversarial audit findings."
    return (
        f"{critical} critical, {warning} warning, {info} info finding(s) "
        f"across deterministic and residual adversarial checks."
    )


async def run_manuscript_auditor(
    *,
    draft: ProposalDraft,
    research: ProposalResearchCache | None,
    rfp: RfpRecord,
    use_llm: bool = True,
) -> ProposalAdversarialAudit:
    """Scan full manuscript for findings only; never mutates manuscript content."""
    deterministic_findings = _build_deterministic_findings(
        draft=draft,
        research=research,
        rfp=rfp,
    )
    llm_findings: list[AdversarialAuditFinding] = []
    provider = "deterministic"
    if use_llm:
        llm_findings, llm_provider = await _run_llm_residual_audit(
            draft=draft,
            research=research,
            rfp=rfp,
            deterministic_findings=deterministic_findings,
        )
        if llm_provider:
            provider = llm_provider

    findings = _dedupe_findings([*deterministic_findings, *llm_findings])
    return ProposalAdversarialAudit(
        rfpId=rfp.id,
        findings=findings,
        summary=_summary(findings),
        scannedAt=datetime.now(timezone.utc).isoformat(),
        provider=provider,
    )


def persist_manuscript_audit(
    research: ProposalResearchCache | None,
    audit: ProposalAdversarialAudit,
) -> ProposalResearchCache:
    """Attach adversarial audit findings to research using existing model-copy pattern."""
    now = datetime.now(timezone.utc).isoformat()
    base = research or ProposalResearchCache(rfpId=audit.rfp_id, updatedAt=now)
    logger.info(
        "manuscript_auditor persist_findings=%s critical=%s rfp_id=%s",
        len(audit.findings),
        sum(1 for finding in audit.findings if finding.severity == "critical"),
        audit.rfp_id,
    )
    return base.model_copy(
        update={
            "adversarial_audit": audit,
            "updated_at": now,
        }
    )


def collect_adversarial_critical_findings_for_blockers(
    *,
    draft: ProposalDraft,
    research: ProposalResearchCache | None,
    rfp: RfpRecord | None,
) -> list[AdversarialAuditFinding]:
    """Fresh readiness scan for adversarial criticals (mirrors consistency OQ-4).

    Deterministic criticals are always re-scanned from the live draft so a stale
    ``research.adversarial_audit`` cannot block (or clear) incorrectly. LLM residual
    criticals cannot be re-run synchronously, so those are retained from the last
    persisted audit when present.
    """
    findings: list[AdversarialAuditFinding] = []
    if rfp is not None:
        findings.extend(
            finding
            for finding in _build_deterministic_findings(
                draft=draft,
                research=research,
                rfp=rfp,
            )
            if finding.severity == "critical"
        )

    audit = research.adversarial_audit if research else None
    if audit:
        for finding in audit.findings:
            if finding.severity != "critical":
                continue
            source = (finding.source or "").casefold()
            code = (finding.code or "").casefold()
            is_llm = source == "llm" or code.startswith("llm.")
            if is_llm or rfp is None:
                findings.append(finding)

    return _dedupe_findings(findings)
