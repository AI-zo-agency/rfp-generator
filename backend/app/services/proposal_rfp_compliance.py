"""RFP-to-proposal compliance scanning — driven by Phase 2 research, not static regex."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.models.proposal import ProposalDraft, ProposalResearchCache, ProposalSection
from app.models.requirement_ledger import LedgerRequirement, RequirementLedger
from app.models.rfp import RfpRecord
from app.services.proposal_budget_content import find_budget_section_index
from app.services.proposal_budget_validation import (
    derive_commission_agency_revenue,
    is_commission_style_budget,
)
from app.services.rfp_page_limit import resolve_page_limit

logger = logging.getLogger(__name__)

OPEN_TAG_MARKERS = ("[VERIFY", "[PLACEHOLDER", "[TBD", "[INSERT")
MANUAL_FILL_MARKER = "[MANUAL FILL"

_REQ_STOPWORDS = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "that",
        "this",
        "from",
        "shall",
        "must",
        "will",
        "have",
        "been",
        "provide",
        "include",
        "submit",
        "proposal",
        "offeror",
        "vendor",
        "contractor",
        "services",
        "required",
        "agency",
    }
)


@dataclass(frozen=True)
class ComplianceGap:
    section_id: str
    section_title: str
    category: str
    message: str
    rfp_requirement: str
    excerpt: str
    repair_hint: str


def _manuscript_blob(draft: ProposalDraft) -> str:
    return "\n\n".join(
        f"## {s.title}\n{s.content}" for s in draft.sections if (s.content or "").strip()
    )


def _section_by_title_patterns(
    draft: ProposalDraft,
    *patterns: str,
) -> ProposalSection | None:
    for section in draft.sections:
        title = (section.title or "").casefold()
        if any(p in title for p in patterns):
            return section
    return None


def _words_from_title(title: str) -> list[str]:
    words: list[str] = []
    normalized = (
        title.casefold()
        .replace("-", " ")
        .replace("—", " ")
        .replace("/", " ")
    )
    for word in normalized.split():
        cleaned = "".join(ch for ch in word if ch.isalnum())
        if len(cleaned) > 4:
            words.append(cleaned)
    return words


def _section_for_mapped_title(
    draft: ProposalDraft,
    mapped_title: str,
) -> ProposalSection | None:
    title_key = (mapped_title or "").strip().casefold()
    by_title = {(s.title or "").strip().casefold(): s for s in draft.sections}
    if title_key in by_title:
        return by_title[title_key]

    words = _words_from_title(mapped_title or "")
    if not words:
        return None
    for section in draft.sections:
        section_title = (section.title or "").casefold()
        if any(word in section_title for word in words):
            return section
    return None


def _text_contains(haystack: str, needle: str) -> bool:
    return needle.casefold() in haystack.casefold()


def _has_manual_fill_handoff(content: str, *keywords: str) -> bool:
    lower = content.casefold()
    start = 0
    while True:
        idx = lower.find(MANUAL_FILL_MARKER.casefold(), start)
        if idx < 0:
            return False
        end = lower.find("]", idx)
        if end < 0:
            return False
        tag = lower[idx : end + 1]
        if any(keyword.casefold() in tag for keyword in keywords):
            return True
        start = end + 1


def _unresolved_submission_placeholders(content: str) -> bool:
    upper = content.upper()
    if any(marker in upper for marker in OPEN_TAG_MARKERS):
        return True
    padded = f" {content} "
    return " TBD " in padded.upper() or "___" in content


def _requirement_tokens(req: str) -> list[str]:
    tokens: list[str] = []
    for word in req.casefold().split():
        cleaned = "".join(ch for ch in word if ch.isalnum())
        if len(cleaned) >= 5 and cleaned not in _REQ_STOPWORDS:
            tokens.append(cleaned)
    return tokens[:8]


# A requirement is answered in ONE PLACE, not sprinkled across a 50-page packet.
# The original check asked only whether half a requirement's keywords appeared
# ANYWHERE in the whole manuscript blob, which passed on two different lies:
#   * vocabulary reuse that answers nothing ("Quality standards are described.
#     Translating materials is discussed elsewhere.")
#   * the same words scattered across unrelated tabs thousands of chars apart,
#     so one section's prose marked another section's requirement satisfied.
# Requiring the keywords to co-occur inside a bounded window fixes both without
# changing any call site: prose that genuinely answers the requirement keeps its
# terms close together, and scattered words never land in one window.
#
# The window spans a few paragraphs rather than one, because a real answer is
# often a short subsection rather than a single block.
_REQ_COVERAGE_WINDOW_CHARS = 1500


def _token_positions(tokens: list[str], haystack: str) -> list[tuple[int, str]]:
    """Every occurrence of every token, as (position, token). Plain str.find."""
    found: list[tuple[int, str]] = []
    for token in tokens:
        start = haystack.find(token)
        while start != -1:
            found.append((start, token))
            start = haystack.find(token, start + 1)
    found.sort()
    return found


def requirement_likely_covered(req: str, manuscript: str) -> bool:
    """Heuristic: enough requirement keywords co-occur in one passage of prose."""
    tokens = _requirement_tokens(req)
    if not tokens:
        return True
    needed = max(2, (len(tokens) + 1) // 2)
    haystack = (manuscript or "").casefold()
    if not haystack:
        return False

    occurrences = _token_positions(tokens, haystack)
    if len(occurrences) < needed:
        return False

    # Slide a character window over the occurrences; covered as soon as any one
    # window holds `needed` DISTINCT requirement terms.
    counts: dict[str, int] = {}
    left = 0
    for right, (pos, token) in enumerate(occurrences):
        counts[token] = counts.get(token, 0) + 1
        while pos - occurrences[left][0] > _REQ_COVERAGE_WINDOW_CHARS:
            drop = occurrences[left][1]
            counts[drop] -= 1
            if counts[drop] == 0:
                del counts[drop]
            left += 1
        if len(counts) >= needed:
            return True
    return False


def scan_open_submission_tags(*, draft: ProposalDraft) -> list[ComplianceGap]:
    """Flag sections that still contain open VERIFY / PLACEHOLDER / TBD tags."""
    gaps: list[ComplianceGap] = []
    for section in draft.sections:
        content = section.content or ""
        if not content.strip() or not _unresolved_submission_placeholders(content):
            continue
        gaps.append(
            ComplianceGap(
                section_id=section.id,
                section_title=section.title,
                category="submission_tag",
                message=(
                    "Section still contains open submission placeholders "
                    "(VERIFY, PLACEHOLDER, TBD, or INSERT) — fill from KB or assign MANUAL FILL"
                ),
                rfp_requirement="submission-ready prose with no open placeholder tags",
                excerpt=content[:280],
                repair_hint=(
                    "Search KB for the missing fact. If KB cannot supply it, replace with exactly one "
                    "[MANUAL FILL: Sonja — field] or [MANUAL FILL: Ella — field] tag per gap."
                ),
            )
        )
    return gaps


def scan_uncovered_requirement_gaps(
    *,
    draft: ProposalDraft,
    research: ProposalResearchCache | None,
) -> list[ComplianceGap]:
    """Gaps from Phase 2 mapped sections — dynamic per RFP, not static patterns."""
    if not research or not research.rfp_sections:
        return []

    manuscript = _manuscript_blob(draft)
    gaps: list[ComplianceGap] = []

    for mapped in research.rfp_sections:
        uncovered = mapped.uncovered_requirements or []
        if not uncovered:
            continue

        section = _section_for_mapped_title(draft, mapped.title or "")
        if not section:
            continue

        content = section.content or ""
        for req in uncovered[:5]:
            if _has_manual_fill_handoff(content, *(_requirement_tokens(req)[:3])):
                continue
            if requirement_likely_covered(req, content) or requirement_likely_covered(req, manuscript):
                continue
            gaps.append(
                ComplianceGap(
                    section_id=section.id,
                    section_title=section.title,
                    category="requirement_coverage",
                    message=(
                        "Phase 2 research flagged an uncovered RFP requirement still missing "
                        f"from the manuscript: {req[:120]}"
                    ),
                    rfp_requirement=req[:200],
                    excerpt=content[:240],
                    repair_hint=(
                        "Address this requirement explicitly in prose, a compliance table, or form "
                        "response. Search KB for supporting facts; use MANUAL FILL only after KB "
                        "search returns nothing."
                    ),
                )
            )

    return gaps[:15]


# ---------------------------------------------------------------------------
# Requirement-ledger reconciler (Task 5, ADD wired in Task 9).
#
# scan_uncovered_requirement_gaps above walks research.rfp_sections and skips
# any requirement with no matching section (`if not section: continue`) — it
# structurally cannot ADD anything, and it never checks duplication or page
# budget at all. reconcile_requirement_ledger replaces that section-driven
# walk with a requirement-driven one over the persisted RequirementLedger:
#
#     len(satisfied_by) == 0  -> ADD: applied (new stub section, MANUAL FILL)
#     len(satisfied_by) == 1  -> correct, left alone
#     len(satisfied_by) >  1  -> MERGE: applied (cross-reference, not restate)
#     over page budget        -> CUT: applied (lowest-scoring content first)
#
# ADD was surfaced-only through Task 8: _match_outline_sections (assembler.py)
# was measured at 6/10 on realistic wording-variant pairs, so missing()
# over-reported — a requirement genuinely covered under a different title
# read as missing, and auto-adding on that signal risked a second "Letter of
# Transmittal" next to an existing "Cover Letter". Task 8's alias table (and
# this round's removal of two unsafe aliases — proposal_section_aliases.py)
# raised the matcher to a measured 8/10 with zero false positives in every
# battery, so the precision objection that justified surfaced-only no longer
# holds. ADD now applies exactly the same len(satisfied_by) == 0 signal
# missing() already used — only the action changed. The added section is a
# deterministic [MANUAL FILL] stub (see MANUAL_FILL_MARKER) naming the
# requirement verbatim; it never invents content and is never mistaken for
# an open placeholder gap by scan_open_submission_tags. Idempotent by
# construction: the section id is derived from the requirement id
# (`ledger-{requirement.id}`), so a requirement that already got a section on
# a prior pass is skipped, not re-added, even though satisfied_by on the
# persisted ledger is never mutated to reflect it.
#
# MERGE and CUT act on stronger signals (duplicated() found >=2 real matches;
# page count is measured, not guessed) and were already applied automatically.
# A section a MERGE resolution designates as owner in THIS pass is exempt
# from CUT in the same pass — it just became the sole bearer of that
# requirement's substance (limits, carriers, amounts); trimming it right
# after could delete the very detail MERGE just consolidated there. Exemption
# is computed from every duplicate-owner resolution the ledger currently
# implies (not just the ones that made a fresh edit this run), so it holds on
# every re-run, not only the first.
#
# Post-incident correction: ADD used to apply to every missing() requirement
# regardless of source. A live run on a real proposal proved that unsafe —
# ledger.missing() flagged 21 of the RFP's evaluation *criteria* ("Relevant
# Experience", "Strategic Approach and Methodology", "Personnel and Project
# Management", "Reporting and Performance Optimization", "Cost and Overall
# Value", ...) as uncovered even though the proposal already addressed every
# one of them under requirement-phrased section titles (e.g. "Examples of
# similar work performed within the past five (5) years" for "Relevant
# Experience"). A scored_criterion is a scoring CATEGORY name, not a
# deliverable — it is satisfied by whatever section addresses it, and
# matching an abstract category name to requirement-phrased prose lexically
# is not a problem the matcher can be tuned to solve reliably (5 of 5 misses
# on the real RFP above). ADD now applies ONLY to source in
# _ADD_ELIGIBLE_SOURCES (required_content) — narrative deliverables where
# "no section covers this" really does mean "this is missing". Forms and
# signed attachments used to be ADD-eligible too; a live Providence scan
# then spawned one Bid Form / MWBE / Financial Assurance tab per checklist
# row (18→26 sections) and ballooned Manual Fill / Checklist counts. Those
# are physical or buyer-template submissions — they belong on the attachment
# checklist (and at most one consolidated Forms & Attachments tab), never as
# N separate manuscript stubs. A missing scored_criterion is never
# auto-added; it is downgraded to an advisory line in the report so a human
# can judge whether it is genuinely uncovered, exactly the judgment call the
# matcher cannot safely make on its own. Do NOT try to fix this by loosening
# the matcher instead — see test_outline_coverage.py /
# test_section_aliases.py's false-positive battery for what that
# reintroduces.
#
# Blast-radius guard: the same incident's ledger would have added 21 new
# sections to a 23-section, 12-page-limit proposal even with source-filtering
# applied to a smaller set — a reconciler bug that adds a handful of sections
# is a nuisance; one that silently doubles a document is a different order of
# danger, and the failure mode above shows the matcher can be systematically
# wrong across an entire category, not just a one-off miss. If a single pass
# would add more than _BLAST_RADIUS_MAX_ADDITIONS sections, or grow the
# section count by more than _BLAST_RADIUS_MAX_GROWTH_FRACTION, none of that
# pass's eligible additions are applied — they are reported instead so a
# human decides. A reconciler that under-adds and says so is recoverable by
# clicking Scan RFP again after review; one that silently balloons a
# near-page-limit proposal is not.
#
# Pure and deterministic — zero LLM calls, never raises.
# ---------------------------------------------------------------------------

# Same 350-words/page ratio as proposal_drafting_graph.WORDS_PER_PAGE /
# proposal_presubmit_review's page-limit check. Duplicated as a local
# constant rather than imported: proposal_drafting_graph.py pulls in
# langgraph, and this module is imported by many lightweight consumers
# (self_edit_loop, presubmit_review, ending_report, manual_flags) that only
# need ComplianceGap. Matches proposal_presubmit_review.py's existing
# precedent of hardcoding the same ratio rather than importing it.
_WORDS_PER_PAGE = 350
# A section carrying evaluation points must never be cut below this — same
# value as proposal_drafting_graph.MIN_SECTION_WORDS, the floor generation
# already uses so a page-budget response still reads as a real answer.
_SCORED_SECTION_FLOOR_WORDS = 150
# Content with no scored requirement attached is the lowest priority to keep
# — same value as proposal_drafting_graph.ABSOLUTE_MIN_SECTION_WORDS.
_UNSCORED_SECTION_FLOOR_WORDS = 50

_LEDGER_XREF_MARKER_TEMPLATE = "[LEDGER-XREF:{requirement_id}]"
_ADDED_SECTION_ID_TEMPLATE = "ledger-{requirement_id}"
# Same generic-confirmation owner proposal_manual_flags.py uses for
# non-bio, non-budget MANUAL FILL handoffs.
_ADDED_SECTION_MANUAL_FILL_OWNER = "Sonja"

# ADD is only safe for ledger sources that name an actual submittable
# deliverable — see the module note above. "scored_criterion" (an evaluation
# scoring category), "eligibility" (a go/no-go gate, not a proposal section),
# and "submission_instruction" (a compliance obligation you COMPLY WITH —
# a deadline, delivery address, labelling rule, validity window, copy count,
# format rule, or, as of the fourth instance below, ANY phrasing that fails
# to positively read as a narrative deliverable — never a section you WRITE;
# see proposal_intelligence/assembler.py's _classify_compliance_source) are
# deliberately excluded: len(satisfied_by) == 0 for those means "the matcher
# didn't find a lexical match" or "this was never section-shaped to begin
# with", not "this is missing a section". Third instance of the same defect:
# a live KVCC scan flagged 8 administrative compliance-matrix items
# ("Proposal must be received no later than August 3, 2026 by 3:00 P.M.
# (ET)", "Include contractor's name(s)", ...) as missing sections; the
# blast-radius guard below happened to catch it that time only because 8
# exceeded the cap — with 4 such items it would have silently added them.
# Fourth instance (task-19-report.md): a *different* live KVCC scan flagged
# a blanket statutory-compliance clause, a public-records exemption
# instruction, and a font/format rule as missing sections — none matched the
# third instance's deny-list patterns, proving a deny list can never be
# complete. The classifier's default is now fail-closed instead of extended
# with a fifth pattern set: see assembler.py's _classify_compliance_source
# module note.
_ADD_ELIGIBLE_SOURCES = frozenset({"required_content"})

# Blast-radius guard (see module note above). Both are module-level named
# constants, not magic numbers, because the threshold decision itself is the
# safety mechanism and needs to be reviewable/tunable in one place.
#   - _BLAST_RADIUS_MAX_ADDITIONS: an absolute cap. 3 keeps Complete & Clean
#     from quietly minting a cluster of MANUAL FILL stubs (each inflates
#     Checklist). Always active, regardless of the existing draft's size.
#   - _BLAST_RADIUS_MAX_GROWTH_FRACTION: a relative cap so a large proposal
#     (60+ sections) isn't held to the same absolute ceiling as a small
#     one. 0.25 means a single Scan-RFP click can grow a document by at most
#     a quarter — the incident this guards against would have grown a
#     23-section proposal by 91% (21/23) in one click. Only checked once the
#     draft already has at least _BLAST_RADIUS_MAX_ADDITIONS sections — on a
#     near-empty draft (e.g. one legitimately missing section on a 1-section
#     outline) a fraction is not a meaningful signal (100% "growth") and the
#     absolute cap alone is the right guard.
# Either threshold alone is enough to decline the pass.
_BLAST_RADIUS_MAX_ADDITIONS = 3
_BLAST_RADIUS_MAX_GROWTH_FRACTION = 0.25

# Soft headroom under the hard page cap — evaluators count cover/TOC; shipping
# at 100% of the word budget is how proposals get disqualified on length.
_PAGE_BUDGET_HEADROOM = 0.92

# Titles that build evaluator trust — never drop the whole section for length.
_TRUST_ANCHOR_TITLE_RE = re.compile(
    r"(?i)\b(?:"
    r"who\s+we\s+are|about\s+(?:the\s+)?(?:firm|agency|company)|organizational\s+structure|"
    r"business\s+information|certification|insurance|cover\s+letter|"
    r"authorized\s+signature|legal\s+obligation|budget|pricing|cost\s+of\s+base|"
    r"past\s+performance|references?|personnel|r[ée]sum[ée]|bio|"
    r"key\s+personnel|team\s+overview|case\s+stud|sample\s+work|portfolio|"
    r"closing|offeror\s+commitment"
    r")\b"
)

_PADDING_TITLE_RE = re.compile(
    r"(?i)\b(?:"
    r"additional\s+supporting\s+material|document\s+checklist|"
    r"attachment\s+checklist|compliance\s+checklist|"
    r"designer\s+note|internal\s+only"
    r")\b"
)


def _is_trust_anchor_section(
    section: ProposalSection,
    ledger: RequirementLedger,
    *,
    protected_owner_ids: set[str],
) -> bool:
    sid = section.id or ""
    if sid in protected_owner_ids:
        return True
    if sid.startswith(("section-1", "section-2", "section-3", "section-budget")):
        return True
    title = section.title or ""
    if _TRUST_ANCHOR_TITLE_RE.search(title):
        return True
    if _section_evaluation_points(ledger, sid) > 0:
        return True
    for req in ledger.requirements:
        if sid in (req.satisfied_by or []) and (
            req.mandatory or (req.points or 0) > 0 or req.source == "required_content"
        ):
            return True
    try:
        from app.services.proposal_section_dedup import _is_protected_scan_section

        if _is_protected_scan_section(section):
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _is_padding_only_section(section: ProposalSection) -> bool:
    """Checklist / stub sections that inflate length without earning trust."""
    title = section.title or ""
    content = section.content or ""
    if _PADDING_TITLE_RE.search(title):
        return True
    fill_count = content.upper().count("[MANUAL FILL")
    words = _word_count(content)
    if fill_count >= 4 and words < 500:
        return True
    if fill_count >= 1 and words < 80 and "never invent the answer" in content.casefold():
        return True
    try:
        from app.services.proposal_voice_enforcement import is_duplicate_static_rfp_section

        if is_duplicate_static_rfp_section(title):
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


@dataclass(frozen=True)
class AppliedRequirementAddition:
    """A mandatory ledger requirement no section covered — a new stub
    section was added for it.

    Applied, not merely surfaced: see the module-level note above on why ADD
    graduated from surfaced-only (Task 5) to applied (Task 9) once the
    matcher behind len(satisfied_by) == 0 was raised to a measured 8/10 with
    zero false positives. The stub itself never invents facts — it is a
    [MANUAL FILL] marker naming the requirement verbatim.
    """

    requirement_id: str
    requirement_text: str
    section_id: str
    section_title: str
    source: str
    points: float | None
    kb_queries: list[str]


@dataclass(frozen=True)
class AppliedMergeAction:
    """A requirement claimed by more than one section, resolved to a single
    owner. The other sections keep a cross-reference marker instead of
    restating the requirement's substance."""

    requirement_id: str
    requirement_text: str
    owner_section_id: str
    owner_section_title: str
    cross_reference_section_ids: list[str]
    note: str


