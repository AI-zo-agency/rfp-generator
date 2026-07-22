"""Hard filters: Public Confirm, provenance, claim↔work-type."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.services.evidence_trust.client_list import ClientListRegistry
from app.services.evidence_trust.flags import (
    flag_claim_mismatch,
    flag_confirm,
    flag_provenance,
    verify_gap,
)
from app.services.evidence_trust.provenance import (
    provenance_block_reason,
    is_win_eligible,
)


class GateDecision(str, Enum):
    ALLOW = "allow"
    BLOCK_CONFIRM = "block_confirm"
    BLOCK_WORK_TYPE = "block_work_type"
    BLOCK_PROVENANCE = "block_provenance"
    BLOCK_UNKNOWN_CLIENT = "block_unknown_client"
    EMPTY = "empty"


@dataclass
class ClaimIntent:
    """What the writer is trying to assert."""

    slot: str = "experience"
    claim: str = "experience"  # e.g. website_build, tourism_leisure
    require_win_provenance: bool = True
    allow_unknown_clients: bool = False  # False = never name clients not on ClientList


@dataclass
class GateResult:
    decision: GateDecision
    allowed_hits: list[dict[str, Any]] = field(default_factory=list)
    rejected: list[tuple[str, str]] = field(default_factory=list)  # (label, reason)
    gap_tag: str | None = None  # VERIFY/FLAG to insert when empty


def _hit_label(hit: dict[str, Any]) -> str:
    metadata = hit.get("metadata") if isinstance(hit.get("metadata"), dict) else {}
    return str(
        hit.get("source")
        or hit.get("customId")
        or metadata.get("fileName")
        or metadata.get("title")
        or hit.get("title")
        or hit.get("id")
        or "document"
    )


def _guess_client_name(hit: dict[str, Any], registry: ClientListRegistry) -> str | None:
    label = _hit_label(hit)
    body = str(
        hit.get("excerpt")
        or hit.get("content")
        or hit.get("memory")
        or hit.get("chunk")
        or hit.get("text")
        or hit.get("snippet")
        or ""
    )[:1500]
    blob = f"{label}\n{body}"
    # Prefer longest client name match to avoid City of Bend vs Bend Water
    ranked = sorted(registry.entries, key=lambda e: len(e.name), reverse=True)
    for entry in ranked:
        if entry.name.casefold() in blob.casefold():
            return entry.name
    # Title-only soft match via registry.find on filename tokens
    found = registry.find(label)
    return found.name if found else None


def gate_client_for_claim(
    client_name: str,
    *,
    registry: ClientListRegistry,
    intent: ClaimIntent,
) -> GateResult:
    entry = registry.find(client_name)
    if entry is None:
        if intent.allow_unknown_clients:
            return GateResult(decision=GateDecision.ALLOW)
        tag = verify_gap(
            intent.slot,
            f"{client_name} not on {registry.source_label}; "
            "confirm name and permission with Ella before including",
        )
        return GateResult(
            decision=GateDecision.BLOCK_UNKNOWN_CLIENT,
            rejected=[(client_name, "not on approved ClientList")],
            gap_tag=tag,
        )
    if entry.is_confirm:
        return GateResult(
            decision=GateDecision.BLOCK_CONFIRM,
            rejected=[(entry.name, "Public=Confirm")],
            gap_tag=flag_confirm(entry.name),
        )
    if not entry.is_public_yes:
        return GateResult(
            decision=GateDecision.BLOCK_CONFIRM,
            rejected=[(entry.name, f"Public={entry.public}")],
            gap_tag=flag_confirm(entry.name),
        )
    if intent.claim and intent.claim not in {"experience", "case_study", ""}:
        if not registry.work_type_supports_claim(entry, intent.claim):
            return GateResult(
                decision=GateDecision.BLOCK_WORK_TYPE,
                rejected=[(entry.name, f"work type '{entry.work_type}' lacks '{intent.claim}'")],
                gap_tag=flag_claim_mismatch(entry.name, intent.claim, entry.work_type),
            )
    return GateResult(decision=GateDecision.ALLOW)

def filter_evidence_hits(
    hits: list[dict[str, Any]],
    *,
    registry: ClientListRegistry,
    intent: ClaimIntent,
) -> GateResult:
    """Best-effort filter: keep only public + claim-matched + win-eligible hits."""
    allowed: list[dict[str, Any]] = []
    rejected: list[tuple[str, str]] = []

    for hit in hits:
        label = _hit_label(hit)
        if intent.require_win_provenance and not is_win_eligible(hit):
            reason = provenance_block_reason(hit) or "not win-eligible provenance"
            rejected.append((label, reason))
            continue

        client = _guess_client_name(hit, registry)
        if client:
            client_gate = gate_client_for_claim(client, registry=registry, intent=intent)
            if client_gate.decision != GateDecision.ALLOW:
                reason = client_gate.rejected[0][1] if client_gate.rejected else client_gate.decision.value
                rejected.append((f"{label} ({client})", reason))
                continue
        elif not intent.allow_unknown_clients:
            # Experience hit with no ClientList client — keep only if no client-like claim
            # For strict experience slots, drop unnamed/unknown.
            if intent.claim in {"website", "website_build", "tourism_leisure", "tourism_mci"}:
                rejected.append((label, "no ClientList client matched in hit"))
                continue

        allowed.append(hit)

    if allowed:
        return GateResult(
            decision=GateDecision.ALLOW,
            allowed_hits=allowed,
            rejected=rejected,
        )

    reasons: list[str] = []
    for _label, reason in rejected[:6]:
        if reason not in reasons:
            reasons.append(reason)
    reason_text = (
        f"no verified source after ClientList/provenance gate for claim '{intent.claim}'"
    )
    if reasons:
        reason_text += "; rejected: " + "; ".join(reasons)
    # Prefer FLAG if all rejects were Confirm
    if rejected and all("Confirm" in r or "confirm" in r.casefold() for _, r in rejected):
        gap = flag_confirm(rejected[0][0])
    elif rejected and any("07_FIN" in r or "finalist" in r.casefold() for _, r in rejected):
        gap = flag_provenance(rejected[0][0], rejected[0][1])
    else:
        gap = verify_gap(intent.slot, reason_text)

    return GateResult(
        decision=GateDecision.EMPTY,
        allowed_hits=[],
        rejected=rejected,
        gap_tag=gap,
    )
