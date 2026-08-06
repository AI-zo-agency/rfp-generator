"""Curated procurement-section alias table.

``_scored_token_overlap_match`` (proposal_intelligence/assembler.py) measures
6/10 on realistic RFP wording-variant pairs because several of them are
standard procurement synonyms that share zero (or only "boring", see
``_BORING_SHARED_TOKENS``) tokens — no lexical overlap scoring can bridge
"Project Schedule" / "Timeline", they simply don't share words. This table
closes those gaps with a small, curated, deterministic list. No LLM call, no
inference — this is data, and it is data other agents in this codebase (or a
human editing this file) can audit line by line.

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

A GROUP IS A MUTUAL EQUIVALENCE, NOT A LIST OF PAIRS. Adding an Nth phrase
to a group does not add one equivalence, it adds N-1 of them, and every one
of those cross-pairs is live whether or not anyone measured it. Task 8's
first cut shipped two Criticals from exactly this blind spot:

  * "staffing plan" was put in the Key Personnel group. Reproduced through
    derive_legacy_fields: an RFP scoring "Key Personnel" (15 pts) and
    "Staffing Plan" (10 pts) as two separate criteria, with an outline
    containing only a "Staffing Plan" section, marked BOTH satisfied — the
    15-point "who is on the team, with resumes" ask was silently discharged
    by a section about staffing methodology and never reached missing(), so
    the amendment never gave it a section.
  * "summary of approach" was put in the Executive Summary group. Because
    "of" is a stopword it reduces to {summary, approach} — the identical
    token set to "Approach Summary", an ordinary sub-heading inside a
    Technical Approach section. Reproduced: a 20-point "Executive Summary"
    criterion absorbed by that subsection.

Both are removed. ``tests/test_section_aliases.py`` now enumerates EVERY
same-group cross-pair with a written justification and fails if a new group
member appears without one — that test, not review, is what keeps this
honest.

Two questions to answer before adding any phrase:
  1. Is it the same CONCEPT as every existing member, or merely the same
     topic area? (Key Personnel = WHO is on the team; Staffing Plan = HOW
     the team is assembled and managed. Same topic, different asks,
     commonly scored separately with separate weights. NOT aliases.)
  2. Does its normalized token set also describe some ordinary DIFFERENT
     section or sub-heading? Stopwords and the <3-character filter make this
     non-obvious — "Summary of Approach" and "Approach Summary" are the same
     token set, and "About Us" reduces to just {about}.
"""

from __future__ import annotations

PROPOSAL_SECTION_ALIAS_GROUPS: tuple[frozenset[str], ...] = (
    # Cover letter -- the transmittal document accompanying a submission.
    # Standard in nearly all procurement: RFPs from municipal to federal use
    # "Letter of Transmittal" as the formal name for what is colloquially a
    # cover letter. No RFP scores both as separate criteria; there is only
    # ever one such letter in a submission.
    frozenset({
        "cover letter",
        "letter of transmittal",
    }),
    # Key personnel -- WHO is on the team: the named individuals, their
    # resumes/bios, their roles. "Key Staff" is the identical ask in a
    # synonym. Deliberately does NOT include "staffing plan", "staffing
    # approach" or "organizational chart": those are the HOW (team assembly,
    # allocation, succession, management structure), routinely scored as a
    # separate criterion with its own weight. See the module docstring's C1.
    frozenset({
        "key personnel",
        "key staff",
    }),
    # Project schedule -- the proposed delivery timeline. "Timeline" alone is
    # the single most common shorthand for this section in modern RFPs. All
    # three phrasings name one artifact; an RFP does not score a schedule and
    # a timeline as two separate criteria.
    frozenset({
        "project schedule",
        "timeline",
        "project timeline",
    }),
    # Company overview -- the firm's background/introduction section, as
    # distinct from qualifications, experience, or key personnel. NOTE: "about
    # us" reduces to {about} ("us" is filtered as a <3-character token), so a
    # section titled bare "About" also matches this concept. That is accepted
    # deliberately, not by accident -- see test_section_aliases.py's
    # SingleTokenAliasReductionTests, which pins every single-token reduction
    # so a future addition like "plan" or "schedule" cannot slip in unnoticed.
    frozenset({
        "company overview",
        "about us",
        "firm overview",
    }),
    # Executive summary: NO GROUP. The only candidate alias, "summary of
    # approach", collides with the ordinary sub-heading "Approach Summary"
    # on token set {summary, approach} and let a 20-point criterion be
    # absorbed by a subsection (module docstring, C2). "Executive Summary" /
    # "Summary of Approach" is therefore left as a documented MISS —
    # consistent with the "when in doubt leave it out" policy already applied
    # to bare "schedule". A missed alias costs a human one dismissal in
    # missing(); a wrong one hides a scored requirement forever.
)