@dataclass(frozen=True)
class AppliedCutAction:
    """Trailing content trimmed from one section to fit the RFP's page budget."""

    section_id: str
    section_title: str
    words_removed: int
    had_evaluation_points: bool


@dataclass(frozen=True)
class AdvisoryScoredCriterion:
    """A scored evaluation criterion (source="scored_criterion") the matcher
    found no section for — never auto-added (see module note above), only
    surfaced so a human can judge whether it is genuinely uncovered or
    already addressed under a differently-worded section title."""

    requirement_id: str
    requirement_text: str
    points: float | None


@dataclass(frozen=True)
class AdvisorySubmissionInstruction:
    """A compliance obligation (source="submission_instruction") — a
    deadline, delivery method, labelling rule, validity window, copy count,
    or format rule the deny list in assembler.py recognises explicitly, OR
    (as of task-19-report.md, the fourth instance of this defect class) any
    compliance-matrix item that fails to positively read as a narrative
    deliverable or a form — a blanket statutory-compliance clause, a
    public-records exemption instruction, an unanticipated format rule, or
    any other phrasing nobody has seen yet. Never auto-added (see
    _ADD_ELIGIBLE_SOURCES' module note): nobody writes a proposal section
    titled "Proposal must be received no later than August 3, 2026 by 3:00
    P.M. (ET)", and nobody writes one titled "Comply with all applicable
    federal, state and local statutes..." either. Unlike
    AdvisoryScoredCriterion, this is not a matcher-precision judgment call
    for a human to review — it is a real obligation that must stay visible,
    just not as a candidate section. The Scan-RFP banner reports these as a
    compliance checklist ("N submission requirement(s) to comply with: ...")
    distinct from both the drafting and attachment checklists
    proposal_rfp_submission_requirements.py already surfaces."""

    requirement_id: str
    requirement_text: str


