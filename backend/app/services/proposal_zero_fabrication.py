"""Deterministic anti-fabrication + budget-canonical guards for proposal manuscripts.

Runs after drafting, budget incorporate, and senior editor so invented phase tables,
wrong reference phones, internal flags, and unverified qual content cannot ship.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from app.models.proposal import ProposalBudget, ProposalDraft, ProposalResearchCache

logger = logging.getLogger(__name__)


@dataclass
class ZeroFabricationReport:
    logs: list[str] = field(default_factory=list)
    phase_table_conflicts: list[str] = field(default_factory=list)
    budget_mismatch_count: int = 0


def _phase_amount_map(body: str) -> dict[str, str] | None:
    """Extract phase → $amount mapping from fee/disbursement tables."""
    text = body or ""
    if not re.search(
        r"(?i)(?:fee detail|disbursement|budget allocation|milestone|payment schedule|proposed investment)",
        text,
    ):
        return None
    mapping: dict[str, str] = {}
    skip_heads = {"phase", "phase / milestone", "---", "total", "**total**", "deliverable"}
    for line in text.splitlines():
        if not line.strip().startswith("|"):
            continue
        cols = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cols) < 2:
            continue
        phase = re.sub(r"\*+", "", cols[0]).strip().casefold()
        if not phase or phase in skip_heads:
            continue
        amount_match = re.search(r"\$[\d,]+(?:\.\d{1,2})?", cols[-1])
        if amount_match:
            mapping[phase] = amount_match.group(0)
    return mapping if len(mapping) >= 2 else None


def detect_contradictory_phase_tables(draft: ProposalDraft) -> list[str]:
    """Flag when budget-related sections disagree on phase dollar amounts."""
    section_maps: list[tuple[str, dict[str, str]]] = []
    for section in draft.sections or []:
        amap = _phase_amount_map(section.content or "")
        if amap:
            title = section.title or section.id or "section"
            section_maps.append((title, amap))

    conflicts: list[str] = []
    for i, (title_a, map_a) in enumerate(section_maps):
        for title_b, map_b in section_maps[i + 1 :]:
            shared = set(map_a) & set(map_b)
            for phase in shared:
                if map_a[phase] != map_b[phase]:
                    conflicts.append(
                        f"Phase {phase!r}: {title_a} has {map_a[phase]} vs "
                        f"{title_b} has {map_b[phase]}"
                    )
            # Whole-table mismatch when both are full breakdowns (≥3 phases)
            if len(map_a) >= 3 and len(map_b) >= 3 and map_a != map_b and not shared:
                conflicts.append(
                    f"Contradictory phase tables: {title_a!r} vs {title_b!r}"
                )
    return conflicts


def apply_zero_fabrication_guards(
    draft: ProposalDraft,
    *,
    research: ProposalResearchCache | None = None,
    budget: ProposalBudget | None = None,
    rfp_text: str = "",
    label: str = "zero-fabrication",
) -> tuple[ProposalDraft, ZeroFabricationReport]:
    """Run all deterministic anti-fabrication scrubs in canonical order."""
    report = ZeroFabricationReport()
    if not draft.sections:
        return draft, report

    from app.services.proposal_integrity_guards import (
        apply_manuscript_integrity_guards,
        apply_reference_contact_evidence_guard,
    )

    draft, integrity_logs = apply_manuscript_integrity_guards(draft)
    for line in integrity_logs:
        report.logs.append(f"{label}: integrity — {line}")

    draft, phone_logs = apply_reference_contact_evidence_guard(draft, research)
    for line in phone_logs:
        report.logs.append(f"{label}: reference phone — {line}")

    resolved_budget = budget or (research.budget if research else None)
    if resolved_budget and resolved_budget.line_items:
        from app.services.proposal_budget_content import (
            reconcile_draft_budget_summaries,
            sync_phase_budget_tables_across_draft,
        )

        draft, sync_logs = sync_phase_budget_tables_across_draft(draft, resolved_budget)
        for line in sync_logs:
            report.logs.append(f"{label}: phase table sync — {line}")
        draft, reconciled = reconcile_draft_budget_summaries(draft, resolved_budget)
        if reconciled:
            report.logs.append(
                f"{label}: reconciled budget summary prose in {reconciled} section(s)"
            )

        from app.services.proposal_pricing_sync_repair import scrub_invented_ceiling_claims

        draft, scrubbed = scrub_invented_ceiling_claims(draft, resolved_budget)
        if scrubbed:
            report.logs.append(
                f"{label}: scrubbed invented ceiling claims in {scrubbed} section(s)"
            )

        from app.services.proposal_budget_sync import collect_deterministic_budget_mismatches

        mismatches = collect_deterministic_budget_mismatches(draft, resolved_budget)
        report.budget_mismatch_count = len(mismatches)
        for item in mismatches[:6]:
            note = getattr(item, "note", None) or str(item)
            report.logs.append(f"{label}: budget mismatch — {note[:160]}")

    try:
        from app.services.proposal_fulfill_fabrication_guard import (
            repair_fabricated_qualifications,
        )

        draft, fab_logs, _human = repair_fabricated_qualifications(
            draft, research, registry=None
        )
        for line in fab_logs:
            report.logs.append(f"{label}: fabrication guard — {line}")
    except Exception as exc:  # noqa: BLE001
        logger.warning("%s fabrication guard skipped: %s", label, exc)

    try:
        from app.services.proposal_scan_insurance_certification import (
            gate_draft_insurance_certifications,
        )

        draft, ins_logs, ins_human = gate_draft_insurance_certifications(draft)
        for line in ins_logs:
            report.logs.append(f"{label}: insurance cert — {line}")
        for gap in ins_human:
            report.logs.append(f"{label}: HUMAN_GAP: {gap}")
    except Exception as exc:  # noqa: BLE001
        logger.warning("%s insurance certification gate skipped: %s", label, exc)

    conflicts = detect_contradictory_phase_tables(draft)
    report.phase_table_conflicts = conflicts
    for line in conflicts:
        report.logs.append(f"{label}: {line}")

    return draft, report


async def apply_structure_coverage_pass(
    draft: ProposalDraft,
    *,
    rfp,
    rfp_text: str,
    research: ProposalResearchCache | None,
    use_llm: bool = True,
) -> tuple[ProposalDraft, list[str]]:
    """Ensure RFP-scored tabs exist; reframe VERIFY stubs when LLM available."""
    from app.services.proposal_budget_content import find_budget_section_index
    from app.services.proposal_fulfill_rfp_structure import run_rfp_structure_alignment_pass

    budget_idx = find_budget_section_index(draft.sections)
    skip: set[str] = set()
    if budget_idx is not None:
        skip.add(draft.sections[budget_idx].id or "")

    updated, logs, _human = await run_rfp_structure_alignment_pass(
        draft=draft,
        rfp=rfp,
        rfp_text=rfp_text,
        research=research,
        skip_section_ids=skip,
        use_llm=use_llm,
    )
    return updated, logs
