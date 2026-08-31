"""LLM pass: manuscript internal + KB verified-fact contradictions.

Catches cross-section conflicts (team size 20 vs 35) and claims that conflict with
01_companyfacts_verified — the designated single source of truth for agency profile
facts. Won/finalist proposals (06_WON_, 07_FIN_) must not override companyfacts.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app.models.proposal import ProposalDraft, ProposalSection
from app.models.rfp import RfpRecord
from app.services import llm
from app.services.proposal_scan_rfp_contradictions import _manuscript_digest
from app.services.proposal_section_patch import (
    TARGETED_EDIT_CONTRACT as _PATCH_CONTRACT,
    apply_targeted_edits as _apply_targeted_edits,
    parse_targeted_edits as _parse_targeted_edits,
)

logger = logging.getLogger(__name__)

_SYSTEM = """You are a proposal fact-consistency editor for zö agency.

TASK: Read the FULL proposal manuscript and the VERIFIED company-facts corpus.
Find contradictions, fabrications, and cross-section conflicts — especially
numeric agency profile claims.

CANONICAL SOURCE (always wins):
- 01_companyfacts_verified / 01_companyfacts verified.docx — single source of truth
  for agency profile facts: team size, founded year, legal name, ownership,
  agency-level certifications (WBENC, WOSB), office location, contact email/phone,
  website, etc.

IS a contradiction (flag these):
- STANDING CORRECTION CONFLICT: a named person's title, role, or employment
  status anywhere in the manuscript (org chart, bios, staffing tables) does not
  match a "## STANDING CORRECTIONS" entry for that same person (e.g. a title
  change, a retirement/departure). A correction is newer than every KB document
  and the roster — it always wins. Severity=critical. rewrite → update to the
  correction's wording (or remove the person if the correction says they left).
- Contact email / phone / website in Business Information (or repeated agency-wide)
  that conflicts with companyfacts (e.g. companyfacts Email: connect@zo.agency but
  draft says info@zo.agency or hello@zo.agency)
- Team size / headcount / "N professionals" / "N full-time" / "N+ specialists"
  that conflicts with companyfacts (e.g. companyfacts Team Size: 35 but draft
  says "20 core team + 35+ specialists" — invented split with no KB source)
- DIFFERENT team-size numbers in DIFFERENT sections of the same manuscript
- Bio **Role on this engagement** that contradicts the org chart (e.g. Agency Director
  in Section 1.2 but Creative Director on the bio tab for the same person)
- Sector-tailored bio claims (transit authority, bike share, Northeast mobility) when
  04_Bio KB does not support them
- Founded year, legal name, or tenure that conflicts with companyfacts
- Agency certifications in prose when not listed in companyfacts
- Treating numbers from old won/finalist proposals (06_WON_, 07_FIN_) as current
  agency facts when they conflict with companyfacts (e.g. "15 professionals")
- SIGNED INSURANCE CERTIFICATIONS: Exception Forms / compliance tables that mark
  coverages "Compliant", assert "meets or exceeds" RFP insurance minimums, or claim
  "No exceptions" when Section 1.5 / companyfacts do not list that coverage type
  (e.g. Automobile Liability) or do not state the certified dollar limits
  (e.g. $2M aggregate vs types-only / $1M narrative). Severity=critical.
  fixAction=rewrite → MANUAL FILL for Sonja/COI verification OR an honest exception /
  bind-before-execution commitment — NEVER leave a false Compliant certification.
- INVENTED PAST TECHNICAL CAPABILITY: "We have implemented / integrated / delivered"
  specific systems, integrations, or specialist workflows when NO case study, bio,
  or companyfacts excerpt in the manuscript evidences that exact past delivery.
  Severity=critical/major. Rewrite to adjacent verified experience or [VERIFY] —
  never assert checkable past work that is not in the evidence.
- STATE BUSINESS REGISTRATION: "registered / qualified / authorized to conduct
  business in [State]" when that state is not on the companyfacts / Section 1.3
  State Registrations list. Severity=critical. rewrite → MANUAL FILL for Sonja
  (public-record filing) or delete the sentence. Never leave a signed letter
  asserting an unlisted foreign qualification.
