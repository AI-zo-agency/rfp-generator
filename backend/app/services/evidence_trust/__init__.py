"""Evidence trust: ClientList gates, provenance, claim validation, RFP hard facts."""

from app.services.evidence_trust.claim_validator import (
    validate_and_flag_section,
    validate_draft_sections,
)
from app.services.evidence_trust.client_list import (
    ClientListEntry,
    ClientListRegistry,
    parse_client_list_markdown,
)
from app.services.evidence_trust.flags import flag_confirm, verify_gap
from app.services.evidence_trust.gate import (
    ClaimIntent,
    GateDecision,
    GateResult,
    filter_evidence_hits,
    gate_client_for_claim,
)
from app.services.evidence_trust.provenance import (
    ProvenanceKind,
    classify_provenance,
    is_win_eligible,
)

__all__ = [
    "ClaimIntent",
    "ClientListEntry",
    "ClientListRegistry",
    "GateDecision",
    "GateResult",
    "ProvenanceKind",
    "classify_provenance",
    "filter_evidence_hits",
    "flag_confirm",
    "gate_client_for_claim",
    "is_win_eligible",
    "parse_client_list_markdown",
    "validate_and_flag_section",
    "validate_draft_sections",
    "verify_gap",
]
