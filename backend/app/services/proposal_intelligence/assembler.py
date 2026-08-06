"""Assemble Proposal Execution Plan + derive legacy Phase 2 fields."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.models.proposal import ProofPoint, RfpSectionMap
from app.models.requirement_ledger import LedgerRequirement, RequirementLedger
from app.services.proposal_intelligence.memory import upsert_memory
from app.services.proposal_intelligence.schemas import (
    ComplianceItem,
    EvaluationCriterion,
    ProposalExecutionPlan,
)
from app.services.proposal_section_aliases import PROPOSAL_SECTION_ALIAS_GROUPS


def refresh_proposal_memory(plan: ProposalExecutionPlan) -> ProposalExecutionPlan:
    """Consolidate known facts from opportunity/delivery into proposalMemory."""
    u = plan.opportunity.understanding
    d = plan.delivery
    facts: dict[str, str] = {
        "clientName": u.client,
        "organizationType": u.org_type,
        "industry": u.industry,
        "projectType": u.project_type,
        "complexity": u.complexity,
    }
    if d.delivery_model.type:
        facts["deliveryApproach"] = d.delivery_model.type
    if d.budget.pricing_model:
        facts["pricingModel"] = d.budget.pricing_model
    if d.budget.contract_type:
        facts["contractType"] = d.budget.contract_type
    if plan.opportunity.strategy.winning_theme:
        facts["winningTheme"] = plan.opportunity.strategy.winning_theme
    # Preserve existing memory keys (cms, hosting, accessibility, etc.)
    plan.proposal_memory = upsert_memory(plan.proposal_memory, "assembler", facts)
    return plan


def _zo_mode_for_title(title: str) -> str:
    lower = title.lower()
    if any(k in lower for k in ("team", "personnel", "staff", "bio")):
        return "select"
    if any(k in lower for k in ("experience", "case", "reference", "portfolio")):
        return "select"
    if any(k in lower for k in ("company", "qualification", "about", "firm")):
        return "pull"
    return "write"


def _normalize(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace — for exact comparison."""
    return " ".join(re.findall(r"[a-z0-9]+", (text or "").lower()))


_MATCH_STOPWORDS = {
    "the", "and", "for", "with", "from", "section", "of", "to", "a", "an",
    "or", "in", "on", "per", "rfp",
}

# Shared tokens this generic/boring on their own can NOT establish a match —
# "Summary" is a substring of "Provide a summary of your insurance coverage"
# and of a hundred unrelated asks; "Cost" is a substring of any pricing
# sentence. Reused from proposal_outline_dedup's own judgment of which short
# labels are too vague to stand alone (that module treats "qualifications" as
# boring too, but here it is the deciding token behind a real 10-pair
# measurement, so it stays out of this set — see task-2-report.md).
_BORING_SHARED_TOKENS = {
    "summary", "cost", "price", "pricing", "budget", "fee", "fees",
}


def _match_tokens(normalized_text: str) -> set[str]:
    return {
        t for t in normalized_text.split()
        if len(t) >= 3 and t not in _MATCH_STOPWORDS
    }


# Coverage of the SHORTER side — is the smaller of the two texts mostly
# accounted for by the overlap?
_MIN_SHORTER_SIDE_COVERAGE = 0.5
# Coverage of the LONGER side — how much of the bigger, MORE SPECIFIC text does
# the overlap actually explain? This is the guard that stops a one-token title
# ("Team", "Overview", "Approach", "Insurance") from satisfying a long
# enumerated requirement. Such a title trivially scores 1.0 on the shorter side
# whenever its single word appears anywhere in the requirement, so the shorter
# side alone cannot tell "References" vs "Client References and Testimonials"
# (a real wording variant) from "Team" vs "...your project team, subcontractors,
# and key personnel including resumes and organizational charts" (a section that
# answers a fraction of the ask). Measured separation is wide: every genuine
# wording-variant pair scores >= 0.333 here, every reproduced false positive
# <= 0.125.
_MIN_LONGER_SIDE_COVERAGE = 1 / 3
# Stricter longer-side floor for the narrow case where a MULTI-token title's
# entire token set is swallowed by the requirement (or vice versa) — e.g. a
# title "Key Staff" (2 tokens) vs a short, UNRELATED requirement "Key
# deliverable needs staff approval" (5 tokens) hits inter=2, shorter=1.0,
# longer=2/5=0.4: comfortably above the base 1/3 floor by pure coincidence,
# because both of two ordinary English words happen to appear in an unrelated
# sentence. "Our Work"/"Our work order requires site approval" and "Project
# Team"/"Project closeout needs team signoff" hit the same trap at exactly
# 1/3. A single-token full match (e.g. "References" vs a 3-token title) does
# NOT get this stricter floor — that is the genuine wording-variant case this
# matcher exists to catch, and it sits at exactly 1/3 with no headroom to
# spare (see _MIN_LONGER_SIDE_COVERAGE's docstring). The failure mode is
# specific to MULTI-token full containment, so only that case is tightened.
_MIN_LONGER_SIDE_COVERAGE_FULL_CONTAINMENT = 0.5