@dataclass(frozen=True)
class LedgerReconcileResult:
    draft: ProposalDraft
    changed: bool
    applied_additions: list[AppliedRequirementAddition]
    applied_merges: list[AppliedMergeAction]
    applied_cuts: list[AppliedCutAction]
    logs: list[str]
    # Missing scored criteria — always populated when any exist, whether or
    # not anything else in this pass changed. See AdvisoryScoredCriterion.
    advisory_scored_criteria: list[AdvisoryScoredCriterion] = field(default_factory=list)
    # Administrative/procedural submission constraints (source=
    # "submission_instruction") — always populated when any exist, whether
    # or not anything else in this pass changed. See
    # AdvisorySubmissionInstruction. Never auto-added, but never silently
    # dropped either — the caller surfaces this as a compliance checklist.
    advisory_submission_instructions: list[AdvisorySubmissionInstruction] = field(
        default_factory=list
    )
    # Set only when the blast-radius guard declined to apply otherwise-
    # eligible additions this pass (see _BLAST_RADIUS_MAX_ADDITIONS /
    # _BLAST_RADIUS_MAX_GROWTH_FRACTION above). 0 / [] / None on every other
    # path, including "nothing to add" and "additions applied normally".
    declined_addition_count: int = 0
    declined_addition_titles: list[str] = field(default_factory=list)
    declined_addition_reason: str | None = None
    # Set whenever this call produced a ledger the caller should persist back
    # onto research.requirement_ledger, in either of two cases: (1)
    # research.requirement_ledger was missing/empty and this call built one
    # on demand (see _build_ledger_on_demand below), or (2) a ledger WAS
    # persisted but had stale source classification(s) from before a
    # classifier fix, corrected in place (see _reclassify_persisted_ledger
    # below). Either way the caller persists it so the NEXT scan reads the
    # corrected/built ledger back instead of redoing the work every time.
    # None on every other path, including "ledger present and already
    # correctly classified — nothing to reconcile".
    built_ledger: RequirementLedger | None = None
    # Set only when the reconcile could not run at all — no persisted ledger
    # AND nothing to build one from. Distinguishes "checked and found nothing
    # to fix" (skipped_reason is None) from "never checked" (skipped_reason
    # explains why), so the caller can say so in the Scan-RFP banner instead
    # of a silent no-op that reads identically to "already compliant".
    skipped_reason: str | None = None


def _word_count(text: str | None) -> int:
    return len((text or "").split())


def _cross_reference_marker(requirement_id: str) -> str:
    return _LEDGER_XREF_MARKER_TEMPLATE.format(requirement_id=requirement_id)


def _append_cross_reference(
    content: str,
    *,
    requirement_id: str,
    requirement_text: str,
    owner_title: str,
) -> str:
    marker = _cross_reference_marker(requirement_id)
    # The closing `_` (markdown italics) must land *before* the final period,
    # not after it — otherwise the note's last character is `_`, which the T1
    # truncation gate's terminal-punctuation check (proposal_t1_validators.py,
    # _TERMINAL_PUNCT_RE) does not recognize as a sentence end, and the
    # cross-referenced section gets misreported as truncated content needing
    # review.
    note = (
        f"\n\n{marker} _Cross-reference: “{requirement_text[:160].strip()}” is "
        f"fully addressed in “{owner_title}” — see that section; not "
        "restated here to avoid duplication_."
    )
    return (content or "").rstrip() + note


def _resolve_draft_section_by_id(
    sections: list[ProposalSection],
    research: ProposalResearchCache | None,
    section_id: str,
) -> ProposalSection | None:
    """Ledger satisfied_by ids are RfpSectionMap ids. Those match ProposalSection
    ids by construction within one generation pass, but can drift after an
    independent research/draft reload — fall back to the same title-matching
    _section_for_mapped_title already uses at the compliance-scan boundary."""
    for section in sections:
        if section.id == section_id:
            return section
    if not research:
        return None
    mapped = next((m for m in research.rfp_sections if m.id == section_id), None)
    if not mapped:
        return None
    title_key = (mapped.title or "").strip().casefold()
    if title_key:
        for section in sections:
            if (section.title or "").strip().casefold() == title_key:
                return section
    words = _words_from_title(mapped.title or "")
    if not words:
        return None
    for section in sections:
        title_cf = (section.title or "").casefold()
        if any(word in title_cf for word in words):
            return section
    return None


def _trim_trailing_paragraphs(
    content: str,
    *,
    max_words_to_remove: int,
    floor_words: int,
) -> tuple[str, int]:
    """Drop whole trailing paragraphs (never mid-sentence, never the last
    paragraph, never below floor_words total)."""
    paragraphs = [p for p in (content or "").split("\n\n") if p.strip()]
    if len(paragraphs) <= 1 or max_words_to_remove <= 0:
        return content, 0
    total_words = sum(_word_count(p) for p in paragraphs)
    kept = list(paragraphs)
    removed = 0
    while len(kept) > 1:
        last_words = _word_count(kept[-1])
        if removed + last_words > max_words_to_remove:
            break
        if total_words - removed - last_words < floor_words:
            break
        kept.pop()
        removed += last_words
    if removed <= 0:
        return content, 0
    return "\n\n".join(kept), removed


_LEDGER_STUB_MARKER = "requirement-ledger reconciler added this section"


def _is_stale_administrative_ledger_stub(section: ProposalSection) -> bool:
    """True for a reconciler-added stub that is really a compliance instruction.

    Live KVCC defect: font rules / FOAA / blanket statutory clauses were ADDed
    as sections under an older classifier default. They must be removed on the
    next Scan RFP, not left as [MANUAL FILL] tabs.
    """
    content = (section.content or "").casefold()
    if _LEDGER_STUB_MARKER not in content:
        return False
    if MANUAL_FILL_MARKER.casefold() not in content and "[manual fill" not in content:
        # Already filled with real prose — leave it (human may have rewritten).
        # Stub marker alone without MANUAL FILL still means reconciler placeholder.
        if "never invent the answer" not in content:
            return False
    try:
        from app.services.proposal_intelligence.assembler import (
            _classify_compliance_source,
        )
        from app.services.proposal_intelligence.schemas import ComplianceItem

        probe = ComplianceItem(
            id=section.id or "stub",
            requirement=(section.title or "").strip(),
            evidence_needed="",
        )
        return _classify_compliance_source(probe) == "submission_instruction"
    except Exception:  # noqa: BLE001
        return True


