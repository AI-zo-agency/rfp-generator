"""Complete Scan — evidence-grounding guard (principles, not edge-case lists).

Every Complete Scan run applies the same rules:

1. **Procurement attestation gate** (legal_attestation_gate) — any past-tense claim
   that vendor registration, document download, or complete-RFP review already
   happened → MANUAL FILL unless KB evidence proves it.

2. **Insurance certification gate** — Exception Forms / compliance tables that mark
   coverages Compliant or assert "meets or exceeds" RFP insurance minimums must be
   grounded in Section 1.5 inventory; otherwise → MANUAL FILL for Sonja/COI.

3. **Org chart wins** — bio *Role on this engagement* must match Section 1.2.

4. **Bio KB grounding** — narrative bio prose must overlap 04_Bio KB text; otherwise
   revert to PDF designer-note stub (no sector-tailored invention).

5. **Named-entity grounding** — specific insurer/carrier/org names in compliance prose
   must appear in scan evidence corpus or become VERIFY tags.
"""

from __future__ import annotations

import logging
import re

from app.models.proposal import ProposalDraft, ProposalSection

logger = logging.getLogger(__name__)

_ROLE_ON_ENGAGEMENT_RE = re.compile(
    r"(?is)(\*\*Role on this engagement:\*\*\s*)([^\n]+)",
)

_BIO_STOPWORDS = frozenset(
    {
        "about",
        "agency",
        "anderson",
        "brings",
        "engagement",
        "experience",
        "including",
        "leadership",
        "marketing",
        "their",
        "through",
        "which",
        "within",
        "years",
        "would",
        "zö",
    }
)

# Proper-noun carriers / vendors in compliance prose — general shape, not a carrier list.
_NAMED_ENTITY_IN_COMPLIANCE_RE = re.compile(
    r"(?is)"
    r"(?:"
    r"\(\s*([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){0,3}(?:\s+Insurance)?)\s*\)"
    r"|"
    r"(?:through|with|from|issued\s+by)\s+"
    r"([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){0,2}\s+Insurance)\b"
    r"|"
    r"(?:carrier|insurer)(?:\s+is|\s*:)\s+([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){0,2})"
    r")",
)

_MIN_BIO_NARRATIVE_CHARS = 120
_MIN_KB_OVERLAP_RATIO = 0.32


def _content_words(text: str) -> set[str]:
    words = re.findall(r"[a-z]{4,}", (text or "").casefold())
    return {w for w in words if w not in _BIO_STOPWORDS}