def _alias_whole_concept_match(ta: set[str], tb: set[str]) -> bool:
    """Curated-synonym channel: standard procurement equivalences that share
    zero (or only "boring") tokens and so can never pass the overlap-scoring
    channel above — "Cover Letter"/"Letter of Transmittal", "Key
    Personnel"/"Staffing Plan", "Project Schedule"/"Timeline", "Company
    Overview"/"About Us", "Executive Summary"/"Summary of Approach". See
    proposal_section_aliases.py for the table and the conservative judgment
    behind each entry.

    Whole-concept only, never a token within a longer ask: a side matches an
    alias phrase only when its ENTIRE meaningful token set equals that
    phrase's token set. "Timeline" (a real alias entry) therefore does NOT
    satisfy "Provide a timeline for subcontractor onboarding and describe
    your quality assurance methodology" — that requirement's token set has
    six members, nowhere near {"timeline"}. A single-token alias is safe
    here for the same reason a single-token title is unsafe in the overlap
    channel above: that channel tests substring/subset containment, this one
    tests set EQUALITY.
    """
    if not ta or not tb:
        return False
    fa, fb = frozenset(ta), frozenset(tb)
    for group in _ALIAS_GROUPS_BY_TOKENS:
        if fa in group and fb in group:
            return True
    return False


# Precomputed once: each alias phrase's meaningful-token-set, grouped exactly
# as proposal_section_aliases.py groups the phrases. Built with the same
# _normalize/_match_tokens pipeline used at match time, so an alias phrase's
# stopwords/short-word handling always agrees with a real title or
# requirement's — e.g. "about us" reduces to {"about"} on both sides ("us" is
# 2 characters, filtered by _match_tokens), which is what lets "About Us"
# alone stand in for "Company Overview" without a special case.
_ALIAS_GROUPS_BY_TOKENS: tuple[frozenset[frozenset[str]], ...] = tuple(
    frozenset(
        frozenset(_match_tokens(_normalize(phrase))) for phrase in group
    )
    for group in PROPOSAL_SECTION_ALIAS_GROUPS
)


def _scored_token_overlap_match(
    req_n: str,
    title_n: str,
    *,
    threshold: float = _MIN_SHORTER_SIDE_COVERAGE,
    longer_side_threshold: float = _MIN_LONGER_SIDE_COVERAGE,
) -> bool:
    """Wording-variant match: "Cover Letter" vs "Letter of Transmittal",
    "Insurance Certificate" vs "Certificate of Insurance", etc.

    Not a bare substring or Jaccard test — those are exactly what produced the
    silent false positives this module used to have (a short generic title is
    a substring of a huge fraction of requirement texts). Four channels, in
    order:
      1. the overlap must cover at least half the SHORTER side's meaningful
         tokens (not a token or two out of a long sentence);
      2. the overlap must also cover a third of the LONGER side — a short
         generic title cannot claim a long, specific, multi-part requirement
         just because one of its words appears in it (raised to one half when
         a MULTI-token side is fully swallowed by coincidence — see
         _MIN_LONGER_SIDE_COVERAGE_FULL_CONTAINMENT);
      3. if every shared token is on the "boring" denylist above, reject —
         "Executive Summary" and "insurance coverage summary" share only
         "summary", which proves nothing about the topic;
      4. failing all of the above, fall through to the curated alias table
         (_alias_whole_concept_match) for standard procurement synonyms that
         share no usable tokens at all.

    Deliberately biased toward false NEGATIVES. A missed match leaves a
    requirement in ``missing()`` for a human to dismiss; a false match marks it
    satisfied and hides it forever, which is the single defect the ledger
    exists to catch. Consequence: a long requirement whose section title is a
    single word ("Provide three professional references from municipal clients"
    vs a "References" tab) will NOT auto-match. That is intended.

    Measured against 10 realistic RFP wording-variant pairs and a false-positive
    battery; see task-2-report.md, task-8-report.md and
    tests/test_outline_coverage.py / tests/test_section_aliases.py.
    """
    ta, tb = _match_tokens(req_n), _match_tokens(title_n)
    if not ta or not tb:
        return False
    inter = ta & tb
    if inter and not (inter <= _BORING_SHARED_TOKENS):
        shorter_len = min(len(ta), len(tb))
        required_longer = longer_side_threshold
        if len(inter) == shorter_len and shorter_len >= 2:
            required_longer = max(
                longer_side_threshold, _MIN_LONGER_SIDE_COVERAGE_FULL_CONTAINMENT
            )
        if (len(inter) / shorter_len) >= threshold and (
            len(inter) / max(len(ta), len(tb))
        ) >= required_longer:
            return True
    return _alias_whole_concept_match(ta, tb)


