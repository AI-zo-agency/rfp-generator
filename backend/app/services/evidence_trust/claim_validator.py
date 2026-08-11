"""Post-draft claim validator — correct inventions; FLAG only when still unresolved."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.services.evidence_trust.client_list import (
    CLAIM_WORK_TYPE_ALIASES,
    ClientListRegistry,
)
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
    # Ignore existing FLAG tags so "tourism_mci" inside a FLAG cannot re-trigger.
    cleaned = "\n\n".join(
        p for p in (text or "").split("\n\n") if not p.strip().startswith("[FLAG:")
    )
    cf = cleaned.casefold()
    claims: list[str] = []
    # Substring checks against known claim vocabulary (ClientList aliases).
    if (
        "website" in cf
        or "web site" in cf
        or "web build" in cf
        or "mortgage calculator" in cf
    ):
        claims.append("website_build")
    if "leisure" in cf or "visitor economy" in cf or "destination brand" in cf:
        claims.append("tourism_leisure")
    # Do NOT treat bare "meeting" / "conference" / "planner" as MCI — that
    # false-positives on "media planners", "team meeting", "press conference".
    if (
        "meeting planner" in cf
        or "conference planner" in cf
        or "meetings and conferences" in cf
        or "meeting and conference" in cf
        # Word "MCI" only — not the substring inside tourism_mci FLAG tags.
        or re.search(r"(?<![a-z_])mci(?![a-z_])", cf) is not None
    ):
        claims.append("tourism_mci")
    if "brand identity" in cf or "rebrand" in cf or "branding" in cf:
        claims.append("brand")
    return claims or ["experience"]


def _split_sentences(text: str) -> list[str]:
    """Split on sentence terminators; ignore cents decimals inside money."""
    if not text:
        return []
    out: list[str] = []
    buf = ""
    i = 0
    while i < len(text):
        ch = text[i]
        buf += ch
        if ch in ".!?":
            is_cents = (
                ch == "."
                and i + 1 < len(text)
                and text[i + 1].isdigit()
                and len(buf) >= 2
                and buf[-2].isdigit()
            )
            if not is_cents:
                piece = buf.strip()
                if piece:
                    out.append(piece)
                buf = ""
        i += 1
    trailing = buf.strip()
    if trailing:
        out.append(trailing)
    return out


def _blocked_claim_markers(claim: str, work_type: str) -> list[str]:
    """Markers that assert ``claim`` but are not licensed by ClientList work_type."""
    work_cf = (work_type or "").casefold()
    aliases = CLAIM_WORK_TYPE_ALIASES.get(claim, ())
    markers = [a for a in aliases if a not in work_cf]
    # Extra website-build phrasing that appears in fabricated case studies.
    if claim in {"website", "website_build"}:
        for extra in (
            "mortgage calculator",
            "mortgage calculators",
            "custom-programmed",
            "custom programmed",
            "hits on our website",
            "on our website",
            "professional website",
            "web-related",
        ):
            if extra not in work_cf and extra not in markers:
                markers.append(extra)
    # Bare "meeting"/"conference" false-positive on "meeting brand guidelines"
    # / "press conference". Prefer multi-word MCI phrases only.
    if claim == "tourism_mci":
        markers = [
            m
            for m in markers
            if m
            not in {
                "meeting",
                "conference",
            }
        ]
        for extra in (
            "meeting planner",
            "conference planner",
            "meetings and conferences",
            "meeting and conference",
            "mci",
            "events strategy",
        ):
            if extra not in work_cf and extra not in markers:
                markers.append(extra)
    return markers


def _sentence_about_client(sentence: str, client: str) -> bool:
    s_cf = sentence.casefold()
    client_cf = client.casefold()
    if client_cf and client_cf in s_cf:
        return True
    first_person = (
        "we created",
        "we built",
        "we designed",
        "we launched",
        "we developed",
        "we delivered",
        "our website",
    )
    return any(p in s_cf for p in first_person)


def rewrite_blocked_claim_prose(
    text: str,
    *,
    client: str,
    claim: str,
    work_type: str,
) -> tuple[str, int]:
    """Drop sentences asserting a ClientList-blocked claim; keep accurate work-type prose.

    Complete & Clean used to FLAG mismatches then strip the flags, leaving the
    fabricated claim intact. This rewrites the claim itself using ClientList
    work_type as the source of truth — no topic keyword heuristics beyond the
    existing CLAIM_WORK_TYPE_ALIASES vocabulary.
    """
    if not text or not client or not claim:
        return text, 0
    markers = _blocked_claim_markers(claim, work_type)
    if not markers:
        return text, 0

    removed = 0
    kept_paras: list[str] = []
    for para in (text or "").split("\n\n"):
        raw = para.strip()
        if not raw:
            continue
        # Drop prior mismatch flags for this client/claim — we are fixing prose.
        if raw.startswith("[FLAG:") and "not supported" in raw.casefold():
            if client.casefold() in raw.casefold() or claim in raw.casefold():
                removed += 1
                continue
        sentences = _split_sentences(raw)
        kept_sents: list[str] = []
        for sent in sentences:
            s_cf = sent.casefold()
            has_marker = any(m in s_cf for m in markers)
            if has_marker and _sentence_about_client(sent, client):
                removed += 1
                continue
            # Case-study body about this client: also drop bare website markers.
            if (
                has_marker
                and client.casefold() in (text or "").casefold()
                and any(
                    m in s_cf
                    for m in (
                        "website",
                        "web site",
                        "mortgage calculator",
                        "custom-programmed",
                    )
                )
            ):
                removed += 1
                continue
            kept_sents.append(sent)
        if kept_sents:
            kept_paras.append(" ".join(kept_sents).strip())

    if not removed:
        return text, 0

    body = "\n\n".join(kept_paras).strip()
    # Anchor the section to documented work type so we don't leave a hole.
    wt = (work_type or "").strip()
    if wt and client.casefold() in (text or "").casefold():
        anchor = f"Documented work for {client} centers on {wt}."
        if anchor.casefold() not in body.casefold():
            body = f"{body}\n\n{anchor}".strip() if body else anchor
    ending = "\n" if text.endswith("\n") else ""
    return body + ending, removed


def _strip_flag_paragraphs(text: str) -> str:
    kept: list[str] = []
    for para in (text or "").split("\n\n"):
        if para.strip().startswith("[FLAG:"):
            continue
        kept.append(para)
    return "\n\n".join(kept)


def _client_asserts_claim_markers(
    text: str,
    *,
    client: str,
    markers: list[str],
) -> bool:
    client_cf = (client or "").casefold()
    if not client_cf or not markers:
        return False
    # Never treat FLAG tags themselves as evidence the claim was asserted.
    body = _strip_flag_paragraphs(text)
    for sent in _split_sentences(body):
        s_cf = sent.casefold()
        if client_cf not in s_cf:
            continue
        if any(m in s_cf for m in markers):
            return True
    return False


def _drop_unasserted_claim_flags(text: str, registry: ClientListRegistry) -> str:
    """Remove [FLAG: claim …] paragraphs when the client never asserted that claim."""
    kept: list[str] = []
    flag_re = re.compile(
        r"^\[FLAG:\s*claim\s+'([^']+)'\s+not supported for\s+(.+?)\s*[—\-]",
        re.I,
    )
    body_for_assert = _strip_flag_paragraphs(text)
    for para in (text or "").split("\n\n"):
        raw = para.strip()
        if not raw.startswith("[FLAG:"):
            kept.append(para)
            continue
        m = flag_re.match(raw)
        if not m:
            kept.append(para)
            continue
        claim = (m.group(1) or "").strip()
        client = (m.group(2) or "").strip()
        entry = registry.find(client) if client else None
        work = entry.work_type if entry else ""
        markers = _blocked_claim_markers(claim, work)
        # Keep Confirm-style FLAGs and real mismatches; drop false positives.
        if entry and entry.is_confirm:
            kept.append(para)
            continue
        if _client_asserts_claim_markers(
            body_for_assert, client=client, markers=markers
        ):
            kept.append(para)
            continue
        # Stale / unasserted — drop.
    return "\n\n".join(p.strip() for p in kept if p.strip())


def validate_and_flag_section(
    content: str,
    *,
    registry: ClientListRegistry,
    slot: str = "experience",
    allowed_client_names: set[str] | None = None,
) -> tuple[str, ClaimValidationReport]:
    """Scan prose; correct work-type mismatches; FLAG only Confirm / unresolved."""
    report = ClaimValidationReport()
    text = content or ""
    if not text.strip():
        return text, report

    # Drop stale FLAGs left by earlier false-positive claim inference
    # (e.g. tourism_mci from "media planners") before re-validating.
    text = _drop_unasserted_claim_flags(text, registry)

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
            continue

        for claim in _infer_claims_from_text(out):
            intent = ClaimIntent(slot=slot, claim=claim)
            result = gate_client_for_claim(client, registry=registry, intent=intent)
            if result.decision == GateDecision.BLOCK_WORK_TYPE and entry:
                markers = _blocked_claim_markers(claim, entry.work_type)
                # Only rewrite/FLAG when this client sentence actually asserts
                # the blocked claim — inferred section-wide claims (e.g. bare
                # "media planners" → tourism_mci) must not pollute every client.
                if not _client_asserts_claim_markers(
                    out, client=entry.name, markers=markers
                ):
                    continue
                rewritten, n = rewrite_blocked_claim_prose(
                    out,
                    client=entry.name,
                    claim=claim,
                    work_type=entry.work_type,
                )
                if n > 0 and rewritten != out:
                    out = rewritten
                    report.blocks_replaced += n
                    report.notes.append(
                        f"Corrected {n} unsupported '{claim}' claim(s) for "
                        f"{entry.name} (work type: {entry.work_type})"
                    )
                    # Only FLAG if blocked markers still remain after rewrite.
                    still = _blocked_claim_markers(claim, entry.work_type)
                    if any(m in out.casefold() for m in still):
                        tag = flag_claim_mismatch(
                            entry.name, claim, entry.work_type
                        )
                        if tag not in out:
                            out = f"{tag}\n\n{out}"
                            report.flags_inserted += 1
                            report.clients_flagged.append(entry.name)
                    break
                tag = flag_claim_mismatch(entry.name, claim, entry.work_type)
                if tag not in out:
                    out = f"{tag}\n\n{out}"
                    report.flags_inserted += 1
                    report.clients_flagged.append(entry.name)
                    report.notes.append(
                        f"Work-type mismatch: {entry.name}/{claim}"
                    )
                break

        if allowed and client.casefold() not in allowed and entry and entry.is_public_yes:
            report.notes.append(
                f"Not in allowed evidence (no VERIFY tag inserted): {client}"
            )

    # Invented reference package: emails/phones for clients not on list / Confirm
    if _REF_BLOCK_HINT.search(out) and (_EMAIL_RE.search(out) or _PHONE_RE.search(out)):
        suspicious = False
        for client in mentions:
            entry = registry.find(client)
            if entry is None or entry.is_confirm:
                suspicious = True
                break
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
            out = replacement + "\n\n" + _REF_BLOCK_HINT.sub(
                "[references package removed — unverified]",
                out,
                count=1,
            )
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