def _build_added_requirement_section(requirement) -> ProposalSection:
    """Deterministic stub for a mandatory requirement no section covers.

    Zero LLM calls (net LLM delta stays zero). Names the requirement
    verbatim inside a [MANUAL FILL] tag — the existing convention
    scan_open_submission_tags already treats as a legitimate human handoff,
    not an open placeholder — so a human or a later KB-search pass fills it
    in without this function ever inventing a fact.
    """
    text = (requirement.text or "").strip() or requirement.id
    # The explanation is wrapped in [NOTE: …] deliberately. It is BOTH internal
    # machinery narration (which must never reach the designer — it was shipping
    # as section prose) AND a detection marker four call sites match on
    # (_LEDGER_STUB_MARKER, "never invent the answer"). The tag keeps the marker
    # present in `content` through the whole pipeline, while
    # proposal_manuscript.strip_inline_instruction_tags removes [NOTE: …] during
    # manuscript cleanup, so the client-facing copy carries only the MANUAL FILL
    # handoff — the shape scan_open_submission_tags already treats as legitimate.
    content = (
        f"[MANUAL FILL: {_ADDED_SECTION_MANUAL_FILL_OWNER} — {text[:200]}]\n\n"
        "[NOTE: No section in the draft addressed this mandatory RFP requirement; "
        "the requirement-ledger reconciler added this section as a "
        "placeholder so it cannot silently ship missing. Search the KB for "
        "supporting facts and replace the tag above before submission — "
        "never invent the answer.]"
    )
    return ProposalSection(
        id=_ADDED_SECTION_ID_TEMPLATE.format(requirement_id=requirement.id),
        title=text[:120],
        content=content,
        required=True,
        custom=True,
        source="rfp",
        mode="write",
        status="outline",
        word_target=300,
    )


def _section_evaluation_points(ledger: RequirementLedger, section_id: str) -> float:
    return sum(
        r.points
        for r in ledger.requirements
        if r.points and section_id in r.satisfied_by
    )


def _build_ledger_on_demand(
    research: ProposalResearchCache | None,
) -> tuple[RequirementLedger | None, str]:
    """Rebuild the requirement ledger for a proposal that predates Task 1 (or
    had its ledger wiped by a whitelist-rebuild bug fixed alongside it).

    reconcile_requirement_ledger's normal path reads research.requirement_ledger
    and no-ops when it is missing — which is EVERY proposal generated before
    the ledger existed, i.e. every real proposal a live user opens. The ledger
    only ever gets built in Phase 2 (build_requirement_ledger, called from
    proposal_intelligence/assembler.py.derive_legacy_fields); this reuses that
    exact function and matcher rather than a second implementation, sourcing
    its three inputs from what Phase 2 already persisted:
      - compliance_items / evaluation_criteria: research.proposal_execution_plan
        .opportunity.compliance.items / .opportunity.evaluation.criteria — the
        same ProposalExecutionPlan derive_legacy_fields itself reads from.
      - outline_sections: research.rfp_sections — Phase 2's own output, always
        persisted independently of the ledger.
    Pure Python, zero LLM calls — same as build_requirement_ledger itself.

    Returns (ledger, reason) when a genuine ledger was built, or (None, reason)
    when it could not be — reason is always populated (never blank) so the
    caller can log AND surface to the user why nothing happened, instead of a
    silent no-op that reads identically to "already compliant".
    """
    if research is None:
        return None, "no research cache persisted for this proposal"

    plan = research.proposal_execution_plan
    if isinstance(plan, dict):
        try:
            from app.services.proposal_intelligence.schemas import ProposalExecutionPlan

            plan = ProposalExecutionPlan.model_validate(plan)
        except Exception:  # noqa: BLE001 — malformed legacy payload, treat as absent
            plan = None

    if plan is None or not hasattr(plan, "opportunity"):
        return None, (
            "no proposal execution plan persisted on this proposal's research "
            "cache (pre-intelligence-layer proposal, or Phase 2 never "
            "completed) — compliance items and evaluation criteria were never "
            "captured, so a ledger cannot be built without a fresh Phase 2 run"
        )

    compliance_items = list(getattr(plan.opportunity.compliance, "items", None) or [])
    evaluation_criteria = list(getattr(plan.opportunity.evaluation, "criteria", None) or [])
    outline_sections = list(research.rfp_sections or [])

    if not compliance_items and not evaluation_criteria:
        return None, (
            "research.proposal_execution_plan is persisted but has zero "
            "compliance items and zero evaluation criteria — nothing to build "
            "a ledger from"
        )

    from app.services.proposal_intelligence.assembler import build_requirement_ledger

    built = build_requirement_ledger(compliance_items, evaluation_criteria, outline_sections)
    if not built.requirements:
        return None, (
            f"built an on-demand ledger from {len(compliance_items)} compliance "
            f"item(s) and {len(evaluation_criteria)} evaluation criterion(a), "
            "but every one was empty/unusable — nothing to reconcile"
        )
    return built, (
        f"built on demand from research.proposal_execution_plan "
        f"({len(compliance_items)} compliance item(s), {len(evaluation_criteria)} "
        f"evaluation criterion(a)) matched against {len(outline_sections)} "
        "outline section(s) in research.rfp_sections"
    )


# Sources _classify_compliance_source can actually produce for a compliance-
# matrix item (see its body in proposal_intelligence/assembler.py).
# "scored_criterion" is set explicitly by build_requirement_ledger for
# evaluation criteria — the classifier is never consulted for those — and
# "eligibility" is reserved/unused (see requirement_ledger.py's module note).
# Re-classifying either would run a matcher over text it was never designed
# to judge, so _reclassify_persisted_ledger below leaves both untouched.
_RECLASSIFIABLE_SOURCES = frozenset({"required_content", "form", "submission_instruction"})


def _reclassify_persisted_ledger(
    ledger: RequirementLedger,
) -> tuple[RequirementLedger, int]:
    """Re-run the CURRENT _classify_compliance_source matcher over a ledger
    that was already persisted onto research.requirement_ledger, so a
    classifier fix (e.g. the submission_instruction patterns added alongside
    _ADD_ELIGIBLE_SOURCES — see that module note) reaches every EXISTING
    proposal instead of only ones whose ledger gets built fresh after the fix
    ships.

    build_requirement_ledger only ever runs _classify_compliance_source once,
    at Phase 2 build time. reconcile_requirement_ledger's normal path then
    reads research.requirement_ledger completely as-is, so a persisted
    "required_content" label assigned BEFORE a classifier fix never gets
    corrected — every real proposal a live user opens predates whatever
    classifier fix shipped most recently. Same defect shape
    _build_ledger_on_demand fixed for a MISSING ledger, applied here to a
    STALE one.

    Only re-classifies sources the classifier can actually produce — see
    _RECLASSIFIABLE_SOURCES. Re-classifies ``source`` ONLY: ``satisfied_by``,
    ``points``, ``id``, ``mandatory`` and ``kb_queries`` are left
    byte-for-byte untouched on every requirement, changed or not — the
    reconciler depends on that state (matcher results, evaluation weight)
    for idempotence, and none of it is a function of source classification.

    Never raises: a requirement the classifier can't evaluate (missing text,
    or any other error probing it) keeps its persisted source unchanged
    rather than blocking the scan, same as build_requirement_ledger's own
    per-item try/except.

    Returns ``(ledger, 0)`` — the SAME ledger object, unchanged — when every
    requirement's persisted source already matches the current classifier,
    so a second scan (or a proposal whose ledger was already built post-fix)
    is a true no-op: nothing to log, nothing new to persist. Otherwise
    returns a new ``RequirementLedger`` with only the reclassified
    requirements replaced, and the count of requirements that changed.
    """
    from app.services.proposal_intelligence.assembler import _classify_compliance_source
    from app.services.proposal_intelligence.schemas import ComplianceItem

    changed_count = 0
    updated: list[LedgerRequirement] = []
    for requirement in ledger.requirements:
        if requirement.source not in _RECLASSIFIABLE_SOURCES:
            updated.append(requirement)
            continue
        try:
            probe = ComplianceItem(
                id=requirement.id or "probe",
                requirement=requirement.text or "",
                evidence_needed=(requirement.kb_queries[0] if requirement.kb_queries else ""),
            )
            new_source = _classify_compliance_source(probe)
        except Exception:  # noqa: BLE001 — malformed persisted entry keeps its label
            updated.append(requirement)
            continue
        if new_source == requirement.source:
            updated.append(requirement)
            continue
        changed_count += 1
        updated.append(requirement.model_copy(update={"source": new_source}))

    if changed_count == 0:
        return ledger, 0
    return RequirementLedger(requirements=updated), changed_count