def _match_outline_sections(
    *,
    requirement_text: str,
    target_hint: str,
    outline_sections: list[RfpSectionMap],
) -> list[str]:
    """Deterministic (no LLM) requirement -> section matching.

    Every branch is either a whole-string comparison on normalized text or a
    guarded scored token overlap — never a bare substring test. A short
    generic title (Summary, Cost, Team) is a substring of a large fraction of
    requirement texts, so the old ``title in requirement`` branch produced
    silent false positives — and a false "satisfied" hides the requirement
    from ``missing()``, which is the one thing the ledger exists to catch.

    Four ways a section can cover a requirement, in order of confidence:
      1. the item's own ``target_section`` hint equals the section title;
      2. one of the section's ``requirements`` bullets equals the requirement;
      3. the section title equals the requirement text;
      4. scored token overlap above threshold (wording variants — Task 2 Step 0).
    """
    req_n = _normalize(requirement_text)
    hint_n = _normalize(target_hint)
    matches: list[str] = []
    for section in outline_sections:
        title_n = _normalize(getattr(section, "title", "") or "")
        if hint_n and title_n and hint_n == title_n:
            matches.append(section.id)
            continue
        bullets = getattr(section, "requirements", None) or []
        if req_n and any(_normalize(str(b)) == req_n for b in bullets):
            matches.append(section.id)
            continue
        if req_n and title_n and title_n == req_n:
            matches.append(section.id)
            continue
        if req_n and title_n and _scored_token_overlap_match(req_n, title_n):
            matches.append(section.id)
    return matches


# Word-boundary "form"/"forms" only. A bare substring test matched "in-form-ation",
# "per-form-ance", "con-form-ing" and "plat-form" — four of the most common words in
# a compliance matrix — and mislabelled ordinary narrative requirements as forms.
_FORM_RE = re.compile(r"\bforms?\b")
# Phrases that contain the word "form" but are not a document to return.
_FORM_DENY_RE = re.compile(
    r"\b(?:"
    r"form(?:s)?\s+(?:of|a|an|the)\b"      # "in the form of", "form a team"
    r"|in\s+form(?:s)?\b"
    r"|form(?:al|ally|at|atting|ation|ative|ulate|ulated|ulation)\b"
    r")"
)


def _classify_compliance_source(item: ComplianceItem) -> str:
    blob = " ".join(
        str(getattr(item, attr, "") or "")
        for attr in ("requirement", "evidence_needed", "source_ref")
    ).lower()
    if _FORM_RE.search(blob) and not _FORM_DENY_RE.search(blob):
        return "form"
    return "required_content"


def _slug(text: str, *, limit: int = 48) -> str:
    return "-".join(re.findall(r"[a-z0-9]+", (text or "").lower()))[:limit]


