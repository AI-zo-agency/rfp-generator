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
    "required_content", "scored_criterion", "form", "eligibility"
]


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