def reconcile_requirement_ledger(
    *,
    draft: ProposalDraft,
    research: ProposalResearchCache | None,
    rfp: RfpRecord,
    rfp_text: str | None = None,
) -> LedgerReconcileResult:
    """Ledger-driven reconciler for an EXISTING draft — fixes it in place
    instead of regenerating it. See the module note above for the three-way
    read: ADD, MERGE and CUT are all applied, with MERGE owners protected
    from CUT in the same pass.

    Idempotent: a second call on the result of the first finds the added
    sections and merge markers already present and the word count already
    under budget, so it returns changed=False. Never raises — missing
    research, a missing or empty ledger, and no resolvable page limit all
    degrade to a no-op.
    """
    ledger = research.requirement_ledger if research else None
    built_ledger: RequirementLedger | None = None
    if not ledger or not ledger.requirements:
        on_demand_ledger, reason = _build_ledger_on_demand(research)
        if on_demand_ledger is None:
            logger.info(
                "ledger:reconcile rfp_id=%s skipped — %s",
                getattr(rfp, "id", None),
                reason,
            )
            return LedgerReconcileResult(
                draft=draft,
                changed=False,
                applied_additions=[],
                applied_merges=[],
                applied_cuts=[],
                logs=[f"ledger: no requirement ledger present — {reason}"],
                skipped_reason=reason,
            )
        logger.info(
            "ledger:reconcile rfp_id=%s no persisted requirement ledger — %s "
            "(%d requirement(s))",
            getattr(rfp, "id", None),
            reason,
            len(on_demand_ledger.requirements),
        )
        ledger = on_demand_ledger
        built_ledger = on_demand_ledger
    else:
        # Ledger IS persisted — but a persisted ledger can still carry STALE
        # source classifications from before a classifier fix shipped (see
        # _reclassify_persisted_ledger's docstring). Re-classify before this
        # pass's ADD/advisory logic reads `source`, so the fix reaches this
        # already-existing proposal, not only ones whose ledger is built
        # fresh from here on.
        reclassified_ledger, reclassified_count = _reclassify_persisted_ledger(ledger)
        if reclassified_count:
            logger.info(
                "ledger:reconcile rfp_id=%s persisted ledger had %d stale "
                "source classification(s) — re-classified with the current "
                "matcher before reconciling",
                getattr(rfp, "id", None),
                reclassified_count,
            )
            ledger = reclassified_ledger
            built_ledger = reclassified_ledger

    logs: list[str] = []
    sections = list(draft.sections)
    changed = False

    # ADD — applied, but ONLY for sources in _ADD_ELIGIBLE_SOURCES (see module
    # note above). len(satisfied_by) == 0 is the exact signal missing() used
    # when ADD was surfaced-only; scored requirements (points set) are added
    # first among eligible ones so a partially-applied pass favors the
    # requirements that carry evaluation weight.
    applied_additions: list[AppliedRequirementAddition] = []
    advisory_scored_criteria: list[AdvisoryScoredCriterion] = []
    advisory_submission_instructions: list[AdvisorySubmissionInstruction] = []
    declined_addition_count = 0
    declined_addition_titles: list[str] = []
    declined_addition_reason: str | None = None
    existing_section_ids = {s.id for s in sections}
    missing_requirements = sorted(
        ledger.missing(),
        key=lambda r: (r.points is None, -(r.points or 0.0)),
    )

    # A scored_criterion is a scoring CATEGORY, never a deliverable — see the
    # module note. It is surfaced as advisory regardless of whether the
    # blast-radius guard below trips, so the user always sees it even if
    # eligible additions this pass get declined.
    for requirement in missing_requirements:
        if requirement.source == "scored_criterion":
            advisory_scored_criteria.append(
                AdvisoryScoredCriterion(
                    requirement_id=requirement.id,
                    requirement_text=requirement.text,
                    points=requirement.points,
                )
            )
    if advisory_scored_criteria:
        names = ", ".join(
            f'"{a.requirement_text[:80]}"' for a in advisory_scored_criteria[:5]
        )
        extra = len(advisory_scored_criteria) - min(5, len(advisory_scored_criteria))
        if extra > 0:
            names += f", +{extra} more"
        logs.append(
            f"ledger:add — {len(advisory_scored_criteria)} scored criteri"
            f"{'on' if len(advisory_scored_criteria) == 1 else 'a'} may not be "
            f"covered: {names} — a scoring category name rarely matches the "
            "requirement-phrased section that actually covers it, so this is "
            "never auto-added; review manually."
        )

    # A submission_instruction is an administrative constraint you comply
    # with, never a deliverable — see _ADD_ELIGIBLE_SOURCES' module note.
    # Unlike a scored_criterion (a matcher-precision judgment call), this is
    # never "genuinely uncovered" in the sense of needing a section at all —
    # it is a real obligation (a deadline, a labelling rule, a validity
    # window) that must stay visible as a compliance checklist item so a
    # human doesn't miss the August 3 deadline just because it was correctly
    # never turned into a stub section.
    for requirement in missing_requirements:
        if requirement.source == "submission_instruction":
            advisory_submission_instructions.append(
                AdvisorySubmissionInstruction(
                    requirement_id=requirement.id,
                    requirement_text=requirement.text,
                )
            )
    if advisory_submission_instructions:
        names = ", ".join(
            f'"{a.requirement_text[:80]}"'
            for a in advisory_submission_instructions[:5]
        )
        extra = len(advisory_submission_instructions) - min(
            5, len(advisory_submission_instructions)
        )
        if extra > 0:
            names += f", +{extra} more"
        logs.append(
            f"ledger:submission-instructions — {len(advisory_submission_instructions)} "
            f"administrative submission requirement(s) to comply with: {names} — "
            "deadlines, delivery instructions and similar constraints are never "
            "drafted as a section; review and comply with each one manually."
        )

    # Forms / signed attachments: checklist (and at most one consolidated
    # Forms tab from submission inventory) — never N ledger stub sections.
    advisory_forms: list[AdvisorySubmissionInstruction] = []
    for requirement in missing_requirements:
        if requirement.source == "form":
            advisory_forms.append(
                AdvisorySubmissionInstruction(
                    requirement_id=requirement.id,
                    requirement_text=requirement.text,
                )
            )
    if advisory_forms:
        names = ", ".join(f'"{a.requirement_text[:80]}"' for a in advisory_forms[:5])
        extra = len(advisory_forms) - min(5, len(advisory_forms))
        if extra > 0:
            names += f", +{extra} more"
        logs.append(
            f"ledger:forms — {len(advisory_forms)} form/attachment item(s) stay on "
            f"the Checklist (not new sidebar sections): {names}"
        )

    # Remove stale ledger stubs that an older classifier wrongly ADDed as
    # required_content (font rules, FOAA exemptions, blanket statutory clauses).
    # Also drop ledger-{id} stubs for requirements now classified as
    # submission_instruction / scored_criterion / form — but ONLY when the
    # section is still an auto-added reconciler stub. Never delete substantive
    # manuscript tabs (Public Sector Experience, Required Forms, etc.) that
    # Phase 3 or the user already drafted.
    removed_admin_stubs: list[AppliedCutAction] = []
    non_addable_ids = {
        _ADDED_SECTION_ID_TEMPLATE.format(requirement_id=r.id)
        for r in ledger.requirements
        if r.source in {"submission_instruction", "scored_criterion", "form"}
    }
    kept_sections: list[ProposalSection] = []
    for section in sections:
        drop = False
        if section.id in non_addable_ids:
            body = section.content or ""
            body_cf = body.casefold()
            is_reconciler_stub = _LEDGER_STUB_MARKER in body_cf
            mostly_manual_fill = (
                body.upper().count("[MANUAL FILL") >= 1
                and _word_count(body) < 120
            )
            if is_reconciler_stub and mostly_manual_fill:
                drop = True
            elif is_reconciler_stub and "never invent the answer" in body_cf:
                drop = True
            # Substantive drafted tabs stay — even if source is scored_criterion.
        elif _is_stale_administrative_ledger_stub(section):
            drop = True
        if drop:
            removed_admin_stubs.append(
                AppliedCutAction(
                    section_id=section.id,
                    section_title=section.title or section.id,
                    words_removed=_word_count(section.content),
                    had_evaluation_points=False,
                )
            )
            changed = True
            continue
        kept_sections.append(section)
    if removed_admin_stubs:
        sections = kept_sections
        existing_section_ids = {s.id for s in sections}
        logs.append(
            f"ledger:remove-admin-stubs — removed {len(removed_admin_stubs)} "
            "non-deliverable instruction stub(s) that should never be proposal "
            "sections: "
            + ", ".join(f'"{c.section_title[:80]}"' for c in removed_admin_stubs[:5])
        )

    eligible_missing = [
        r for r in missing_requirements if r.source in _ADD_ELIGIBLE_SOURCES
    ]
    candidate_additions = [
        r
        for r in eligible_missing
        if _ADDED_SECTION_ID_TEMPLATE.format(requirement_id=r.id)
        not in existing_section_ids
    ]

    existing_section_count = len(sections)
    # The fraction check only engages once the draft already has at least
    # _BLAST_RADIUS_MAX_ADDITIONS sections — see the constant's comment
    # above for why a tiny draft's "growth" isn't a meaningful signal.
    growth_fraction = (
        len(candidate_additions) / existing_section_count
        if existing_section_count >= _BLAST_RADIUS_MAX_ADDITIONS
        else 0.0
    )
    blast_radius_tripped = candidate_additions and (
        len(candidate_additions) > _BLAST_RADIUS_MAX_ADDITIONS
        or growth_fraction > _BLAST_RADIUS_MAX_GROWTH_FRACTION
    )

    if blast_radius_tripped:
        declined_addition_count = len(candidate_additions)
        declined_addition_titles = [r.text[:120] for r in candidate_additions]
        declined_addition_reason = (
            f"would add {len(candidate_additions)} section(s) to a "
            f"{existing_section_count}-section proposal in one pass — over the "
            f"blast-radius guard (max {_BLAST_RADIUS_MAX_ADDITIONS} per pass or "
            f"{_BLAST_RADIUS_MAX_GROWTH_FRACTION:.0%} growth); declined "
            "automatically, review and add manually."
        )
        logs.append(f"ledger:add — declined — {declined_addition_reason}")
    else:
        for requirement in candidate_additions:
            new_section = _build_added_requirement_section(requirement)
            sections.append(new_section)
            existing_section_ids.add(new_section.id)
            changed = True
            applied_additions.append(
                AppliedRequirementAddition(
                    requirement_id=requirement.id,
                    requirement_text=requirement.text,
                    section_id=new_section.id,
                    section_title=new_section.title,
                    source=requirement.source,
                    points=requirement.points,
                    kb_queries=list(requirement.kb_queries),
                )
            )
        if applied_additions:
            logs.append(
                f"ledger:add — {len(applied_additions)} mandatory requirement(s) had no "
                "matching section; added as new draft section(s) flagged [MANUAL FILL] "
                "for KB search / human content."
            )

    section_index_by_id = {s.id: i for i, s in enumerate(sections)}

    # MERGE — applied. Acts on duplicated(), a stronger signal than missing():
    # the matcher found >=2 real section matches, not zero.
    applied_merges: list[AppliedMergeAction] = []
    protected_owner_ids: set[str] = set()
    from app.services.proposal_intelligence.assembler import resolve_duplicate_owners

    _, resolutions = resolve_duplicate_owners(
        ledger, research.rfp_sections if research else []
    )
    for resolution in resolutions:
        owner = _resolve_draft_section_by_id(
            sections, research, resolution.owner_section_id
        )
        if not owner:
            continue
        # Every resolution's owner is protected from CUT below, whether or
        # not this particular run has any fresh cross-reference edit to make
        # for it — an idempotent no-op MERGE pass must still protect the
        # same owner CUT would have seen on the run that actually wrote the
        # cross-reference markers.
        protected_owner_ids.add(owner.id)
        cross_ref_ids: list[str] = []
        for cross_id in resolution.cross_reference_section_ids:
            section = _resolve_draft_section_by_id(sections, research, cross_id)
            if not section:
                continue
            marker = _cross_reference_marker(resolution.requirement_id)
            if marker in (section.content or ""):
                continue  # idempotent: already merged on a prior run
            idx = section_index_by_id.get(section.id)
            if idx is None:
                continue
            new_content = _append_cross_reference(
                section.content or "",
                requirement_id=resolution.requirement_id,
                requirement_text=resolution.requirement_text,
                owner_title=owner.title,
            )
            sections[idx] = section.model_copy(update={"content": new_content})
            cross_ref_ids.append(section.id)
            changed = True
        if cross_ref_ids:
            applied_merges.append(
                AppliedMergeAction(
                    requirement_id=resolution.requirement_id,
                    requirement_text=resolution.requirement_text,
                    owner_section_id=owner.id,
                    owner_section_title=owner.title,
                    cross_reference_section_ids=cross_ref_ids,
                    note=resolution.note,
                )
            )
    if applied_merges:
        logs.append(
            f"ledger:merge — {len(applied_merges)} duplicated requirement(s) resolved to "
            "a single owner; cross-referenced elsewhere, not restated."
        )

    # CUT — applied. Lowest-scoring content first; a section carrying
    # evaluation points is never cut below its floor; a section this pass's
    # MERGE made the sole owner of a requirement's detail is never cut at
    # all in the same pass (see module note above).
    #
    # Qualification guard: over-length proposals get disqualified. Prefer
    # dropping padding / unneeded whole sections before trimming trust
    # anchors. Keep bios, case studies, certifications, insurance, budget,
    # closing, and scored requirement owners.
    applied_cuts: list[AppliedCutAction] = list(removed_admin_stubs)
    page_limit = resolve_page_limit(getattr(rfp, "page_limit", None), rfp_text)

    # Always strip padding-only sections (checklist dumps, static duplicates)
    # even when no page limit is known — they dilute trust and burn pages.
    padding_removed: list[AppliedCutAction] = []
    kept_after_padding: list[ProposalSection] = []
    for section in sections:
        if _is_padding_only_section(section) and not _is_trust_anchor_section(
            section, ledger, protected_owner_ids=protected_owner_ids
        ):
            padding_removed.append(
                AppliedCutAction(
                    section_id=section.id,
                    section_title=section.title or section.id,
                    words_removed=_word_count(section.content),
                    had_evaluation_points=_section_evaluation_points(ledger, section.id) > 0,
                )
            )
            changed = True
            continue
        kept_after_padding.append(section)
    if padding_removed:
        sections = kept_after_padding
        applied_cuts.extend(padding_removed)
        logs.append(
            f"ledger:remove-padding — removed {len(padding_removed)} unneeded "
            "checklist/duplicate section(s) so the proposal stays lean and "
            "qualified: "
            + ", ".join(f'"{c.section_title[:70]}"' for c in padding_removed[:6])
        )

    if page_limit and page_limit > 0:
        budget_words = int(page_limit * _WORDS_PER_PAGE * _PAGE_BUDGET_HEADROOM)
        current_words = sum(_word_count(s.content) for s in sections)
        overage = current_words - budget_words
        if overage > 0:
            # 1) Drop whole dispensable sections (never trust anchors).
            # Sort: non-anchors first (0), then lowest evaluation points, then longest.
            drop_order = sorted(
                range(len(sections)),
                key=lambda i: (
                    1
                    if _is_trust_anchor_section(
                        sections[i], ledger, protected_owner_ids=protected_owner_ids
                    )
                    else 0,
                    _section_evaluation_points(ledger, sections[i].id),
                    -_word_count(sections[i].content),
                ),
            )
            remaining = overage
            whole_drop_before = len(applied_cuts)
            drop_ids: set[str] = set()
            for idx in drop_order:
                if remaining <= 0:
                    break
                section = sections[idx]
                if _is_trust_anchor_section(
                    section, ledger, protected_owner_ids=protected_owner_ids
                ):
                    continue
                words = _word_count(section.content)
                if words <= 0:
                    continue
                drop_ids.add(section.id)
                remaining -= words
                changed = True
                applied_cuts.append(
                    AppliedCutAction(
                        section_id=section.id,
                        section_title=section.title or section.id,
                        words_removed=words,
                        had_evaluation_points=_section_evaluation_points(ledger, section.id)
                        > 0,
                    )
                )
            if drop_ids:
                sections = [s for s in sections if s.id not in drop_ids]
                whole_drops = applied_cuts[whole_drop_before:]
                logs.append(
                    f"ledger:cut-sections — dropped {len(whole_drops)} unneeded "
                    f"section(s) ({sum(c.words_removed for c in whole_drops)} words) "
                    f"to stay within the {page_limit}-page qualification budget "
                    f"({budget_words} words with headroom)."
                )

            # 2) Trim trailing paragraphs on remaining non-protected sections.
            overage = sum(_word_count(s.content) for s in sections) - budget_words
            if overage > 0:
                ordered_indexes = sorted(
                    range(len(sections)),
                    key=lambda i: (
                        _section_evaluation_points(ledger, sections[i].id),
                        -_word_count(sections[i].content),
                    ),
                )
                remaining = overage
                page_cuts_before = len(applied_cuts)
                for idx in ordered_indexes:
                    if remaining <= 0:
                        break
                    section = sections[idx]
                    if section.id in protected_owner_ids:
                        logs.append(
                            f"ledger:cut — declined to trim {section.title!r} "
                            f"({section.id}): this pass's MERGE made it the sole "
                            "bearer of a consolidated requirement's detail; "
                            "protected from cutting in the same pass."
                        )
                        continue
                    points = _section_evaluation_points(ledger, section.id)
                    floor = (
                        _SCORED_SECTION_FLOOR_WORDS
                        if points > 0
                        or _is_trust_anchor_section(
                            section, ledger, protected_owner_ids=protected_owner_ids
                        )
                        else _UNSCORED_SECTION_FLOOR_WORDS
                    )
                    new_content, removed = _trim_trailing_paragraphs(
                        section.content or "",
                        max_words_to_remove=remaining,
                        floor_words=floor,
                    )
                    if removed <= 0:
                        continue
                    sections[idx] = section.model_copy(update={"content": new_content})
                    remaining -= removed
                    changed = True
                    applied_cuts.append(
                        AppliedCutAction(
                            section_id=section.id,
                            section_title=section.title,
                            words_removed=removed,
                            had_evaluation_points=points > 0,
                        )
                    )
                page_cuts = applied_cuts[page_cuts_before:]
                if page_cuts:
                    total_removed = sum(c.words_removed for c in page_cuts)
                    logs.append(
                        f"ledger:cut — removed {total_removed} word(s) across "
                        f"{len(page_cuts)} section(s) to fit the {page_limit}-page budget "
                        f"({budget_words} words with headroom)."
                    )
                if remaining > 0:
                    logs.append(
                        f"ledger:cut — still {remaining} word(s) over budget after "
                        "protecting trust anchors (bios, case studies, certifications, "
                        "insurance, budget, closing); review manually before submit."
                    )

    added_titles = [a.section_title for a in applied_additions]
    merged_owner_titles = sorted({m.owner_section_title for m in applied_merges})
    cut_titles = [c.section_title for c in applied_cuts]
    logger.info(
        "ledger:reconcile rfp_id=%s added=%d merged=%d cut=%d "
        "added_titles=%s merged_owner_titles=%s cut_titles=%s",
        getattr(rfp, "id", None),
        len(applied_additions),
        len(applied_merges),
        len(applied_cuts),
        added_titles,
        merged_owner_titles,
        cut_titles,
    )

    if not changed:
        return LedgerReconcileResult(
            draft=draft,
            changed=False,
            applied_additions=[],
            applied_merges=[],
            applied_cuts=[],
            logs=logs,
            advisory_scored_criteria=advisory_scored_criteria,
            advisory_submission_instructions=advisory_submission_instructions,
            declined_addition_count=declined_addition_count,
            declined_addition_titles=declined_addition_titles,
            declined_addition_reason=declined_addition_reason,
            built_ledger=built_ledger,
        )

    now = datetime.now(timezone.utc).isoformat()
    new_draft = draft.model_copy(update={"sections": sections, "updated_at": now})
    return LedgerReconcileResult(
        draft=new_draft,
        changed=True,
        applied_additions=applied_additions,
        applied_merges=applied_merges,
        applied_cuts=applied_cuts,
        logs=logs,
        advisory_scored_criteria=advisory_scored_criteria,
        advisory_submission_instructions=advisory_submission_instructions,
        declined_addition_count=declined_addition_count,
        declined_addition_titles=declined_addition_titles,
        declined_addition_reason=declined_addition_reason,
        built_ledger=built_ledger,
    )