def build_requirement_ledger(
    compliance_items: list[ComplianceItem],
    evaluation_criteria: list[EvaluationCriterion],
    outline_sections: list[RfpSectionMap],
) -> RequirementLedger:
    """Build the persisted requirement ledger from Phase 2's parsed compliance
    matrix and evaluation criteria — the data that was previously reduced to
    ``f"Compliance item count: {len(...)}"`` for the outline planner and then
    discarded. Matches each requirement against the (already lean-filtered)
    outline sections so ``missing()``/``duplicated()`` can be read later.

    Pure data plumbing — makes zero LLM calls. Never raises: malformed or
    missing inputs degrade to an empty/partial ledger rather than blocking
    Phase 2.
    """
    requirements: list[LedgerRequirement] = []
    used_ids: set[str] = set()

    def _unique(candidate: str, fallback: str) -> str:
        """Ids are keys for later stages; the LLM can emit the same id twice."""
        base = (candidate or "").strip() or fallback
        if base not in used_ids:
            used_ids.add(base)
            return base
        suffix = 2
        while f"{base}-{suffix}" in used_ids:
            suffix += 1
        unique = f"{base}-{suffix}"
        used_ids.add(unique)
        return unique

    for index, item in enumerate(compliance_items or [], start=1):
        try:
            text = (getattr(item, "requirement", "") or "").strip()
            if not text:
                continue
            satisfied_by = _match_outline_sections(
                requirement_text=text,
                target_hint=getattr(item, "target_section", "") or "",
                outline_sections=outline_sections,
            )
            evidence_needed = (getattr(item, "evidence_needed", "") or "").strip()
            requirements.append(
                LedgerRequirement(
                    id=_unique(getattr(item, "id", "") or "", f"comp-{index}"),
                    text=text,
                    source=_classify_compliance_source(item),  # type: ignore[arg-type]
                    mandatory=bool(getattr(item, "mandatory", True)),
                    points=None,
                    satisfiedBy=satisfied_by,
                    kbQueries=[evidence_needed] if evidence_needed else [],
                )
            )
        except Exception:  # noqa: BLE001 — one bad item must never block Phase 2
            continue

    for index, crit in enumerate(evaluation_criteria or [], start=1):
        try:
            name = (getattr(crit, "name", "") or "").strip()
            if not name:
                continue
            satisfied_by = _match_outline_sections(
                requirement_text=name,
                target_hint="",
                outline_sections=outline_sections,
            )
            weight = getattr(crit, "weight", None)
            # Slug, not position: criterion identity must survive a Phase 2 re-run
            # that returns the same criteria in a different order.
            slug = _slug(name)
            requirements.append(
                LedgerRequirement(
                    id=_unique(
                        f"scored-{slug}" if slug else "", f"scored-{index}"
                    ),
                    text=name,
                    source="scored_criterion",
                    mandatory=True,
                    points=float(weight) if weight is not None else None,
                    satisfiedBy=satisfied_by,
                    kbQueries=[name],
                )
            )
        except Exception:  # noqa: BLE001
            continue

    return RequirementLedger(requirements=requirements)


@dataclass(frozen=True)
class DuplicateOwnerResolution:
    """One requirement's ownership decision when more than one section
    satisfies it — the mapping that killed the insurance-x3 duplication
    (Section 1.5, the attachments checklist, and the contract acknowledgment
    all restated coverage because nothing owned it).

    Deliberately does NOT prune ``LedgerRequirement.satisfied_by``: that list
    is factual matcher evidence (``_match_outline_sections``, measured 6/10 on
    wording variants — see task-2-report.md), not an ownership claim, and a
    requirement must never look missing again just because ownership was
    resolved. This record is the additive ownership claim instead.
    """

    requirement_id: str
    requirement_text: str
    owner_section_id: str
    cross_reference_section_ids: list[str]
    note: str


