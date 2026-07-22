"""VERIFY / FLAG strings with explicit why-not-found reasons."""

from __future__ import annotations


def verify_gap(slot: str, reason: str) -> str:
    slot_s = (slot or "content").strip()
    reason_s = (reason or "no verified source found").strip().rstrip(".")
    return f"[VERIFY: {slot_s} — {reason_s}]"


def flag_confirm(client: str, *, owner: str = "Sonja") -> str:
    name = (client or "client").strip()
    return (
        f"[FLAG: Confirm with {owner} before naming — {name} "
        f"(ClientList Public=Confirm)]"
    )


def flag_claim_mismatch(client: str, claim: str, work_type: str) -> str:
    return (
        f"[FLAG: claim '{claim}' not supported for {client} — "
        f"ClientList work type is '{work_type}']"
    )


def flag_provenance(source: str, reason: str) -> str:
    return f"[FLAG: blocked source {source} — {reason}]"
