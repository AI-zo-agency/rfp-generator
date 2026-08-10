"""Shared Evidence Decision Gate — when to call KB vs write.

Rule-based (no judge LLM). Used by drafting, adversarial repair, and section chat.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class EvidenceDecision(str, Enum):
    RETRIEVE_THEN_WRITE = "retrieve_then_write"
    WRITE_FROM_PLAN = "write_from_plan"
    WRITE_FROM_CANONICAL_BUDGET = "write_from_canonical_budget"
    VERIFY_FIELD = "verify_field"
    MANUAL_FILL = "manual_fill"
    DETERMINISTIC_CLEANUP = "deterministic_cleanup"


@dataclass(frozen=True)
class EvidenceGateResult:
    action: EvidenceDecision
    requires_retrieval: bool = False
    safe_plan_driven: bool = False
    reason: str = ""

    def as_log_dict(self) -> dict[str, Any]:
        return {
            "decision": self.action.value,
            "requires_retrieval": self.requires_retrieval,
            "safe_plan_driven": self.safe_plan_driven,
            "reason": self.reason,
        }


_METHODOLOGY_TITLE_TOKENS = (
    "technical approach",
    "methodology",
    "training",
    "timeline",
    "knowledge transfer",
    "transmittal",
    "project management",
    "qa process",
    "quality assurance",
)

_MONEY_MARKERS = (
    "budget",
    "money",
    "commission",
    "fee",
    "pricing",
    "cost / price",
    "cost/price",
    "of-2",
    "lump sum",
    "free_currency",
)

_LEGAL_MARKERS = (
    "e-verify",
    "e_verify",
    "conflict of interest",
    "penalty of perjury",
    "sworn",
    "affidavit",
    "attestation",
    "authorized signature",
    "offeror commitment",
)

_FACT_MARKERS = (
    "fabrication",
    "certification",
    "reference contact",
    "past performance",
    "portfolio",
    "experience",
    "qualification",
    "insurance",
    "wbenc",
    "wosb",
    "metric",
    "case study",
)

_CLEANUP_MARKERS = (
    "truncation",
    "note_leak",
    "note leak",
    "mid_sentence",
    "flag_for",
    "spotify",
    "fabricated_fact",
    "unverified_cert",
    "individual certs, not agency",
    "certification not in verified",
    "google ads certification",
    "meta certification",
)

_EMPTY_MARKERS = ("coverage.empty", "empty_section", "section is empty")


def _blob(*parts: str | None) -> str:
    return " ".join(p for p in parts if p).casefold()


def decide_evidence_action(
    *,
    section_id: str | None = None,
    section_title: str | None = None,
    finding: Any | None = None,
    user_ask: str | None = None,
) -> EvidenceGateResult:
    """Classify how content should be produced for a section or finding.

    Prefer finding signals when present; fall back to section title for drafting.
    """
    code = str(getattr(finding, "code", "") or "") if finding is not None else ""
    category = str(getattr(finding, "category", "") or "") if finding is not None else ""
    message = str(getattr(finding, "message", "") or "") if finding is not None else ""
    title = section_title or (
        str(getattr(finding, "section_title", "") or "") if finding is not None else ""
    )
    text = _blob(code, category, message, title, section_id, user_ask)

    if any(m in text for m in _CLEANUP_MARKERS) or category.casefold() in {
        "truncation",
        "note_leak",
        "fabricated_fact",
        "unverified_claim",
    }:
        result = EvidenceGateResult(
            action=EvidenceDecision.DETERMINISTIC_CLEANUP,
            reason="truncation_note_leak_or_cert_fabrication",
        )
        _log(section_id, result)
        return result

    if any(m in text for m in _LEGAL_MARKERS):
        result = EvidenceGateResult(
            action=EvidenceDecision.MANUAL_FILL,
            reason="legal_or_attestation_field",
        )
        _log(section_id, result)
        return result

    if any(m in text for m in _MONEY_MARKERS) or category.casefold() in {
        "budget",
        "money",
    }:
        result = EvidenceGateResult(
            action=EvidenceDecision.WRITE_FROM_CANONICAL_BUDGET,
            reason="money_or_budget_claim",
        )
        _log(section_id, result)
        return result

    if category.casefold() == "fabrication" or any(m in text for m in _FACT_MARKERS):
        # Methodology titles still win for empty process sections below; fabrication
        # always retrieves even if the title looks process-like.
        if category.casefold() == "fabrication" or "reference contact" in text:
            result = EvidenceGateResult(
                action=EvidenceDecision.RETRIEVE_THEN_WRITE,
                requires_retrieval=True,
                reason="fact_bound_or_reference",
            )
            _log(section_id, result)
            return result
        # Broad fact markers on methodology titles → still plan-driven for process
        if not any(tok in (title or "").casefold() for tok in _METHODOLOGY_TITLE_TOKENS):
            result = EvidenceGateResult(
                action=EvidenceDecision.RETRIEVE_THEN_WRITE,
                requires_retrieval=True,
                reason="fact_bound_section",
            )
            _log(section_id, result)
            return result

    if any(tok in (title or "").casefold() for tok in _METHODOLOGY_TITLE_TOKENS) or any(
        m in text for m in _EMPTY_MARKERS
    ):
        result = EvidenceGateResult(
            action=EvidenceDecision.WRITE_FROM_PLAN,
            safe_plan_driven=True,
            reason="methodology_or_empty_process",
        )
        _log(section_id, result)
        return result

    if "verify" in text or category.casefold() == "placeholder":
        # Generic VERIFY without legal/money → try retrieval before field VERIFY
        result = EvidenceGateResult(
            action=EvidenceDecision.RETRIEVE_THEN_WRITE,
            requires_retrieval=True,
            reason="unresolved_verify_try_kb",
        )
        _log(section_id, result)
        return result

    # Default for drafting unknown sections: retrieve rather than invent
    result = EvidenceGateResult(
        action=EvidenceDecision.RETRIEVE_THEN_WRITE,
        requires_retrieval=True,
        reason="default_prefer_evidence",
    )
    _log(section_id, result)
    return result


def gate_to_repair_mode(decision: EvidenceGateResult) -> str:
    """Map a gate decision onto existing RepairPlan.repair_mode values."""
    mapping = {
        EvidenceDecision.RETRIEVE_THEN_WRITE: "targeted_retrieval_then_rewrite",
        EvidenceDecision.WRITE_FROM_PLAN: "plan_driven_rewrite",
        EvidenceDecision.WRITE_FROM_CANONICAL_BUDGET: "budget_canonical_repair",
        EvidenceDecision.MANUAL_FILL: "protected_skip",
        EvidenceDecision.DETERMINISTIC_CLEANUP: "deterministic_cleanup",
        EvidenceDecision.VERIFY_FIELD: "targeted_retrieval_then_rewrite",
    }
    return mapping[decision.action]


def evidence_policy_prompt_stanza(
    decision: EvidenceGateResult,
    *,
    section_id: str = "",
) -> str:
    """One-line / short block injected into drafting and chat rewrite prompts.

    Wording deliberately avoids chat intent trigger tokens (edit verbs like
    "replace"/"remove", and the literal "[MANUAL FILL]" marker). Those used to
    live in this stanza and hijacked routing when the augmented string was
    classified as if the user had typed them.
    """
    sid = section_id or "section"
    if decision.action == EvidenceDecision.WRITE_FROM_PLAN:
        return (
            f"Evidence policy for {sid}: write_from_plan — compose from RFP + "
            "execution plan only. Do not invent company facts, certifications, "
            "client metrics, or portfolio scopes. Prefer field-level VERIFY marks "
            "over whole-section stubs."
        )
    if decision.action == EvidenceDecision.WRITE_FROM_CANONICAL_BUDGET:
        return (
            f"Evidence policy for {sid}: write_from_canonical_budget — use ledger / "
            "money slots / pricing guide only. Never invent fee totals."
        )
    if decision.action == EvidenceDecision.RETRIEVE_THEN_WRITE:
        return (
            f"Evidence policy for {sid}: retrieve_then_write — use only retrieved "
            "evidence snippets for company facts; if missing, narrow field-level "
            "VERIFY / owner-gap marks, do not fabricate."
        )
    if decision.action == EvidenceDecision.MANUAL_FILL:
        return (
            f"Evidence policy for {sid}: manual_fill — do not invent; emit "
            "owner-gap marks for legal/protected gaps."
        )
    if decision.action == EvidenceDecision.VERIFY_FIELD:
        return (
            f"Evidence policy for {sid}: verify_field — keep discrete VERIFY marks; "
            "do not overwrite the whole section with a stub."
        )
    return (
        f"Evidence policy for {sid}: deterministic_cleanup — strip truncation / "
        "note leaks without inventing new facts."
    )


def _log(section_id: str | None, result: EvidenceGateResult) -> None:
    logger.info(
        "evidence_gate decision=%s section_id=%s reason=%s retrieval=%s",
        result.action.value,
        section_id or "",
        result.reason,
        result.requires_retrieval,
    )


# Silence unused import warning if re is needed later for field VERIFY patterns
_ = re