def resolve_duplicate_owners(
    ledger: RequirementLedger | None,
    outline_sections: list[RfpSectionMap] | None,
) -> tuple[RequirementLedger, list[DuplicateOwnerResolution]]:
    """Assign exactly one owning section per requirement ``duplicated()`` finds.

    Owner = the candidate section with the highest evaluation points; ties
    break toward the earliest RFP-ordered section (its position in
    ``outline_sections``, the list's own order). Every other section that
    matched keeps the topic only as a cross-reference — it must never restate
    the requirement's substance (limits, carriers, coverage types, etc.).

    This operates strictly WITHIN one requirement's own ``satisfied_by``
    list. It never compares text or topic across different
    ``LedgerRequirement`` entries, so two genuinely different requirements
    that both happen to mention insurance ("provide proof of general
    liability insurance" vs "acknowledge the insurance provisions of the
    standard contract") can never be merged into each other — merging
    distinct requirements would lose a required response, which is worse
    than the duplication this function exists to resolve.

    Returns the ledger unchanged (same requirements, same ``satisfied_by``)
    alongside the list of resolutions — never raises: ``None``/empty inputs
    degrade to an empty ledger and no resolutions.
    """
    if ledger is None:
        return RequirementLedger(requirements=[]), []

    sections = outline_sections or []
    order_index = {s.id: i for i, s in enumerate(sections)}
    weight_by_id: dict[str, float] = {}
    for s in sections:
        raw_weight = getattr(s, "evaluation_weight", None)
        try:
            weight_by_id[s.id] = float(raw_weight) if raw_weight is not None else 0.0
        except (TypeError, ValueError):
            weight_by_id[s.id] = 0.0

    resolutions: list[DuplicateOwnerResolution] = []
    for requirement in ledger.requirements:
        candidates = list(requirement.satisfied_by)
        if len(candidates) <= 1:
            continue
        ordered = sorted(
            candidates,
            key=lambda sid: (
                -weight_by_id.get(sid, 0.0),
                order_index.get(sid, len(sections)),
            ),
        )
        owner = ordered[0]
        cross_refs = ordered[1:]
        resolutions.append(
            DuplicateOwnerResolution(
                requirement_id=requirement.id,
                requirement_text=requirement.text,
                owner_section_id=owner,
                cross_reference_section_ids=cross_refs,
                note=(
                    f"{requirement.text!r} is owned by section {owner!r}; "
                    "do not restate its specifics (limits, carriers, coverage "
                    "types, or similar detail) here — cross-reference it instead."
                ),
            )
        )
    return ledger, resolutions


def amend_outline_for_missing_requirements(
    ledger: RequirementLedger,
    outline_sections: list[RfpSectionMap],
) -> list[RfpSectionMap]:
    """Append one section per missing mandatory requirement.

    Task 2 Step 4's interface, implemented and unit-tested. Originally shipped
    **not called from derive_legacy_fields**: Task 1's review measured
    ``_match_outline_sections`` at 10/10 misses on realistic wording variants
    before Step 0's fix; Step 0 raised that to a measured 6/10, but four
    pairs — Key Personnel/Staffing Plan, Project Schedule/Timeline, Company
    Overview/About Us, Executive Summary/Summary of Approach — still missed
    because they share zero (or only "boring") tokens, and auto-adding on a
    signal that still misses real synonyms would add a duplicate section for
    a requirement that is already covered, manufacturing the exact
    insurance-x3 duplication this plan exists to remove.

    Task 8's curated alias table (``proposal_section_aliases.py``) closes
    those four gaps deterministically — no LLM call — raising the matcher to
    a measured 10/10 with zero false positives (task-8-report.md). This
    function is now wired at its Phase 2 call site, ``derive_legacy_fields``,
    which rebuilds the ledger against the amended outline immediately after
    calling this so ``satisfied_by`` reflects the sections just added.
    """
    existing_ids = {s.id for s in outline_sections}
    amended = list(outline_sections)
    for requirement in ledger.missing():
        section_id = f"ledger-{requirement.id}"
        if section_id in existing_ids:
            continue
        amended.append(
            RfpSectionMap(
                id=section_id,
                title=requirement.text,
                requirements=[requirement.text],
                zoMode="write",
                # Stays a float: an RFP can weight a criterion at 12.5 pts and
                # the old int() cast silently truncated that to 12.
                evaluationWeight=requirement.points,
            )
        )
        existing_ids.add(section_id)
    return amended