- CASE STUDY NAME / URL: a project title, client name, or URL that does not
  appear in Section 3 cards, 03_CS, or ClientList. Severity=critical. Remove or
  VERIFY — never keep an invented case-study name or link.
- CASE STUDY / SECTOR FRAMING MISMATCH: narrative claims government / utility /
  enterprise web proof while the included Section 3 case studies are private
  healthcare, retail, or otherwise do not demonstrate the claimed capability.
  Rewrite the framing to match the actual studies shown, or flag VERIFY for
  better portfolio selections.
- INVENTED CASE-STUDY METRICS: impressions, clicks, CTR, or % lift in a campaign
  write-up when the 03_CS / case-study KB excerpt does not contain those numbers.
  Rewrite to qualitative outcomes from the source or [VERIFY] — never keep invented KPIs.
- UNHEDGED LEGAL/COMPLIANCE CERTIFICATION WITHOUT EVIDENCE, OR AGAINST ITS OWN
  FLAG: any confident, unqualified statement that a legal or compliance fact is
  settled — no conflicts of interest, no litigation history, never
  debarred/suspended, active E-Verify enrollment, bonding compliance, and
  anything else in this family — is a contradiction when EITHER (a) no
  KB/companyfacts evidence supports it, OR (b) the same section already
  contains a flag/note saying that exact fact needs confirmation from
  Sonja/leadership/Operations before it can be certified (a flag and a
  confident assertion of the same fact cannot both stand). Judge this by
  MEANING — an unhedged sworn-style certification with no source — not by
  matching any fixed phrase list; the wording will vary by RFP and by
  section. Severity=critical. fixAction=rewrite → replace the confident
  assertion with a [MANUAL FILL: Sonja — confirm before submission] flag; if
  a flag already exists for the same fact, remove the contradictory assertion
  so only the flag remains.
- INTERNAL PROCESS/QC NOTE LEAKED AS CONTENT: a section's answer is itself an
  instruction, caveat, or reviewer note ADDRESSED TO whoever is writing or
  checking the proposal (e.g. what to claim, what NOT to claim, how a fact
  should be scoped) rather than being the actual fact or content that field
  requires — for example a certifications exhibit's "Item" entry containing
  guidance about how to describe a certificate instead of the certificate's
  name and validity. Judge this by FUNCTION — does this text talk about the
  content instead of being the content — not by matching specific words,
  since the leaked note's wording differs every time. Severity=critical.
  fixAction=rewrite → replace with the clean, direct fact from verified
  companyfacts/KB evidence, or [VERIFY]/[MANUAL FILL] if the exact detail
  (e.g. an expiration date) is not in evidence — never leave internal
  guidance text as the visible answer.
- INTERNAL NUMERIC SELF-CONTRADICTION: two claims about the same span or
  count that cannot both be true appear in the same section (most often a
  bio) — e.g. "25 years" and "three decades" describing one person's tenure,
  or two different headcounts for the same team. Flag regardless of the
  specific numbers or units involved. Severity=major. fixAction=rewrite →
  keep whichever number matches companyfacts/KB evidence and remove the
  other; if neither is grounded, state the span once without inventing which
  one is correct.

NOT a contradiction (do NOT flag):
- RFP-specific project staffing (named roles on THIS engagement — Section 2 bios)
- [VERIFY] / [MANUAL FILL] tags already flagging uncertainty
- Budget/fees/dollar amounts (handled elsewhere)
- Duplication of Who We Are in other tabs (Senior Editor handles dedupe)
- Case-study metrics whose exact figures appear in the 03_CS source for that study

NEVER invent replacement numbers. When fixing team size, use the companyfacts
value exactly OR remove the invented claim and state team size per verified facts.

