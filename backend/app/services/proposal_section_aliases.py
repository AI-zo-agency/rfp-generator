"""Curated procurement-section alias table.

``_scored_token_overlap_match`` (proposal_intelligence/assembler.py) measures
6/10 on realistic RFP wording-variant pairs because four of them are standard
procurement synonyms that share zero (or only "boring", see
``_BORING_SHARED_TOKENS``) tokens — no lexical overlap scoring can bridge
"Cover Letter" / "Letter of Transmittal", they simply don't share words. This
table closes exactly those gaps with a small, curated, deterministic list.
No LLM call, no inference — this is data, and it is data other agents in this
codebase (or a human editing this file) can audit line by line.

CONSERVATIVE BY DESIGN. Every group below is an equivalence that holds in
procurement **generally** — never a guess, never client-specific. When in
doubt, an entry is left OUT: a wrong alias produces a false "satisfied" that
HIDES a real requirement from ``RequirementLedger.missing()`` forever, which
is the single failure mode this whole ledger exists to prevent. A missed
alias just leaves a requirement in ``missing()`` for a human to look at —
annoying, not dangerous. See assembler.py's own module note on the same bias.

MATCHING CONTRACT (enforced in assembler._alias_whole_concept_match, not
here): an alias phrase may satisfy a requirement or section title only when
it names that side's ENTIRE meaningful token set — never a single token
buried inside a longer, more specific ask. "Timeline" therefore matches
something that normalizes to exactly "Timeline"; it does NOT match "Provide
a timeline for subcontractor onboarding and describe your quality assurance
methodology", whose token set is nowhere near {"timeline"}. This is why
short, single-word phrases (e.g. "timeline", "about us") are safe to list
here at all — the whole-set-equality contract, not phrase length, is what
keeps them from over-firing.

Each group is a set of phrasings for ONE procurement-section concept. Groups
must never share a phrase, and (by construction, verified by tests) must
never reduce to the same meaningful-token-set as another group's phrase —
that would silently merge two different concepts.
"""

from __future__ import annotations

PROPOSAL_SECTION_ALIAS_GROUPS: tuple[frozenset[str], ...] = (
    # Cover letter -- the transmittal document accompanying a submission.
    # Standard in nearly all procurement: RFPs from municipal to federal use
    # "Letter of Transmittal" as the formal name for what is colloquially a
    # cover letter.
    frozenset({
        "cover letter",
        "letter of transmittal",
    }),
    # Key personnel -- who is on the team and how it is staffed. "Staffing
    # Plan" and "Key Personnel" are used interchangeably across construction,
    # professional-services, and public-sector RFPs. "Key Staff" is the same
    # concept in fewer words.
    frozenset({
        "key personnel",
        "staffing plan",
        "key staff",
    }),
    # Project schedule -- the proposed delivery timeline. "Timeline" alone is
    # the single most common shorthand for this section in modern RFPs.
    frozenset({
        "project schedule",
        "timeline",
        "project timeline",
    }),
    # Company overview -- the firm's background/introduction section, as
    # distinct from qualifications, experience, or key personnel.
    frozenset({
        "company overview",
        "about us",
        "firm overview",
    }),
    # Executive summary -- the proposal's opening synopsis. "Summary of
    # Approach" is a common substitute title in shorter-form RFPs where the
    # opening summary doubles as a synopsis of the technical approach.
    frozenset({
        "executive summary",
        "summary of approach",
    }),
)