def derive_legacy_fields(plan: ProposalExecutionPlan) -> dict[str, Any]:
    """Derive rfpSections / sectionQueries / proofPoints. Never returns evidenceCorpus."""
    from app.services.proposal_outline_dedup import filter_lean_outline_sections

    plans_by_id = {p.section_id: p for p in plan.writing.section_plans.plans}
    retrieval_by_id = {e.section_id: e for e in plan.writing.retrieval_plan.entries}

    # Near-dup + static only — outline already lean-filtered with RFP context upstream.
    lean_sections, _dropped = filter_lean_outline_sections(
        list(plan.writing.proposal_outline.sections),
        rfp_context="",
        drop_generic_filler=False,
    )

    rfp_sections: list[RfpSectionMap] = []
    section_queries: dict[str, list[str]] = {}

    for section in lean_sections:
        brief = plans_by_id.get(section.id)
        entry = retrieval_by_id.get(section.id)
        requirements: list[str] = []
        if brief:
            requirements.extend(brief.key_messages)
            requirements.extend(brief.evidence_needed)
        if not requirements:
            requirements = [f"Address {section.title} per RFP"]

        weight = None
        if brief and brief.evaluation_criteria:
            for crit in plan.opportunity.evaluation.criteria:
                if crit.name in brief.evaluation_criteria and crit.weight is not None:
                    weight = int(crit.weight)
                    break

        focus: list[str] = []
        if entry:
            focus = list(entry.expected_sources)[:6]
            section_queries[section.id] = list(entry.queries)[:5]

        rfp_sections.append(
            RfpSectionMap(
                id=section.id,
                title=section.title,
                requirements=requirements[:12],
                retrievalFocus=focus or ["company facts"],
                zoMode=_zo_mode_for_title(section.title),  # type: ignore[arg-type]
                evaluationWeight=weight,
            )
        )

    proof_points: list[ProofPoint] = []
    for brief in plan.writing.section_plans.plans:
        for need in brief.evidence_needed[:3]:
            proof_points.append(
                ProofPoint(
                    requirement=need,
                    caseStudy=need,
                    narrativeHook=brief.purpose,
                    relevance="planned",
                    sectionIds=[brief.section_id],
                    evaluationWeight=None,
                )
            )

    requirement_ledger = build_requirement_ledger(
        list(plan.opportunity.compliance.items),
        list(plan.opportunity.evaluation.criteria),
        rfp_sections,
    )

    # Step 5 (Task 8): amend the outline itself when a mandatory requirement —
    # a compliance item OR a scored criterion, RequirementLedger.missing()
    # already treats every scored criterion as mandatory so neither class is
    # silently skipped here — has no covering section. This used to be
    # advisory-only (missing() persisted for a human to read in the ending
    # report) because the matcher measured 6/10 on realistic wording variants;
    # auto-adding at that precision would have put a second cover letter
    # beside an existing "Letter of Transmittal". The curated alias table
    # (proposal_section_aliases.py) closes the remaining gaps deterministically
    # — no LLM call — raising the matcher to a measured 10/10 with zero false
    # positives (task-8-report.md), so it is now safe to amend BEFORE drafting
    # rather than after, meaning the new section gets drafted like any other
    # instead of surfacing only in an ending report nobody reads until
    # submission is already assembled.
    amended_sections = amend_outline_for_missing_requirements(
        requirement_ledger, rfp_sections
    )
    if len(amended_sections) != len(rfp_sections):
        rfp_sections = amended_sections
        # Rebuild so satisfied_by reflects the sections just added — each new
        # section's title is the requirement's own text, so
        # _match_outline_sections' exact-title-equals-requirement branch picks
        # it up deterministically on this second pass. Without the rebuild the
        # requirement would still read as "missing" despite now having a
        # section, making Step 5 self-defeating.
        requirement_ledger = build_requirement_ledger(
            list(plan.opportunity.compliance.items),
            list(plan.opportunity.evaluation.criteria),
            rfp_sections,
        )

    return {
        "rfpSections": rfp_sections,
        "sectionQueries": section_queries,
        "proofPoints": proof_points,
        "requirementLedger": requirement_ledger,
    }


def stamp_metadata(plan: ProposalExecutionPlan, *, rfp_id: str, provider: str | None) -> ProposalExecutionPlan:
    plan.metadata.rfp_id = rfp_id
    plan.metadata.generated_at = datetime.now(timezone.utc).isoformat()
    if provider:
        plan.metadata.provider = provider
    plan.metadata.validation_status = plan.validation.readiness_status
    confidences = [
        plan.opportunity.understanding.confidence,
        plan.opportunity.strategy.confidence,
        plan.delivery.methodology.confidence,
        plan.delivery.budget.confidence,
        plan.writing.proposal_outline.confidence,
        plan.writing.retrieval_plan.confidence,
    ]
    nonzero = [c for c in confidences if c > 0]
    plan.metadata.plan_confidence = sum(nonzero) / len(nonzero) if nonzero else 0.0
    return plan