Return ONLY JSON:
{
  "contradictions": [
    {
      "sectionId": "exact id from manuscript",
      "sectionTitle": "title",
      "verifiedFact": "what companyfacts / another section authoritatively says",
      "manuscriptContradiction": "what this section wrongly claims",
      "severity": "critical|major|minor",
      "fixAction": "rewrite|verify|human",
      "rewriteInstruction": "if rewrite: fix using companyfacts only; else empty"
    }
  ],
  "summary": "one sentence"
}"""


@dataclass
class FactContradictionFinding:
    section_id: str
    section_title: str
    verified_fact: str
    manuscript_contradiction: str
    severity: str
    fix_action: str
    rewrite_instruction: str = ""

    def banner_line(self) -> str:
        return (
            f"{self.section_title or self.section_id}: "
            f"{self.manuscript_contradiction[:160]} "
            f"(verified: {self.verified_fact[:120]})"
        )


@dataclass
class ManuscriptFactContradictionResult:
    draft: ProposalDraft
    findings: list[FactContradictionFinding] = field(default_factory=list)
    unresolved_findings: list[FactContradictionFinding] = field(default_factory=list)
    rewrites_applied: int = 0
    verify_tags_added: int = 0
    logs: list[str] = field(default_factory=list)
    summary: str = ""


def _parse_findings(raw: dict[str, Any], draft: ProposalDraft) -> list[FactContradictionFinding]:
    known = {s.id for s in draft.sections}
    out: list[FactContradictionFinding] = []
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
        verified = str(
            row.get("verifiedFact")
            or row.get("verified_fact")
            or row.get("canonicalFact")
            or ""
        ).strip()
        contra = str(
            row.get("manuscriptContradiction")
            or row.get("manuscript_contradiction")
            or ""
        ).strip()
        if not verified or not contra:
            continue
        severity = str(row.get("severity") or "major").strip().casefold()
        if severity not in {"critical", "major", "minor"}:
            severity = "major"
        action = str(row.get("fixAction") or row.get("fix_action") or "human").strip().casefold()
        if action not in {"rewrite", "verify", "human"}:
            action = "human"
        section = next(s for s in draft.sections if s.id == sid)
        out.append(
            FactContradictionFinding(
                section_id=sid,
                section_title=str(
                    row.get("sectionTitle") or row.get("section_title") or section.title or ""
                ),
                verified_fact=verified[:400],
                manuscript_contradiction=contra[:400],
                severity=severity,
                fix_action=action,
                rewrite_instruction=str(
                    row.get("rewriteInstruction") or row.get("rewrite_instruction") or ""
                ).strip()[:800],
            )
        )
    return out


async def _fetch_verified_facts_corpus() -> tuple[str, list[str]]:
    try:
        from app.services.company_qualification.retrieval.company_queries import (
            fetch_company_truth_corpus,
        )

        corpus, sources = await fetch_company_truth_corpus()
        return corpus[:48_000], sources[:12]
    except Exception as exc:  # noqa: BLE001
        logger.warning("Company truth corpus fetch failed: %s", exc)
        return "", []


async def _rewrite_section_for_fact_contradiction(
    section: ProposalSection,
    *,
    finding: FactContradictionFinding,
    verified_corpus: str,
    rfp: RfpRecord,
) -> tuple[ProposalSection, bool, str]:
    if not llm.is_configured():
        return section, False, ""
    system = (
        "You fix ONE proposal section so it no longer contradicts verified zö "
        "company facts OR invents ungrounded certifications / capabilities.\n"
        "01_companyfacts_verified is the single source of truth for agency profile "
        "(team size, founded year, legal name, agency certifications).\n"
        "Section 1.5 Insurance Information is authoritative for which coverage TYPES "
        "are claimed in this manuscript — never leave Exception Form 'Compliant' marks "
        "or 'meets or exceeds' insurance language when 1.5/companyfacts do not support "
        "that coverage type or dollar limit. Use [MANUAL FILL: Sonja — confirm on COI…] "
        "or an honest exception / bind-before-execution commitment.\n"
        "Do not invent past technical deliveries, specialist role titles without named "
        "bios, or sector proof the included case studies do not support.\n"
        "Do not invent numbers or splits (e.g. '20 core + 35 network') unless "
        "explicitly supported in the verified corpus below.\n"
        "Prefer removing fabricated claims and stating verified facts simply, or one "
        "precise [VERIFY]/[MANUAL FILL] if the corpus is silent.\n"
        "Keep brand voice for narrative sections.\n"
        + _PATCH_CONTRACT
    )
    user = (
        f"Client: {rfp.client}\nRFP: {rfp.title}\n"
        f"Section: {section.title} (id={section.id})\n\n"
        f"Verified fact (authoritative):\n{finding.verified_fact}\n\n"
        f"Contradiction in draft:\n{finding.manuscript_contradiction}\n\n"
        f"Fix instruction:\n"
        f"{finding.rewrite_instruction or 'Align with verified company facts only.'}\n\n"
        f"Verified company facts corpus:\n{verified_corpus[:18_000]}\n\n"
        f"Current section (copy `find` text verbatim from here):\n{(section.content or '')[:10_000]}"
    )
    try:
        raw, _ = await llm.chat_json(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            max_tokens=4096,
            temperature=0.0,
            node_name=f"manuscript_fact_contradiction_rewrite:{section.id}",
            rfp_id=rfp.id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Fact contradiction patch failed for %s: %s", section.id, exc)
        return section, False, ""
    if not isinstance(raw, dict):
        return section, False, ""
    new_body, applied, changed, reason = _apply_targeted_edits(
        section.content or "", _parse_targeted_edits(raw)
    )
    if not changed:
        # Never fall back to a whole-section rewrite — leave the good body intact.
        return section, False, str(raw.get("notes") or reason)
    return (
        section.model_copy(update={"content": new_body, "status": "generated"}),
        True,
        str(raw.get("notes") or f"patched {applied} span(s) to align with verified facts"),
    )


def _append_verify_note(section: ProposalSection, finding: FactContradictionFinding) -> ProposalSection:
    from app.services.proposal_manual_flags import format_verify_tag

    note = format_verify_tag(
        f"resolve fact contradiction — {finding.manuscript_contradiction[:180]} "
        f"| Verified source says: {finding.verified_fact[:140]}"
    )
    body = section.content or ""
    if note[:60].casefold() in body.casefold():
        return section
    new_body = f"{note}\n\n{body}".strip()
    return section.model_copy(update={"content": new_body, "status": "generated"})


async def run_manuscript_fact_contradiction_pass(
    draft: ProposalDraft,
    *,
    rfp: RfpRecord,
    use_llm: bool = True,
    only_rewrite_section_ids: frozenset[str] | None = None,
    precomputed_raw: dict[str, Any] | None = None,
) -> ManuscriptFactContradictionResult:
    """LLM scan for internal + KB verified-fact contradictions across all sections.

    ``precomputed_raw`` (from the combined contradiction detector): the parsed
    audit JSON for THIS dimension, so this pass skips its own detection LLM call
    and goes straight to parse + apply. The verified-facts corpus is still
    fetched — the per-finding rewrites need it.
    """
    result = ManuscriptFactContradictionResult(draft=draft)
    if not use_llm or not llm.is_configured():
        result.logs.append("Manuscript fact-contradiction scan skipped (LLM unavailable).")
        return result

    digest = _manuscript_digest(draft, max_chars=32_000)
    if not digest.strip():
        result.logs.append("Manuscript fact-contradiction scan skipped (empty manuscript).")
        return result

    verified_corpus, sources = await _fetch_verified_facts_corpus()
    source_note = (
        f"Sources: {', '.join(sources[:6])}" if sources else "No companyfacts corpus retrieved."
    )

    if precomputed_raw is not None:
        raw: Any = precomputed_raw
    else:
        user = (
            f"Client: {rfp.client}\nRFP title: {rfp.title}\n\n"
            f"VERIFIED COMPANY FACTS (01_companyfacts_verified — authoritative):\n"
            f"{verified_corpus or '(corpus unavailable — still flag cross-section conflicts)'}\n\n"
            f"{source_note}\n\n"
            f"FULL MANUSCRIPT (all sections — check EVERY tab for conflicts):\n{digest}"
        )
        try:
            raw, _ = await llm.chat_json(
                [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": user}],
                max_tokens=4096,
                temperature=0.0,
                node_name="manuscript_fact_contradiction_audit",
                rfp_id=rfp.id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Manuscript fact-contradiction audit failed: %s", exc)
            result.logs.append(f"Manuscript fact-contradiction audit failed: {exc}")
            return result

    if not isinstance(raw, dict):
        result.logs.append("Manuscript fact-contradiction audit returned non-object JSON.")
        return result

    findings = _parse_findings(raw, draft)
    result.findings = findings
    result.summary = str(raw.get("summary") or "").strip()

    if not findings:
        result.logs.append(
            "Manuscript fact-contradiction scan: no internal/KB contradictions found."
        )
        return result

    result.logs.append(
        f"Manuscript fact-contradiction scan: {len(findings)} issue(s) "
        f"({sum(1 for f in findings if f.severity == 'critical')} critical)."
    )

    sections = list(draft.sections)
    by_id = {s.id: i for i, s in enumerate(sections)}
    fixed_ids: set[str] = set()

    for finding in findings:
        if (
            only_rewrite_section_ids is not None
            and finding.section_id not in only_rewrite_section_ids
        ):
            result.logs.append(
                f"{finding.section_id}: skipped fact-contradiction rewrite "
                "(outside scoped track)"
            )
            continue
        idx = by_id.get(finding.section_id)
        if idx is None:
            continue
        # Unlike the RFP-contradiction and budget-contradiction passes, this
        # pass exists specifically to catch stale facts on these sections
        # (team roster titles, certifications, etc.) — including conflicts
        # with a standing correction, which this pass's own LLM call also
        # receives. Excluding it here would remove a real repair path
        # (e.g. a roster title that standing corrections have since changed).
        section = sections[idx]
        if (
            (section.id or "").startswith("section-2-bio-")
            and section.id != "section-2-bio-placeholder"
        ):
            from app.services.proposal_bio_stub import is_bio_pdf_designer_note

            if is_bio_pdf_designer_note(section.content or ""):
                # A designer-note stub hands the bio off to an approved PDF —
                # there is no narrative prose here to fix, and rewriting the
                # stub itself would fight the "insert PDF as-is" instruction.
                result.logs.append(
                    f"{finding.section_id}: skipped fact-contradiction rewrite "
                    "on bio PDF designer-note stub"
                )
                continue
            # Any other bio has real narrative content and gets the same
            # fact-consistency treatment as every other section — skipping
            # bios wholesale would silently drop real findings (e.g. two
            # contradicting tenure claims in one bio).
        if finding.severity in {"critical", "major"}:
            updated, changed, notes = await _rewrite_section_for_fact_contradiction(
                section,
                finding=finding,
                verified_corpus=verified_corpus,
                rfp=rfp,
            )
            if changed:
                sections[idx] = updated
                result.rewrites_applied += 1
                fixed_ids.add(finding.section_id)
                result.logs.append(
                    f"{finding.section_id}: FIXED fact contradiction by rewrite"
                    + (f" — {notes}" if notes else "")
                )
                continue
        if finding.severity != "minor":
            from app.services.agency_facts import (
                enforce_agency_tenure,
                ticket_is_agency_tenure,
            )

            tenure_blob = (
                f"{finding.manuscript_contradiction} {finding.verified_fact} "
                f"{finding.rewrite_instruction}"
            )
            if ticket_is_agency_tenure(tenure_blob):
                body = sections[idx].content or ""
                fixed = enforce_agency_tenure(body)
                if fixed != body:
                    sections[idx] = sections[idx].model_copy(update={"content": fixed})
                    result.rewrites_applied += 1
                    fixed_ids.add(finding.section_id)
                    result.logs.append(
                        f"{finding.section_id}: FIXED agency tenure from companyfacts "
                        "(no VERIFY banner)"
                    )
                    continue
                result.logs.append(
                    f"{finding.section_id}: tenure already canonical — skipped VERIFY banner"
                )
                fixed_ids.add(finding.section_id)
                continue
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
