"""RequirementLedger — the spine: one requirement, exactly one section.

Built from the compliance matrix (`ComplianceItem`) and evaluation criteria
(`EvaluationCriterion`) that Phase 2 already produces, then persisted so
downstream stages (coverage gate, dedup, ending report) can audit sections
Phase 2 never emitted instead of only iterating `research.rfp_sections`.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

LedgerRequirementSource = Literal[
    "required_content",
    "scored_criterion",
    "form",
    "eligibility",
    "submission_instruction",
]
# "submission_instruction" (added alongside the third instance of the same
# defect class — see proposal_rfp_compliance.py's _ADD_ELIGIBLE_SOURCES
# module note) is deliberately NOT "eligibility". "eligibility" is reserved
# for a go/no-go GATE — whether zö should bid at all (small-business
# set-aside, licensure, bonding capacity) — a decision made once, before a
# draft exists. "submission_instruction" is a compliance obligation on an
# ALREADY-DECIDED bid you COMPLY WITH, not a deliverable you WRITE a
# proposal section to satisfy — a deadline, delivery address, labelling
# instruction, validity period, copy count, or format rule (the phrasings
# _ADMIN_INSTRUCTION_PATTERNS recognises explicitly), OR — as of the fourth
# instance of this defect class, see assembler.py's
# _classify_compliance_source module note — anything else that fails to
# positively read as a narrative deliverable or a form. The classifier is
# fail-closed: it no longer guesses "required_content" for phrasing it does
# not recognise, so a blanket statutory-compliance clause or an unanticipated
# format rule lands here too, still visible to the user, never silently
# turned into a client-facing section. Conflating this with "eligibility"
# would make a real go/no-go signal (mis)appear on every draft's requirement
# ledger. "eligibility" remains unused/reserved; nothing in the ledger
# pipeline currently produces it.


class LedgerRequirement(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    text: str
    source: LedgerRequirementSource = "required_content"
    mandatory: bool = True
    points: float | None = None
    satisfied_by: list[str] = Field(default_factory=list, alias="satisfiedBy")
    kb_queries: list[str] = Field(default_factory=list, alias="kbQueries")


class RequirementLedger(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    requirements: list[LedgerRequirement] = Field(default_factory=list)

    def missing(self) -> list[LedgerRequirement]:
        """Mandatory requirements with no section covering them."""
        return [
            r for r in self.requirements if r.mandatory and not r.satisfied_by
        ]

    def duplicated(self) -> list[LedgerRequirement]:
        """Requirements covered by more than one section."""
        return [r for r in self.requirements if len(r.satisfied_by) > 1]

    def scored(self) -> list[LedgerRequirement]:
        """Requirements that carry evaluation points, highest first."""
        scored = [r for r in self.requirements if r.points is not None]
        return sorted(scored, key=lambda r: r.points or 0.0, reverse=True)