# ---------------------------------------------------------------------------
# ADD content drafting (Task 10).
#
# reconcile_requirement_ledger above stays pure/synchronous/zero-LLM on
# purpose — its own test suite (test_scan_rfp_reconciler.py,
# test_scan_rfp_reconciler_wiring.py) exercises it with no LLM configured and
# asserts the [MANUAL FILL] stub verbatim. draft_added_requirement_sections is
# a separate, async, best-effort pass the caller runs immediately afterward,
# scoped to exactly the sections THIS pass's applied_additions just created.
# That scoping is what keeps ADD idempotent even with drafting wired in: a
# second reconcile call finds those section ids already present, so
# applied_additions is empty and this function is never even invoked again —
# a drafted section is never redrafted, and a still-stub section from a prior
# failed drafting attempt is not silently retried into a different content on
# a later click (matches the deterministic-on-replay contract the ADD tests
# already lock in).
#
# Reuses the real drafting stack instead of inventing a new one:
#   - app.services.proposal_intelligence.jit_retrieval.retrieve_for_section
#     (zero LLM calls — Supermemory search + Evidence Trust Gate)
#   - app.services.proposal_section_editor.REFINE_QUERIES_PROMPT (query
#     planning) and SECTION_REDRAFT_PROMPT (drafting) — the same prompts
#     _redraft_rfp_section uses for an existing section, called here directly
#     through llm.chat_json instead of the tool-calling agent wrapper so the
#     call count per section is exactly bounded (agent tool loops are not).
#   - app.services.proposal_brand_voice.{resolve_voice_context,
#     format_brand_voice_block, classify_section_register} for the same
#     dual-layer zö voice block every other section writer uses.
#   - app.services.proposal_voice_enforcement.enforce_narrative_voice for the
#     same post-write register fix-up as a normal redraft.
#
# LLM budget: at most one query-planning call + one drafting call per added
# section (both node_name-routed — see llm_routing.py). Degrades to the
# existing [MANUAL FILL] placeholder, never raises, on any failure: LLM not
# configured, retrieval empty, planner returns no queries, or the drafted
# content is too short to be substantial.
# ---------------------------------------------------------------------------


