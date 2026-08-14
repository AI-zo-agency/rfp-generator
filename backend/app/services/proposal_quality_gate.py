"""The Complete & Scan review agent: one reviewer in three acts.

Act 1 verifies every fact-bound claim against the knowledge base. Act 2 scores the draft
the way the issuing agency will. Act 3 runs four detectors over the whole manuscript.
Every finding terminates in exactly one of three states — fixed, [MANUAL FILL], or
scored-against. Nothing exits silently; that invariant is what makes "nothing left"
checkable.

Two rules here are load-bearing and easy to get backwards:

1. Retrieval failure never deletes content. A Supermemory miss is a fact about
   retrieval, not about reality — timeouts, embedding misses, and un-ingested documents
   all produce empty results. Wiring deletion to that signal would strip true,
   defensible claims out of good proposals on a flaky run.
2. No evidence, no claim. If retrieval returns nothing supporting a fix, the patcher is
   forbidden from writing it and must emit [MANUAL FILL]. This is a precondition checked
   in code, not a sentence in a prompt the model can drift from.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from app.models.proposal import (
    ClaimVerdict,
    CriterionVerdict,
    GateTicket,
    ProposalDraft,
    ProposalResearchCache,
    QualityGateReport,
)
from app.models.rfp import RfpRecord

logger = logging.getLogger(__name__)

MAX_ROUNDS = 3


def _configured_max_rounds() -> int:
    raw = getattr(_settings(), "quality_gate_max_rounds", MAX_ROUNDS)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = MAX_ROUNDS
    return max(1, min(MAX_ROUNDS, value))

# A repair that cuts this much of a section is destroying content, not tightening it.
REGRESSION_SHRINK_RATIO = 0.75

# Tags that represent an unfilled obligation. Losing one during a repair hides a
# submission gap, which is worse than the gap itself.
_OBLIGATION_TAGS = ("[MANUAL FILL", "[VERIFY")


def _norm(text: str) -> str:
    return " ".join((text or "").split())


def _settings() -> Any:
    """Settings, or a permissive stand-in if config cannot be loaded."""
    try:
        from app.core.config import settings

        return settings
    except Exception:  # noqa: BLE001
        return object()


# --------------------------------------------------------------------------- control


def dedupe_tickets(
    tickets: list[GateTicket], seen: set[tuple[str, str]]
) -> list[GateTicket]:
    """Drop tickets already opened for this (section, code). Mutates `seen`.

    Structural rather than substring-based: `_finding_family()` in the adversarial
    repair path collapses findings using hardcoded substrings and so silently fails to
    group anything nobody listed. A tuple key cannot go stale.
    """
    out: list[GateTicket] = []
    for ticket in tickets:
        if ticket.key in seen:
            continue
        seen.add(ticket.key)
        out.append(ticket)
    return out


def is_regression(*, before: str, after: str) -> bool:
    """True when a patch made the section worse rather than better."""
    before_n, after_n = _norm(before), _norm(after)
    if not after_n and before_n:
        return True
    if before_n and len(after_n) < len(before_n) * REGRESSION_SHRINK_RATIO:
        return True
    # An obligation present before must still be present after.
    for tag in _OBLIGATION_TAGS:
        if before_n.count(tag) > after_n.count(tag):
            return True
    return False


def is_oscillating(*, history: list[str], candidate: str) -> bool:
    """True when this round is undoing an earlier one.

    Slop cuts a qualifier, consistency re-adds it, repetition flags it as restatement,
    and round 3 undoes round 2. Without this the loop burns its budget on a cycle.
    """
    target = _norm(candidate)
    return any(_norm(prior) == target for prior in history)


def may_write_claim(*, requires_evidence: bool, evidence: str) -> bool:
    """The no-fabrication precondition. Style fixes pass freely; assertions do not."""
    if not requires_evidence:
        return True
    return bool((evidence or "").strip())


@dataclass(frozen=True)
class ContradictionResolution:
    winner: str | None
    manual_fill: bool
    reason: str


def resolve_contradiction(
    *, values: list[str], verdict: ClaimVerdict | None
) -> ContradictionResolution:
    """Decide which of two conflicting values is true — or refuse to.

    The fix for a contradiction is never "pick one". If the knowledge base cannot say,
    both become [MANUAL FILL]: guessing is how a confident wrong number reaches an
    evaluator.
    """
    if verdict is not None and verdict.status == "contradicted" and verdict.corrected_value:
        return ContradictionResolution(
            winner=verdict.corrected_value,
            manual_fill=False,
            reason=verdict.evidence or "knowledge base",
        )
    return ContradictionResolution(
        winner=None,
        manual_fill=True,
        reason="knowledge base could not determine which value is correct",
    )


# ------------------------------------------------------------------------------ acts


def _sections_digest(draft: ProposalDraft, *, limit: int = 40_000) -> str:
    parts = [
        f"### [{s.id}] {s.title}\n{(s.content or '').strip()}" for s in draft.sections
    ]
    return "\n\n".join(parts)[:limit]


def batch_sections(
    sections: list[Any], *, limit: int = 24_000
) -> list[list[Any]]:
    """Pack sections into groups that fit one verifier call.

    A packing change, not a sampling change: every non-empty section lands in exactly
    one batch, so coverage is identical to one-call-per-section at a fraction of the
    calls. A section larger than the limit gets a batch to itself rather than being
    dropped for being too big.
    """
    batches: list[list[Any]] = []
    current: list[Any] = []
    used = 0
    for section in sections:
        content = (section.content or "").strip()
        if not content:
            continue
        size = len(content)
        if current and used + size > limit:
            batches.append(current)
            current, used = [], 0
        current.append(section)
        used += size
    if current:
        batches.append(current)
    return batches


def sections_to_examine(draft: ProposalDraft, *, changed: set[str] | None) -> list[Any]:
    """Round 1 sees everything; later rounds only re-examine what actually changed."""
    if changed is None:
        return list(draft.sections)
    return [s for s in draft.sections if s.id in changed]


async def _retrieve_evidence(section: Any) -> tuple[str, str]:
    from app.services.proposal_section_kb_evidence import (
        fetch_packed_section_kb_evidence,
    )

    try:
        evidence, _sources = await fetch_packed_section_kb_evidence(
            section_title=section.title or "",
            section_content=(section.content or "").strip(),
        )
        return section.id, evidence
    except Exception as exc:  # noqa: BLE001
        logger.warning("gate act1 retrieval failed section=%s: %s", section.id, exc)
        return section.id, ""


async def _retrieve_evidence_map(
    draft: ProposalDraft, *, only_sections: set[str] | None
) -> dict[str, str]:
    import asyncio

    settings = _settings()
    targets = sections_to_examine(draft, changed=only_sections)
    if not targets:
        return {}
    semaphore = asyncio.Semaphore(
        max(1, getattr(settings, "quality_gate_retrieval_concurrency", 6))
    )

    async def _guarded(section: Any) -> tuple[str, str]:
        async with semaphore:
            return await _retrieve_evidence(section)

    return dict(await asyncio.gather(*(_guarded(s) for s in targets)))


async def verify_fact_bound_claims(
    *,
    rfp_id: str,
    draft: ProposalDraft,
    research: ProposalResearchCache | None,
    only_sections: set[str] | None = None,
    evidence_by_section: dict[str, str] | None = None,
) -> list[ClaimVerdict]:
    """Act 1 — check every fact-bound claim against the KB, three-state.

    Batched and concurrent purely for cost and latency: the model still sees every
    section together with that section's own retrieved evidence.
    """
    import asyncio

    from app.services.proposal_langchain_agents import AgentRole, run_json_agent

    settings = _settings()
    targets = sections_to_examine(draft, changed=only_sections)
    batches = batch_sections(
        targets, limit=getattr(settings, "quality_gate_claim_batch_chars", 24_000)
    )
    if not batches:
        return []

    if evidence_by_section is None:
        evidence_by_section = await _retrieve_evidence_map(
            draft, only_sections=only_sections
        )
    else:
        evidence_by_section = dict(evidence_by_section)

    batch_sem = asyncio.Semaphore(
        max(1, getattr(settings, "quality_gate_verifier_batch_concurrency", 3))
    )

    async def _verify_batch(batch: list[Any]) -> list[ClaimVerdict]:
        async with batch_sem:
            parts: list[str] = []
            for section in batch:
                evidence = evidence_by_section.get(section.id, "")
                parts.append(
                    f"SECTION [{section.id}] {section.title}\n"
                    f"{(section.content or '').strip()[:12_000]}\n\n"
                    f"EVIDENCE FOR [{section.id}]:\n"
                    + (evidence[:8_000] if evidence else "(no evidence retrieved)")
                )
            user_content = (
                "Verify the fact-bound claims in each section below against that "
                "section's own evidence. Tag every claim with its sectionId.\n\n"
                + "\n\n---\n\n".join(parts)
            )
            try:
                raw, _ = await run_json_agent(AgentRole.CLAIM_VERIFIER, user_content)
                claims = raw.get("claims") if isinstance(raw.get("claims"), list) else []
            except Exception as exc:  # noqa: BLE001
                logger.warning("gate act1 verifier failed: %s", exc)
                return []

            out: list[ClaimVerdict] = []
            valid_ids = {s.id for s in batch}
            for item in claims:
                if not isinstance(item, dict):
                    continue
                sid = str(item.get("sectionId") or "")
                if sid not in valid_ids:
                    sid = batch[0].id if len(batch) == 1 else sid
                if not sid:
                    continue
                status = str(item.get("status") or "unresolved").strip().casefold()
                if status not in {"verified", "contradicted", "unresolved"}:
                    status = "unresolved"
                # Retrieval produced nothing for this section, so nothing can be
                # contradicted by it. Never let an empty KB read as disproof.
                if not (evidence_by_section.get(sid) or "").strip() and status == "contradicted":
                    status = "unresolved"
                out.append(
                    ClaimVerdict(
                        sectionId=sid,
                        claim=str(item.get("claim") or "")[:600],
                        status=status,  # type: ignore[arg-type]
                        evidence=str(item.get("evidence") or "")[:1200],
                        correctedValue=str(item.get("correctedValue"))
                        if item.get("correctedValue")
                        else None,
                    )
                )
            return out

    nested = await asyncio.gather(*(_verify_batch(batch) for batch in batches))
    verdicts = [v for group in nested for v in group]
    logger.info(
        "gate act1 verified %d section(s) in %d call(s), %d claim(s)",
        len(targets),
        len(batches),
        len(verdicts),
    )
    return verdicts


async def evaluate_against_criteria(
    *,
    rfp: RfpRecord,
    draft: ProposalDraft,
    research: ProposalResearchCache | None,
    rfp_text: str,
) -> list[CriterionVerdict]:
    """Act 2 — score each section the way the issuing agency will."""
    from app.services.proposal_langchain_agents import AgentRole, run_json_agent

    weights = {
        s.id: getattr(s, "evaluation_weight", None) for s in draft.sections
    }
    user_content = (
        f"Client: {rfp.client or ''}\nRFP: {rfp.title or ''}\n\n"
        f"RFP TEXT (scored criteria live here):\n{(rfp_text or '')[:24_000]}\n\n"
        f"Known section weights: {weights}\n\n"
        f"PROPOSAL:\n{_sections_digest(draft)}"
    )
    try:
        raw, _ = await run_json_agent(AgentRole.EVALUATOR, user_content)
    except Exception as exc:  # noqa: BLE001
        logger.warning("gate act2 evaluator failed: %s", exc)
        return []

    items = raw.get("verdicts") if isinstance(raw.get("verdicts"), list) else []
    out: list[CriterionVerdict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        sid = str(item.get("sectionId") or "")
        weight = item.get("weight")
        out.append(
            CriterionVerdict(
                sectionId=sid,
                criterion=str(item.get("criterion") or "")[:300],
                weight=float(weight) if isinstance(weight, (int, float)) else weights.get(sid),
                score=max(0, min(5, int(item.get("score") or 0))),
                whatWouldLosePoints=str(item.get("whatWouldLosePoints") or "")[:1200],
                fixTicket=str(item.get("fixTicket") or "")[:1200],
            )
        )
    return out


async def _run_detector(
    role: Any, user_content: str, key: str
) -> list[dict[str, Any]]:
    from app.services.proposal_langchain_agents import run_json_agent

    try:
        raw, _ = await run_json_agent(role, user_content)
    except Exception as exc:  # noqa: BLE001
        logger.warning("gate detector %s failed: %s", key, exc)
        return []
    items = raw.get(key)
    return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []


async def detect_quality_tickets(
    *,
    draft: ProposalDraft,
    scorecard: list[CriterionVerdict],
    only_sections: set[str] | None = None,
) -> list[GateTicket]:
    """Act 3 — four detectors over the whole manuscript, emitting local tickets.

    Detection is whole-manuscript because repetition and consistency are invisible from
    inside a single section. Repair is per-section.
    """
    import asyncio

    from app.services.proposal_langchain_agents import AgentRole

    targets = sections_to_examine(draft, changed=only_sections)
    if not targets:
        return []

    # Two digests, because the detectors do not all have the same field of view.
    #
    # Consistency and repetition compare sections AGAINST EACH OTHER, so they always get
    # the whole manuscript. Scoping them to the sections edited this round would hide a
    # duplicate created between an edited section and an untouched one — trading real
    # accuracy for tokens, which is the wrong trade.
    #
    # Slop is judged inside a single section, so restricting it to what changed costs
    # nothing and saves the most, since it is the detector with the most findings.
    full_digest = _sections_digest(draft)
    scoped_digest = (
        _sections_digest(draft.model_copy(update={"sections": targets}))
        if only_sections is not None
        else full_digest
    )
    tickets: list[GateTicket] = []

    # Evaluator findings become tickets directly — they already name the fix.
    for verdict in scorecard:
        if verdict.score >= 4 or not verdict.fix_ticket:
            continue
        tickets.append(
            GateTicket(
                sectionId=verdict.section_id,
                code=f"evaluator.{_norm(verdict.criterion).casefold().replace(' ', '_')[:40] or 'criterion'}",
                detector="evaluator",
                message=verdict.what_would_lose_points,
                guidance=verdict.fix_ticket,
                requiresEvidence=True,
            )
        )

    # The three detectors read the same digest and do not depend on each other, so
    # running them sequentially only added wall-clock time.
    conflicts, repeats, slop_findings = await asyncio.gather(
        _run_detector(AgentRole.CONSISTENCY_AUDITOR, full_digest, "conflicts"),
        _run_detector(AgentRole.REPETITION_AUDITOR, full_digest, "repeats"),
        _run_detector(AgentRole.SLOP_AUDITOR, scoped_digest, "findings"),
    )

    for conflict in conflicts:
        ids = conflict.get("sectionIds") or []
        fact = str(conflict.get("fact") or "")[:200]
        for sid in ids if isinstance(ids, list) else []:
            tickets.append(
                GateTicket(
                    sectionId=str(sid),
                    code=f"consistency.{_norm(fact).casefold().replace(' ', '_')[:40] or 'conflict'}",
                    detector="consistency",
                    message=f"Conflicting values for {fact}: {conflict.get('values')}",
                    guidance=str(conflict.get("guidance") or "")[:1200],
                    requiresEvidence=True,
                )
            )

    for repeat in repeats:
        cuts = repeat.get("cutSectionIds") or []
        what = str(repeat.get("what") or "")[:200]
        for sid in cuts if isinstance(cuts, list) else []:
            tickets.append(
                GateTicket(
                    sectionId=str(sid),
                    code=f"repetition.{_norm(what).casefold().replace(' ', '_')[:40] or 'restated'}",
                    detector="repetition",
                    message=f"Restates content owned by {repeat.get('keepSectionId')}: {what}",
                    guidance=str(repeat.get("guidance") or "")[:1200],
                    # Cuts and cross-references only — never asserts.
                    requiresEvidence=False,
                )
            )

    for finding in slop_findings:
        tickets.append(
            GateTicket(
                sectionId=str(finding.get("sectionId") or ""),
                code=f"slop.{_norm(str(finding.get('why') or '')).casefold().replace(' ', '_')[:40] or 'filler'}",
                detector="slop",
                message=str(finding.get("text") or "")[:600],
                guidance=f"Replace with: {str(finding.get('replacement') or '')[:600]}",
                requiresEvidence=False,
            )
        )

    return [t for t in tickets if t.section_id]


async def sweep_repetition(
    *, rfp: RfpRecord, draft: ProposalDraft
) -> tuple[ProposalDraft, list[str]]:
    """First-stage whole-manuscript repetition pass.

    Runs before any other stage so downstream polishing works on deduped text. Uses the
    repetition detector, whose profile has tools off: it cuts and cross-references, and
    has no authority to assert facts. Every edit is still guarded by the same regression
    check as the gate — a "dedupe" that empties a section is a loss, not a cut.
    """
    from app.services.proposal_langchain_agents import AgentRole

    logs: list[str] = []
    repeats = await _run_detector(
        AgentRole.REPETITION_AUDITOR, _sections_digest(draft), "repeats"
    )
    if not repeats:
        return draft, logs

    sections = {s.id: s for s in draft.sections}
    for repeat in repeats:
        cuts = repeat.get("cutSectionIds") or []
        what = str(repeat.get("what") or "")[:160]
        guidance = str(repeat.get("guidance") or "")[:800]
        keep = str(repeat.get("keepSectionId") or "")
        for sid in cuts if isinstance(cuts, list) else []:
            section = sections.get(str(sid))
            if section is None or not (section.content or "").strip():
                continue
            from app.services.proposal_budget_content import section_is_budgetish

            if section_is_budgetish(section):
                logs.append(
                    f"Repetition sweep: kept {section.title} unchanged (budget tab frozen)"
                )
                continue
            ticket = GateTicket(
                sectionId=section.id,
                code="repetition.sweep",
                detector="repetition",
                message=f"Restates content owned by {keep}: {what}",
                guidance=guidance or "Replace with a one-line cross-reference.",
                requiresEvidence=False,
            )
            before = section.content or ""
            try:
                after, _note = await _patch_section(
                    rfp=rfp,
                    section_id=section.id,
                    section_title=section.title or "",
                    content=before,
                    ticket=ticket,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("repetition sweep patch failed %s: %s", section.id, exc)
                continue
            if _norm(after) == _norm(before) or is_regression(before=before, after=after):
                logs.append(f"Repetition sweep: kept {section.title} unchanged ({what})")
                continue
            section.content = after
            logs.append(f"Repetition sweep: trimmed {section.title} — {what}")

    return draft.model_copy(update={"sections": list(sections.values())}), logs


# ------------------------------------------------------------------- repair contract


def _manual_fill_text(content: str, ticket: GateTicket) -> str:
    """Record the gap in the manuscript itself so it cannot be silently lost."""
    tag = f"[MANUAL FILL: {ticket.message or ticket.code}]"
    return f"{content.rstrip()}\n\n{tag}" if tag not in content else content


async def _patch_section(
    *,
    rfp: RfpRecord,
    section_id: str,
    section_title: str,
    content: str,
    ticket: GateTicket,
    packed_evidence: str = "",
) -> tuple[str, str]:
    """Retrieve, then patch. Returns (new_content, note).

    The no-fabrication rule is enforced here as a precondition: a ticket that asserts a
    fact does not reach the model at all unless retrieval produced something to assert
    from.
    """
    from app.services.proposal_langchain_agents import AgentRole, run_tool_json_agent
    from app.services.proposal_section_kb_evidence import (
        fetch_packed_section_kb_evidence,
        inject_packed_evidence_into_instruction,
    )

    evidence = (packed_evidence or "").strip()
    if ticket.requires_evidence and not evidence:
        try:
            evidence, _sources = await fetch_packed_section_kb_evidence(
                section_title=section_title,
                section_content=content,
                user_message=ticket.guidance or ticket.message,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("gate repair retrieval failed %s: %s", section_id, exc)
            evidence = ""
    elif ticket.requires_evidence and evidence:
        logger.info("gate repair reuse packed evidence section=%s", section_id)

    if not may_write_claim(requires_evidence=ticket.requires_evidence, evidence=evidence):
        return _manual_fill_text(content, ticket), "no evidence — emitted MANUAL FILL"

    instruction = (
        f"Fix exactly this finding in the section below. Change nothing else.\n\n"
        f"FINDING: {ticket.message}\n"
        f"HOW TO FIX: {ticket.guidance}\n\n"
        f"Return JSON: {{\"content\": \"<the full corrected section>\"}}\n\n"
        f"SECTION:\n{content}"
    )
    if evidence:
        instruction = inject_packed_evidence_into_instruction(instruction, evidence)

    raw, _provider, _log = await run_tool_json_agent(
        role=AgentRole.SECTION_REPAIR,
        rfp_id=rfp.id,
        title=rfp.title or "",
        client=rfp.client or "",
        user_content=instruction,
    )
    patched = str(raw.get("content") or "").strip()
    if not patched:
        return content, "patcher returned nothing"
    return patched, "patched"


async def run_quality_gate(
    *,
    rfp: RfpRecord,
    draft: ProposalDraft,
    research: ProposalResearchCache | None,
    rfp_text: str,
    ensure_not_stopped: Any = None,
) -> tuple[ProposalDraft, QualityGateReport]:
    """Detect → patch → re-detect, stopping at zero new findings or MAX_ROUNDS.

    All three acts always run. Speed comes from concurrent retrieval/verifier batches
    and incremental re-detection — not from dropping claims, rounds, or tickets.
    """
    report = QualityGateReport()
    max_rounds = _configured_max_rounds()

    async def _checkpoint() -> None:
        if ensure_not_stopped is not None:
            await ensure_not_stopped()

    await _checkpoint()
    evidence_map = await _retrieve_evidence_map(draft, only_sections=None)
    report.claims = await verify_fact_bound_claims(
        rfp_id=rfp.id,
        draft=draft,
        research=research,
        evidence_by_section=evidence_map,
    )
    # Act 1 completes before Act 2 begins: judging persuasiveness first risks keeping a
    # case study because of an invented metric.
    contradicted = [c for c in report.claims if c.status == "contradicted"]
    for claim in contradicted:
        report.changes.append(
            f"Corrected '{claim.claim[:80]}' to '{claim.corrected_value}' "
            f"({claim.evidence[:80]})"
        )

    await _checkpoint()
    report.scorecard = await evaluate_against_criteria(
        rfp=rfp, draft=draft, research=research, rfp_text=rfp_text
    )

    seen: set[tuple[str, str]] = set()
    history: dict[str, list[str]] = {s.id: [s.content or ""] for s in draft.sections}
    sections = {s.id: s for s in draft.sections}

    # None on round 1 = examine everything. Later rounds pass the sections that were
    # actually edited, so re-detection costs a fraction of the first pass.
    changed_last_round: set[str] | None = None

    for round_no in range(1, max_rounds + 1):
        await _checkpoint()
        found = await detect_quality_tickets(
            draft=draft, scorecard=report.scorecard, only_sections=changed_last_round
        )
        fresh = dedupe_tickets(found, seen)
        if not fresh:
            report.stopped_reason = f"no new findings in round {round_no}"
            report.rounds_run = round_no
            break

        report.rounds_run = round_no
        oscillated = False
        edited_this_round: set[str] = set()
        for ticket in fresh:
            await _checkpoint()
            section = sections.get(ticket.section_id)
            if section is None:
                ticket.outcome = "unfixed"
                ticket.detail = "section not found"
                report.tickets.append(ticket)
                continue

            from app.services.proposal_budget_content import section_is_budgetish

            if section_is_budgetish(section):
                ticket.outcome = "unfixed"
                ticket.detail = "budget tab is frozen — Complete & Clean must not rewrite fees"
                report.tickets.append(ticket)
                continue

            before = section.content or ""
            try:
                after, note = await _patch_section(
                    rfp=rfp,
                    section_id=section.id,
                    section_title=section.title or "",
                    content=before,
                    ticket=ticket,
                    packed_evidence=evidence_map.get(section.id, ""),
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("gate patch failed %s/%s: %s", section.id, ticket.code, exc)
                ticket.outcome = "unfixed"
                ticket.detail = f"patch error: {exc}"
                report.tickets.append(ticket)
                continue

            if note.startswith("no evidence"):
                section.content = after
                ticket.outcome = "manual_fill"
                ticket.detail = note
                report.tickets.append(ticket)
                continue

            if is_regression(before=before, after=after):
                # A loop with no revert path can only ratchet downward on a bad round.
                ticket.outcome = "reverted"
                ticket.detail = "patch regressed the section; original text kept"
                report.tickets.append(ticket)
                continue

            if _norm(after) == _norm(before):
                ticket.outcome = "unfixed"
                ticket.detail = "patch made no change"
                report.tickets.append(ticket)
                continue

            # Compare against *earlier* versions only — history[-1] is the current text,
            # and matching it means "no change", handled above, not a cycle.
            if is_oscillating(history=history.get(section.id, [])[:-1], candidate=after):
                ticket.outcome = "unfixed"
                ticket.detail = "patch reverted an earlier round; stopping this section"
                report.tickets.append(ticket)
                oscillated = True
                continue

            section.content = after
            history.setdefault(section.id, []).append(after)
            edited_this_round.add(section.id)
            ticket.outcome = "fixed"
            ticket.detail = note
            report.changes.append(f"[{ticket.detector}] {section.title}: {ticket.message[:120]}")
            report.tickets.append(ticket)

        draft = draft.model_copy(update={"sections": list(sections.values())})
        changed_last_round = edited_this_round
        # Oscillation is the more specific diagnosis and must be reported as such — a
        # cycle also leaves nothing edited, so checking "no changes" first would hide it.
        if oscillated:
            report.stopped_reason = f"oscillation detected in round {round_no}"
            break
        if not edited_this_round:
            # Nothing was edited, so re-detecting would ask the same question of the
            # same text and get the same answer. Stop instead of paying for it.
            report.stopped_reason = f"no sections changed in round {round_no}"
            break
    else:
        report.stopped_reason = f"reached the {max_rounds}-round limit"

    for ticket in report.tickets:
        if ticket.outcome in {"unfixed", "reverted"}:
            report.convergence.append(
                f"[{ticket.detector}] {ticket.section_id}: {ticket.message[:120]} "
                f"— {ticket.detail}"
            )
    for claim in report.unresolved_claims:
        report.convergence.append(
            f"[claim] {claim.section_id}: unverified — '{claim.claim[:120]}'"
        )

    logger.info(
        "quality_gate rounds=%s tickets=%s fixed=%s manual=%s unfixed=%s stop=%s",
        report.rounds_run,
        len(report.tickets),
        sum(1 for t in report.tickets if t.outcome == "fixed"),
        sum(1 for t in report.tickets if t.outcome == "manual_fill"),
        sum(1 for t in report.tickets if t.outcome in {"unfixed", "reverted"}),
        report.stopped_reason,
    )
    return draft, report
