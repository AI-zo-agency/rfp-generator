# Winning Pattern Intelligence Design

## Scope

V1 adds a Writing Intelligence planner that learns from similar won proposals without turning old proposal text into drafting evidence.

Included:
- Add a Winning Pattern Intelligence Agent in Phase 2.
- Use current Supermemory search for top similar won proposals.
- Extract structured writing patterns only, not proposal prose.
- Persist `winningPattern` on each `sectionPlan`.
- Feed `winningPattern` to Phase 3 writers as planning guidance.
- Keep Phase 3 JIT retrieval focused on factual evidence.
- Add a UI control to continue after Sections 1-3 when those sections are already acceptable.

Not included:
- New Supermemory metadata schema.
- Proposal tagging.
- Section-level indexing.
- Embedding migration.
- Re-indexing historical proposals.
- New retrieval infrastructure.

## Architecture Principle

Winning proposals are used to learn how successful proposals are structured and argued, not to reuse or paraphrase previous proposal content. The Winning Pattern Intelligence Agent extracts reusable patterns, persuasive techniques, and section structures while intentionally discarding proposal-specific prose.

Won proposals are writing intelligence, not evidence.

## Phase 2 Writing Flow

```text
Dynamic Section Planner
        |
        v
Winning Pattern Intelligence Agent
        |
        v
Section Strategy Planner
        |
        v
Evidence Retrieval Planner
        |
        v
Proposal Execution Plan
```

## Data Contract

Each `SectionPlan` gains a `winningPattern` object:

```text
winningPattern {
  sourceWonProposals[]       # ids/titles only, no body text
  openingPattern             # how winning sections tend to open
  structureFlow[]            # ordered rhetorical structure
  persuasionTechniques[]     # reusable persuasion moves
  commonDifferentiators[]    # themes winners emphasize
  commonObjections[]         # concerns winning sections preempt
  recommendedWordCount       # typical target for this section
  recommendedVisuals[]       # diagrams/tables that tend to help
  avoid[]                    # patterns to avoid
  commonProofThemes[]        # factual proof categories to retrieve later
  confidence
}
```

The object must never contain copied proposal paragraphs, sentence-level rewrite guidance, or client-specific claims from prior proposals.

## Phase 3 Drafting

For each dynamic section, the drafter receives:
- Proposal Execution Plan.
- Section strategy.
- `winningPattern`.
- Proposal memory.
- Current section evidence from Phase 3 JIT retrieval.

Phase 3 writers use the winning pattern for structure, flow, tone, persuasion, and visual recommendations. They still retrieve and cite only factual evidence such as methodology, case studies, testimonials, references, pricing, bios, company facts, portfolio, images, diagrams, playbooks, and standards.

Writers must not retrieve won proposal prose as a writing reference in V1.

## V2 Follow-Up

After V1 is stable, add Supermemory metadata and section-level indexing so the Winning Pattern Intelligence Agent can query by fields such as `status=won`, `industry`, `client_type`, `service`, `proposal_type`, `delivery_model`, and section themes. That is a separate knowledge-base migration project.