async def _draft_one_added_section(
    *,
    section: ProposalSection,
    addition: AppliedRequirementAddition,
    rfp: RfpRecord,
    rfp_context: str,
    brand_voice: dict[str, Any] | None,
    kb_zo_voice: str,
) -> ProposalSection | None:
    """Draft real content for one newly-added [MANUAL FILL] stub section.

    Returns the updated section on success, or None to signal "leave the
    stub exactly as reconcile_requirement_ledger produced it" (caller keeps
    the placeholder). Never raises.
    """
    from app.services import llm
    from app.services.proposal_brand_voice import (
        classify_section_register,
        format_brand_voice_block,
    )
    from app.services.proposal_intelligence.jit_retrieval import retrieve_for_section
    from app.services.proposal_intelligence.schemas import RetrievalEntry
    from app.services.proposal_langchain_agents import content_from_agent_payload
    from app.services.proposal_section_editor import (
        REFINE_QUERIES_PROMPT,
        SECTION_REDRAFT_PROMPT,
        _format_evidence,
    )
    from app.services.proposal_section_quality import section_content_is_substantial
    from app.services.proposal_voice_enforcement import enforce_narrative_voice

    try:
        query_raw, query_provider = await llm.chat_json_soft(
            [
                {"role": "system", "content": REFINE_QUERIES_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"Client: {rfp.client}\n"
                        f"Sector: {rfp.sector}\n"
                        f"Section: {section.title}\n"
                        f"Requirements: {[addition.requirement_text]}\n"
                        "Retrieval focus: []\n"
                        "Prior queries (DO NOT repeat):\n(none)\n\n"
                        "User feedback:\nNo section in the draft addressed this mandatory "
                        "RFP requirement yet. Plan queries to find zö agency evidence to "
                        "draft it from scratch.\n\n"
                        "Current draft (insufficient):\n(none — new section, write from scratch)"
                    ),
                },
            ],
            max_tokens=16000,
            temperature=0.35,
            node_name="ledger_add_query_planner",
        )
    except Exception:
        logger.warning(
            "ledger:add-draft — query planning raised for %s", addition.section_id,
            exc_info=True,
        )
        return None

    if query_provider == "failed":
        return None

    raw_queries = query_raw.get("queries") if isinstance(query_raw, dict) else None
    queries = (
        [str(q).strip()[:240] for q in raw_queries if str(q).strip()]
        if isinstance(raw_queries, list)
        else []
    )
    if not queries:
        return None

    try:
        entry = RetrievalEntry(
            sectionId=section.id,
            requiredAssets=[addition.requirement_text[:200]],
            queries=queries,
        )
        evidence = await retrieve_for_section(entry, rfp_client=rfp.client)
    except Exception:
        logger.warning(
            "ledger:add-draft — retrieval raised for %s", addition.section_id,
            exc_info=True,
        )
        return None

    if not evidence:
        return None

    register = classify_section_register(
        section_id=section.id, title=section.title, zo_mode=section.mode
    )
    voice_block = format_brand_voice_block(
        brand_voice, kb_zo_voice=kb_zo_voice, rfp_client=rfp.client, register=register
    )

    user_block = (
        f"BRAND VOICE (mandatory — maintain throughout):\n{voice_block}\n\n"
        f"Client: {rfp.client}\n"
        f"Sector: {rfp.sector}\n"
        f"RFP: {rfp.title}\n"
        f"Section: {section.title}\n"
        f"Word target: {section.word_target} (stay at or under — be concise)\n"
        f"Requirements:\n- {addition.requirement_text}\n\n"
        "FORMAT (mandatory):\n"
        "- Prefer short paragraphs, markdown bullets, and markdown tables for process/"
        "phases/roles/cadence.\n"
        "- Do not write a long essay. Hit the requirement tightly.\n"
        "- When a table/timeline/swimlane would help evaluators, set designerNote and/or "
        "insert [DESIGNER NOTE: concrete layout hint].\n\n"
        "User edit request:\nNo section in the draft addressed this mandatory RFP "
        "requirement. Write the section from scratch using ONLY the evidence corpus "
        "below. If one specific required fact is not in the evidence, insert a narrow "
        "[VERIFY: that one field] for that fact only — never a whole-section stub, and "
        "never invent the fact.\n\n"
        "Previous draft:\n(none — write from scratch)\n\n"
        f"RFP excerpt:\n{rfp_context[:4000]}\n\n"
        f"Evidence corpus:\n{_format_evidence(evidence)}\n"
    )
    from app.services.proposal_drafting_prompts import (
        MODULAR_APPROACH_BLOCK,
        is_modular_approach_section,
    )

    if is_modular_approach_section(section.title or ""):
        user_block = f"{MODULAR_APPROACH_BLOCK}\n\n{user_block}"

    try:
        draft_raw, draft_provider = await llm.chat_json_soft(
            [
                {"role": "system", "content": SECTION_REDRAFT_PROMPT},
                {"role": "user", "content": user_block},
            ],
            max_tokens=16000,
            temperature=0.3,
            node_name="ledger_add_section_draft",
        )
    except Exception:
        logger.warning(
            "ledger:add-draft — drafting call raised for %s", addition.section_id,
            exc_info=True,
        )
        return None

    if draft_provider == "failed":
        return None

    content = enforce_narrative_voice(
        content_from_agent_payload(draft_raw if isinstance(draft_raw, dict) else {}),
        section_id=section.id,
        title=section.title,
        zo_mode=section.mode,
    )
    if not content.strip() or not section_content_is_substantial(section, content):
        return None

    designer_note = None
    if isinstance(draft_raw, dict):
        designer_note = (
            draft_raw.get("designerNote") or draft_raw.get("designer_note") or None
        )
        if isinstance(designer_note, str):
            designer_note = designer_note.strip() or None
        else:
            designer_note = None
        if (
            designer_note
            and content
            and "[DESIGNER NOTE:" not in content.upper()
        ):
            content = f"{content.rstrip()}\n\n[DESIGNER NOTE: {designer_note}]"

    return section.model_copy(
        update={
            "content": content,
            "status": "generated",
            "designer_note": designer_note,
        }
    )


async def draft_added_requirement_sections(
    *,
    draft: ProposalDraft,
    applied_additions: list[AppliedRequirementAddition],
    research: ProposalResearchCache | None,
    rfp: RfpRecord,
    rfp_context: str,
) -> tuple[ProposalDraft, list[str]]:
    """Replace each freshly-added [MANUAL FILL] stub with drafted content.

    Only touches sections named in applied_additions — the exact set
    reconcile_requirement_ledger just added THIS pass. See the module note
    above for why that scoping keeps ADD idempotent with drafting wired in.
    Never raises: any per-section failure leaves that section's placeholder
    untouched and logs why.
    """
    if not applied_additions:
        return draft, []

    from app.services import llm

    if not llm.is_configured():
        return draft, ["ledger:add-draft — LLM not configured, left [MANUAL FILL] placeholder(s)."]

    brand_voice_dict: dict[str, Any] | None = None
    kb_zo_voice = ""
    try:
        from app.services.proposal_brand_voice import resolve_voice_context

        brand_voice_dict, kb_zo_voice = await resolve_voice_context(
            rfp=rfp,
            rfp_context=rfp_context,
            brand_voice=(
                research.brand_voice.model_dump(by_alias=True)
                if research and research.brand_voice
                else None
            ),
        )
    except Exception:
        logger.warning("ledger:add-draft — voice context resolution failed", exc_info=True)

    sections = list(draft.sections)
    by_id = {s.id: i for i, s in enumerate(sections)}
    logs: list[str] = []
    changed = False

    for addition in applied_additions:
        idx = by_id.get(addition.section_id)
        if idx is None:
            continue
        section = sections[idx]
        try:
            drafted = await _draft_one_added_section(
                section=section,
                addition=addition,
                rfp=rfp,
                rfp_context=rfp_context,
                brand_voice=brand_voice_dict,
                kb_zo_voice=kb_zo_voice,
            )
        except Exception:
            logger.exception(
                "ledger:add-draft — unexpected failure for %s; keeping [MANUAL FILL] placeholder",
                addition.section_id,
            )
            logs.append(
                f"ledger:add-draft — {addition.section_id}: drafting failed unexpectedly, "
                "kept [MANUAL FILL] placeholder."
            )
            continue

        if drafted is None:
            logs.append(
                f"ledger:add-draft — {addition.section_id}: no KB evidence or LLM output "
                "available, kept [MANUAL FILL] placeholder for human fill."
            )
            continue

        sections[idx] = drafted
        changed = True
        logs.append(
            f"ledger:add-draft — {addition.section_id}: drafted "
            f"{len((drafted.content or '').split())} word(s) from KB evidence in zö voice."
        )

    if not changed:
        return draft, logs

    now = datetime.now(timezone.utc).isoformat()
    return draft.model_copy(update={"sections": sections, "updated_at": now}), logs