def _bio_narrative_text(content: str) -> str:
    """Prose lines only — skip headings, role, designer/MANUAL/VERIFY tags."""
    kept: list[str] = []
    for line in (content or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            continue
        if re.match(r"(?i)\*\*role on this engagement", stripped):
            continue
        if re.match(r"(?i)\[(?:DESIGNER NOTE|MANUAL FILL|VERIFY)", stripped):
            continue
        if stripped.startswith("|") or stripped.startswith("---"):
            continue
        kept.append(re.sub(r"\*+", "", stripped))
    return " ".join(kept)


def bio_narrative_ungrounded(content: str, kb_text: str) -> bool:
    """True when bio has substantial invented narrative vs 04_Bio KB."""
    from app.services.proposal_bio_stub import is_bio_pdf_designer_note

    if is_bio_pdf_designer_note(content):
        return False

    narrative = _bio_narrative_text(content)
    if len(narrative) < _MIN_BIO_NARRATIVE_CHARS:
        return False

    kb = (kb_text or "").strip()
    if not kb or kb.startswith("(Supermemory") or len(kb) < 120:
        return True

    narr_words = _content_words(narrative)
    if len(narr_words) < 8:
        return False

    kb_words = _content_words(kb)
    if not kb_words:
        return True

    overlap = len(narr_words & kb_words) / len(narr_words)
    return overlap < _MIN_KB_OVERLAP_RATIO


def repair_bio_role_from_org_chart(
    content: str,
    *,
    member_name: str,
    org_roles: dict[str, str],
) -> tuple[str, list[str]]:
    """Align **Role on this engagement:** with Section 1.2 org chart."""
    text = content or ""
    key = (member_name or "").strip().casefold()
    canonical = org_roles.get(key)
    if not canonical and key:
        for name, role in org_roles.items():
            parts = key.split()
            if len(parts) >= 2 and parts[0] in name and parts[-1] in name:
                canonical = role
                break
    if not canonical:
        return text, []

    match = _ROLE_ON_ENGAGEMENT_RE.search(text)
    if not match:
        return text, []

    stated = re.sub(r"\*+", "", match.group(2)).strip()
    if stated.casefold() == canonical.casefold():
        return text, []

    new_line = f"{match.group(1)}{canonical}"
    fixed = text[: match.start()] + new_line + text[match.end() :]
    return fixed, [
        f"Bio role '{stated}' → org chart '{canonical}' for {member_name}",
    ]


def scrub_ungrounded_named_entities(
    content: str,
    *,
    evidence_text: str,
) -> tuple[str, list[str]]:
    """Flag specific org/carrier names in compliance prose absent from evidence."""
    text = content or ""
    blob = (evidence_text or "").casefold()
    logs: list[str] = []

    def _entity_in_evidence(name: str) -> bool:
        n = name.strip()
        if not n:
            return True
        nc = n.casefold()
        if nc in blob:
            return True
        # Allow partial match for "Next Insurance" vs "Next Insurance workers comp"
        tokens = [t for t in re.split(r"\W+", nc) if len(t) >= 4]
        return bool(tokens) and all(t in blob for t in tokens[:2])

    for match in _NAMED_ENTITY_IN_COMPLIANCE_RE.finditer(text):
        entity = (match.group(1) or match.group(2) or match.group(3) or "").strip()
        if not entity or _entity_in_evidence(entity):
            continue
        # Skip generic words that aren't carriers
        if entity.casefold() in {"general liability", "workers compensation", "professional liability"}:
            continue
        replacement = "[VERIFY: carrier / vendor name from certificate or companyfacts]"
        text = text[: match.start()] + replacement + text[match.end() :]
        logs.append(f"Ungrounded named entity '{entity}' → VERIFY")
        break  # one pass; re-run on next scan if multiple

    return text, logs


async def _rebuild_bio_stub(
    section: ProposalSection,
    *,
    member: str,
    org_role: str,
    rfp_text: str,
) -> tuple[str, list[str]] | None:
    from app.services.proposal_bio_stub import (
        expected_bio_pdf_filename,
        is_bio_pdf_designer_note,
        resolve_bio_pdf_filename,
        rfp_requires_inline_bios,
        stub_from_extraction,
    )
    from app.services.proposal_sections_graph import _fetch_member_bio_kb

    inline_required = rfp_requires_inline_bios(rfp_text)
    body = section.content or ""
    if is_bio_pdf_designer_note(body) and not inline_required:
        return None

    pdf_name = expected_bio_pdf_filename(member)
    kb_text = ""
    kb_available = True
    extracted: dict = {}
    if inline_required:
        try:
            kb_text, sources = await _fetch_member_bio_kb(member)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Bio KB fetch failed for %s: %s", member, exc)
            kb_text, sources = "", []
        kb_available = bool(
            (kb_text or "").strip()
            and not kb_text.startswith("(Supermemory")
            and len(kb_text) >= 200
        )
        pdf_name = resolve_bio_pdf_filename(member, sources)

    new_content = stub_from_extraction(
        member=member,
        role=org_role,
        pdf_filename=pdf_name,
        kb_text=kb_text if inline_required else "",
        kb_available=kb_available,
        inline_required=inline_required,
        extracted=extracted,
    )
    if not new_content.strip() or new_content.strip() == body.strip():
        return None
    return new_content, [
        f"Bio narrative not grounded in 04_Bio — reverted {member} to designer-note stub",
    ]


async def run_compliance_fabrication_repairs(
    draft: ProposalDraft,
    *,
    rfp_text: str = "",
    evidence_text: str = "",
) -> tuple[ProposalDraft, list[str]]:
    """Principle-based evidence grounding — runs on every Complete Scan."""
    from app.services.evidence_trust.legal_attestation_gate import (
        apply_legal_attestation_gates,
    )
    from app.services.proposal_kb_fact_checker import _member_name_from_bio_section
    from app.services.proposal_scan_fact_repairs import parse_org_chart_roles
    from app.services.proposal_sections_graph import _fetch_member_bio_kb
    from app.services.proposal_capability_bio_grounding import (
        align_bio_education_deterministically,
        align_bio_years_deterministically,
        person_name_from_tab_title,
    )

    logs: list[str] = []

    draft, att_report = apply_legal_attestation_gates(
        draft,
        rfp_context=rfp_text,
        evidence_text=evidence_text,
    )
    logs.extend(att_report.logs)

    from app.services.proposal_scan_insurance_certification import (
        gate_draft_insurance_certifications,
    )

    draft, ins_logs, ins_human = gate_draft_insurance_certifications(draft)
    logs.extend(ins_logs)
    for gap in ins_human:
        logs.append(f"HUMAN_GAP: {gap}")

    org_roles = parse_org_chart_roles(draft)
    sections: list[ProposalSection] = []
    changed = att_report.procurement_flags > 0 or bool(att_report.logs)

    for section in draft.sections:
        body = section.content or ""
        sid = section.id or ""
        title_cf = (section.title or "").casefold()
        new_body = body
        section_logs: list[str] = []

        if sid.startswith("section-2-bio-") and sid != "section-2-bio-placeholder":
            member = _member_name_from_bio_section(section.title or "")
        else:
            member = person_name_from_tab_title(section.title or "")
        if member:
            org_role = org_roles.get(member.casefold(), "")
            if not org_role:
                key = member.casefold()
                for name, role in org_roles.items():
                    parts = key.split()
                    if len(parts) >= 2 and parts[0] in name and parts[-1] in name:
                        org_role = role
                        break

            fixed_role, role_logs = repair_bio_role_from_org_chart(
                new_body,
                member_name=member,
                org_roles=org_roles,
            )
            if fixed_role != new_body:
                new_body = fixed_role
                section_logs.extend(role_logs)

            kb_text = ""
            try:
                kb_text, _ = await _fetch_member_bio_kb(member)
            except Exception as exc:  # noqa: BLE001
                logger.debug("Bio KB for grounding %s: %s", member, exc)

            if kb_text:
                aligned, year_logs = align_bio_years_deterministically(new_body, kb_text)
                aligned, edu_logs = align_bio_education_deterministically(
                    aligned, kb_text, member=member
                )
                if year_logs or edu_logs:
                    new_body = aligned
                    section_logs.extend(year_logs + edu_logs)

            if bio_narrative_ungrounded(new_body, kb_text):
                rebuilt = await _rebuild_bio_stub(
                    section.model_copy(update={"content": new_body}),
                    member=member,
                    org_role=org_role,
                    rfp_text=rfp_text,
                )
                if rebuilt:
                    new_body, rebuild_logs = rebuilt
                    section_logs.extend(rebuild_logs)

        if any(
            k in title_cf
            for k in (
                "insurance",
                "certification",
                "business information",
                "submission",
                "compliance",
                "exception",
            )
        ) or sid.startswith("section-1-"):
            fixed_ent, ent_logs = scrub_ungrounded_named_entities(
                new_body,
                evidence_text=evidence_text,
            )
            if fixed_ent != new_body:
                new_body = fixed_ent
                section_logs.extend(ent_logs)

        if new_body != body:
            changed = True
            sections.append(section.model_copy(update={"content": new_body}))
            for line in section_logs:
                logs.append(f"{section.title or sid}: {line}")
        else:
            sections.append(section)

    working = draft.model_copy(update={"sections": sections}) if changed else draft

    if (evidence_text or "").strip():
        from app.services.proposal_integrity_guards import (
            apply_case_study_metric_scrub_to_draft,
        )

        working, metric_logs = apply_case_study_metric_scrub_to_draft(
            working, source_text=evidence_text
        )
        if metric_logs:
            changed = True
            logs.extend(metric_logs)

    from app.services.proposal_capability_bio_grounding import (
        run_capability_bio_grounding,
    )

    grounded = await run_capability_bio_grounding(
        working,
        extra_evidence=evidence_text,
        rfp_text=rfp_text,
        rfp_id=draft.rfp_id,
        use_llm=True,
    )
    logs.extend(grounded.logs)
    if grounded.logs:
        changed = True
        working = grounded.draft

    if not changed and not logs:
        return draft, logs
    return working, logs
