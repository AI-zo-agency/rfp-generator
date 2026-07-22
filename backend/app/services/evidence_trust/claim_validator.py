"""Post-draft claim validator — strip inventions; FLAG mismatches."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.services.evidence_trust.client_list import ClientListRegistry
from app.services.evidence_trust.flags import (
    flag_claim_mismatch,
    flag_confirm,
    verify_gap,
)
from app.services.evidence_trust.gate import ClaimIntent, gate_client_for_claim, GateDecision


# Structured reference / contact blocks that look invented when client unknown.
_EMAIL_RE = re.compile(
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"
)
_PHONE_RE = re.compile(
    r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"
)
_REF_BLOCK_HINT = re.compile(
    r"(?i)(reference|client contact|point of contact|references?\s*:)"
)


@dataclass
class ClaimValidationReport:
    flags_inserted: int = 0
    blocks_replaced: int = 0
    clients_flagged: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _mentioned_clients(text: str, registry: ClientListRegistry) -> list[str]:
    found: list[str] = []
    cf = text.casefold()
    ranked = sorted(registry.entries, key=lambda e: len(e.name), reverse=True)
    for entry in ranked:
        if entry.name.casefold() in cf:
            if entry.name not in found:
                found.append(entry.name)
    return found


def _infer_claims_from_text(text: str) -> list[str]:
    cf = text.casefold()
    claims: list[str] = []
    if re.search(r"\b(website|web\s*site|web\s*build|redesign(ed)?\s+the\s+site)\b", cf):
        claims.append("website_build")
    if re.search(r"\b(leisure|visitor\s+economy|destination\s+brand)\b", cf):
        claims.append("tourism_leisure")
    if re.search(r"\b(meeting|conference|mci|planner)\b", cf):
        claims.append("tourism_mci")
    if re.search(r"\b(brand\s+identity|rebrand|branding)\b", cf):
        claims.append("brand")
    return claims or ["experience"]


def validate_and_flag_section(
    content: str,
    *,
    registry: ClientListRegistry,
    slot: str = "experience",
    allowed_client_names: set[str] | None = None,
) -> tuple[str, ClaimValidationReport]:
    """Scan prose; FLAG Confirm/mismatches; replace invented reference blocks."""
    report = ClaimValidationReport()
    text = content or ""
    if not text.strip():
        return text, report

    allowed = {n.casefold() for n in (allowed_client_names or set())}
    mentions = _mentioned_clients(text, registry)
    out = text

    for client in mentions:
        entry = registry.find(client)
        if entry is None:
            continue
        if entry.is_confirm:
            tag = flag_confirm(entry.name)
            if tag not in out:
                out = f"{tag}\n\n{out}"
                report.flags_inserted += 1
                report.clients_flagged.append(entry.name)
                report.notes.append(f"Confirm gate: {entry.name}")
            # Strip settled-tone by wrapping first occurrence note already inserted
            continue

        for claim in _infer_claims_from_text(out):
            intent = ClaimIntent(slot=slot, claim=claim)
            result = gate_client_for_claim(client, registry=registry, intent=intent)
            if result.decision == GateDecision.BLOCK_WORK_TYPE and entry:
                tag = flag_claim_mismatch(entry.name, claim, entry.work_type)
                if tag not in out:
                    out = f"{tag}\n\n{out}"
                    report.flags_inserted += 1
                    report.clients_flagged.append(entry.name)
                    report.notes.append(f"Work-type mismatch: {entry.name}/{claim}")
                break

        if allowed and client.casefold() not in allowed and entry and entry.is_public_yes:
            # Named but not in allowed evidence set for this draft
            tag = verify_gap(
                slot,
                f"{client} named but not in gated evidence set for this draft",
            )
            if tag not in out:
                out = f"{tag}\n\n{out}"
                report.flags_inserted += 1
                report.notes.append(f"Not in allowed evidence: {client}")

    # Invented reference package: emails/phones for clients not on list / Confirm
    if _REF_BLOCK_HINT.search(out) and (_EMAIL_RE.search(out) or _PHONE_RE.search(out)):
        suspicious = False
        for client in mentions:
            entry = registry.find(client)
            if entry is None or entry.is_confirm:
                suspicious = True
                break
        # Known fabricated tourism refs pattern
        for fake in (
            "travel oregon",
            "visit bend",
            "city of sisters",
            "queensland tourism",
            "tourism fiji",
        ):
            if fake in out.casefold():
                suspicious = True
                break
        if suspicious or (not mentions and _EMAIL_RE.search(out)):
            replacement = verify_gap(
                "references",
                "no verified ClientList/KB match for reference contacts; "
                "do not invent names or emails — provide verified contacts only",
            )
            # Replace dense reference-looking section heuristically
            out = replacement + "\n\n" + _REF_BLOCK_HINT.sub(
                "[references package removed — unverified]",
                out,
                count=1,
            )
            # Stronger: if multiple emails, wipe to VERIFY-only body for trust
            if len(_EMAIL_RE.findall(text)) >= 2:
                out = (
                    replacement
                    + "\n\n"
                    + "Previous draft contained unverified reference contacts and was cleared.\n"
                )
            report.blocks_replaced += 1
            report.notes.append("Cleared unverified reference contacts")

    return out, report


def validate_draft_sections(
    sections: list[tuple[str, str]],
    *,
    registry: ClientListRegistry,
    allowed_client_names: set[str] | None = None,
) -> list[tuple[str, str, ClaimValidationReport]]:
    """sections: list of (section_id, content) → updated triples."""
    results: list[tuple[str, str, ClaimValidationReport]] = []
    for section_id, content in sections:
        updated, report = validate_and_flag_section(
            content,
            registry=registry,
            slot=section_id,
            allowed_client_names=allowed_client_names,
        )
        results.append((section_id, updated, report))
    return results