async def apply_scan_ledger_pass(
    *,
    rfp_id: str,
    draft: ProposalDraft,
    research: ProposalResearchCache | None,
    rfp: RfpRecord,
    rfp_text: str,
) -> tuple[ProposalDraft, ProposalResearchCache | None, LedgerReconcileResult, list[str]]:
    """Shared Scan-RFP ledger pass: MERGE / CUT / ADD + draft new stubs.

    Used by both mode=full and mode=verify_scrub_only so unrequested sections
    are trimmed/merged and missing required narrative tabs are added.
    """
    from datetime import datetime, timezone

    from app.services.proposal_repository import asave_proposal_draft, asave_research_cache

    ledger_result = reconcile_requirement_ledger(
        draft=draft, research=research, rfp=rfp, rfp_text=rfp_text
    )
    if ledger_result.built_ledger is not None:
        research = (
            research
            or ProposalResearchCache(
                rfpId=rfp_id, updatedAt=datetime.now(timezone.utc).isoformat()
            )
        ).model_copy(update={"requirement_ledger": ledger_result.built_ledger})
        await asave_research_cache(research)
    if ledger_result.changed:
        draft = ledger_result.draft
        await asave_proposal_draft(draft)

    ledger_draft_logs: list[str] = []
    if ledger_result.applied_additions:
        draft, ledger_draft_logs = await draft_added_requirement_sections(
            draft=draft,
            applied_additions=ledger_result.applied_additions,
            research=research,
            rfp=rfp,
            rfp_context=rfp_text,
        )
        if ledger_draft_logs:
            await asave_proposal_draft(draft)

    return draft, research, ledger_result, ledger_draft_logs


def scan_budget_revenue_gaps(
    *,
    draft: ProposalDraft,
    research: ProposalResearchCache | None,
) -> list[ComplianceGap]:
    """Budget math gaps from canonical budget fields — no prose regex."""
    budget = research.budget if research else None
    if not budget:
        return []

    idx = find_budget_section_index(draft.sections)
    if idx is None:
        return []
    section = draft.sections[idx]

    revenue = float(budget.agency_revenue_estimate or 0)
    derived = derive_commission_agency_revenue(budget) or 0.0
    if revenue > 0 or (derived > 0 and abs(revenue - derived) < 1.0):
        return []

    commission_style = is_commission_style_budget(budget)
    lump = float(budget.lump_sum_total or 0)
    if not commission_style and lump <= 0:
        return []

    if commission_style and revenue <= 0:
        message = (
            "Budget Summary shows $0 agency revenue for a commission-model RFP — "
            "set agencyRevenueEstimate from commission rate × pass-through (or line items)"
        )
    else:
        message = (
            "Budget Summary shows $0 agency revenue — reconcile agencyRevenueEstimate "
            "with budget line items and commission structure"
        )

    return [
        ComplianceGap(
            section_id=section.id,
            section_title=section.title,
            category="budget_revenue",
            message=message,
            rfp_requirement="itemized budget with correct agency fee / commission totals",
            excerpt=(section.content or "")[:200],
            repair_hint=(
                "Run Budget refinery or reconcile: agencyRevenueEstimate must match "
                "commission structure and line-item subtotals."
            ),
        )
    ]


def scan_submission_pricing_flag_gaps(
    *,
    draft: ProposalDraft,
    research: ProposalResearchCache | None,
) -> list[ComplianceGap]:
    """Internal pricing flags on budget object or budget section text."""
    budget = research.budget if research else None
    idx = find_budget_section_index(draft.sections)
    if idx is None:
        return []

    section = draft.sections[idx]
    content = section.content or ""
    has_manuscript_flag = _text_contains(content, "[PRICING FLAG") or _text_contains(
        content, "## Pricing Flags"
    )
    has_budget_flags = bool(budget and budget.pricing_flags)

    if not has_manuscript_flag and not has_budget_flags:
        return []

    return [
        ComplianceGap(
            section_id=section.id,
            section_title=section.title,
            category="budget",
            message=(
                "Budget still has internal pricing flags — resolve fee decisions with Sonja, "
                "then regenerate or reconcile budget before submission"
            ),
            rfp_requirement="clean cost proposal without internal review notes",
            excerpt=content[:280],
            repair_hint=(
                "Apply scope-adjustment rates to line items, clear pricing_flags in budget "
                "refinery, and re-sync the budget section."
            ),
        )
    ]


def scan_rfp_compliance_gaps(
    *,
    draft: ProposalDraft,
    research: ProposalResearchCache | None,
    rfp: RfpRecord,
) -> list[ComplianceGap]:
    """Dynamic compliance gaps: Phase 2 uncovered reqs, open tags, canonical budget."""
    _ = rfp  # reserved for future RFP-record fields; gaps come from research + draft
    gaps: list[ComplianceGap] = []
    gaps.extend(scan_open_submission_tags(draft=draft))
    gaps.extend(scan_uncovered_requirement_gaps(draft=draft, research=research))
    gaps.extend(scan_budget_revenue_gaps(draft=draft, research=research))
    gaps.extend(scan_submission_pricing_flag_gaps(draft=draft, research=research))
    return gaps


def compliance_gaps_for_section(
    gaps: list[ComplianceGap],
    section_id: str,
) -> list[ComplianceGap]:
    return [g for g in gaps if g.section_id == section_id]


def sections_with_compliance_gaps(
    draft: ProposalDraft,
    research: ProposalResearchCache | None,
    rfp: RfpRecord,
) -> set[str]:
    return {g.section_id for g in scan_rfp_compliance_gaps(draft=draft, research=research, rfp=rfp)}


def build_rfp_compliance_repair_brief(
    gaps: list[ComplianceGap],
    *,
    draft: ProposalDraft,
    research: ProposalResearchCache | None,
    rfp: RfpRecord,
) -> str:
    lines = [
        "RFP COMPLIANCE REPAIR — fill required submission data; do not defer to unnamed attachments.",
        f"Client: {rfp.client} | RFP: {rfp.title}",
        "",
        "Rules:",
        "1. Search KB tools (references, team bios, certifications, 06_WON, 07_FIN, insurance) for facts.",
        "2. NEVER write 'upon request', 'Attachment 05', or 'will be provided separately' for required data.",
        "3. NEVER show $0 for agency revenue / commission when fees apply — use commissionRate × pass-through or agency_fee line items.",
        "4. If KB lacks a fact after search, use [MANUAL FILL: owner — specific field] — not bare VERIFY.",
        "5. Preserve strong narrative; add compliance sentences/tables where gaps exist.",
        "6. Address each uncovered requirement from the mapped RFP section list below.",
        "",
        "## Compliance gaps to fix",
    ]
    for index, gap in enumerate(gaps, start=1):
        lines.append(f"{index}. **[{gap.category}]** {gap.message}")
        lines.append(f"   RFP requires: {gap.rfp_requirement}")
        lines.append(f"   Repair: {gap.repair_hint}")
        if gap.excerpt:
            lines.append(f'   Current excerpt: "{gap.excerpt[:200]}…"')

    mapped = research.rfp_sections if research else []
    if mapped:
        lines.extend(["", "## Mapped RFP section requirements (context)"])
        for m in mapped[:12]:
            reqs = (m.requirements or [])[:4]
            if reqs:
                lines.append(f"- **{m.title}:**")
                for req in reqs:
                    lines.append(f"  - {req[:160]}")

    return "\n".join(lines)


def compliance_gaps_to_presubmit_issues(
    gaps: list[ComplianceGap],
) -> list:
    from app.models.proposal import PreSubmitIssue

    return [
        PreSubmitIssue(
            severity="critical",
            category="compliance",
            message=gap.message,
            sectionId=gap.section_id,
            sectionTitle=gap.section_title,
            excerpt=gap.excerpt[:200] if gap.excerpt else None,
        )
        for gap in gaps
    ]


async def run_rfp_compliance_polish_pass(
    rfp_id: str,
    *,
    rfp: RfpRecord | None = None,
    draft: ProposalDraft | None = None,
    research: ProposalResearchCache | None = None,
) -> tuple[ProposalDraft, list[str]]:
    """LLM repair for RFP requirement gaps surfaced by Phase 2 research."""
    from app.services.proposal_common import ProposalError, load_rfp_for_proposal
    from app.services.proposal_repository import aget_proposal_draft, aget_research_cache
    from app.services.proposal_self_edit_loop import _repair_one_section

    if rfp is None:
        rfp, _, _ = load_rfp_for_proposal(rfp_id)
    draft = draft or await aget_proposal_draft(rfp_id)
    if not draft:
        raise ProposalError("No proposal draft for RFP compliance polish.", status_code=400)
    research = research if research is not None else await aget_research_cache(rfp_id)
    budget = research.budget if research else None

    gaps = scan_rfp_compliance_gaps(draft=draft, research=research, rfp=rfp)
    if not gaps:
        return draft, []

    by_section: dict[str, list[ComplianceGap]] = {}
    for gap in gaps:
        by_section.setdefault(gap.section_id, []).append(gap)

    logs: list[str] = []
    rfp_client = rfp.client
    rfp_title = rfp.title

    for section_id, section_gaps in by_section.items():
        from app.services.proposal_fulfill_guard import section_id_preserved_in_fulfill

        if section_id_preserved_in_fulfill(section_id, draft.sections):
            logs.append(f"{section_id}: skipped — preserved section")
            continue
        brief = build_rfp_compliance_repair_brief(
            section_gaps,
            draft=draft,
            research=research,
            rfp=rfp,
        )
        sid, improved, detail = await _repair_one_section(
            rfp_id,
            section_id,
            use_senior_editor=False,
            rfp=rfp,
            rfp_client=rfp_client,
            rfp_title=rfp_title,
            budget=budget,
            repair_message=brief,
        )
        log_line = f"{sid}: {'fixed' if improved else 'unchanged'} — {detail[:120]}"
        logs.append(log_line)
        draft = await aget_proposal_draft(rfp_id) or draft

    remaining = scan_rfp_compliance_gaps(draft=draft, research=research, rfp=rfp)
    if remaining:
        logger.warning(
            "RFP compliance polish for %s: %d gap(s) remain",
            rfp_id,
            len(remaining),
        )

    return draft, logs
