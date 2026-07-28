"""Build a FactLedger from typed claim candidates — never silently collapse conflicts."""

from __future__ import annotations

import logging
from collections import defaultdict

from app.models.fact_ledger import (
    ClaimClass,
    FactLedger,
    FactLedgerOverride,
    LedgerClaim,
    LedgerClient,
    LedgerCompany,
    LedgerPerson,
)
from app.services.fact_ledger_overrides import (
    apply_overrides_to_claims,
    load_fact_ledger_overrides,
)

logger = logging.getLogger(__name__)

# Numeric equality tolerance for years / counts (35 vs 35.0 vs "35+").
_NUMBER_EQ_TOL = 0.51


def build_fact_ledger(
    *,
    version: str,
    built_at: str,
    claims: list[LedgerClaim],
    people_names: dict[str, str] | None = None,
    clients: list[LedgerClient] | None = None,
    company: LedgerCompany | None = None,
    overrides: list[FactLedgerOverride] | None = None,
    load_default_overrides: bool = False,
) -> FactLedger:
    """Assemble ledger + blocking_conflicts for same subject/field divergent numbers.

    Conflicts are never silently collapsed. Pass *overrides* (or
    load_default_overrides=True) to resolve to one authoritative value while
    leaving alternate numbers in the KB untouched.
    """
    people_names = people_names or {}
    resolved_overrides = list(overrides) if overrides is not None else []
    if overrides is None and load_default_overrides:
        resolved_overrides = load_fact_ledger_overrides()

    claim_list, resolution_notes = apply_overrides_to_claims(
        list(claims), resolved_overrides
    )
    conflicts = detect_blocking_conflicts(claim_list, people_names=people_names)

    people = _people_from_claims(claim_list, people_names)
    company_model = company or _company_from_claims(claim_list)
    client_list = list(clients or [])

    ledger = FactLedger(
        version=version,
        builtAt=built_at,
        people=people,
        company=company_model,
        clients=client_list,
        claims=claim_list,
        blockingConflicts=conflicts,
        resolutionNotes=resolution_notes,
    )
    logger.info(
        "fact_ledger_built version=%s claims=%s people=%s blocking_conflicts=%s "
        "resolution_notes=%s",
        version,
        len(claim_list),
        len(people),
        len(conflicts),
        len(resolution_notes),
    )
    return ledger


def detect_blocking_conflicts(
    claims: list[LedgerClaim],
    *,
    people_names: dict[str, str] | None = None,
) -> list[str]:
    """Same (subject_id, claim_class, field_name) with divergent value_number → conflict."""
    people_names = people_names or {}
    groups: dict[tuple[str, str, str], list[LedgerClaim]] = defaultdict(list)
    for claim in claims:
        if claim.value_number is None:
            continue
        key = (claim.subject_id, claim.claim_class.value, claim.field_name)
        groups[key].append(claim)

    conflicts: list[str] = []
    for (subject_id, claim_class, field_name), group in groups.items():
        nums = [float(c.value_number) for c in group if c.value_number is not None]
        if len(nums) < 2:
            continue
        base = nums[0]
        if all(abs(n - base) <= _NUMBER_EQ_TOL for n in nums[1:]):
            continue
        label = people_names.get(subject_id) or subject_id
        unique = sorted({round(n, 2) for n in nums})
        conflicts.append(
            f"{claim_class} conflict for {label} ({field_name}): "
            + " vs ".join(str(u) for u in unique)
            + f" across {len(group)} sources — add a Fact Ledger override or resolve in KB"
        )
    return conflicts


def _people_from_claims(
    claims: list[LedgerClaim],
    people_names: dict[str, str],
) -> list[LedgerPerson]:
    by_id: dict[str, list[str]] = defaultdict(list)
    for claim in claims:
        if claim.subject_type != "person":
            continue
        by_id[claim.subject_id].append(claim.claim_id)
    people: list[LedgerPerson] = []
    for person_id, claim_ids in sorted(by_id.items()):
        people.append(
            LedgerPerson(
                personId=person_id,
                name=people_names.get(person_id) or person_id,
                claims=claim_ids,
            )
        )
    return people


def _company_from_claims(claims: list[LedgerClaim]) -> LedgerCompany:
    emp_id: str | None = None
    cert_ids: list[str] = []
    for claim in claims:
        if claim.subject_type != "company":
            continue
        if claim.claim_class == ClaimClass.EMPLOYEE_COUNT and emp_id is None:
            emp_id = claim.claim_id
        if claim.claim_class == ClaimClass.CERTIFICATION:
            cert_ids.append(claim.claim_id)
    return LedgerCompany(
        employeeCountClaimId=emp_id,
        certificationClaimIds=cert_ids,
    )
