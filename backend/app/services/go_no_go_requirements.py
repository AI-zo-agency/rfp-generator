"""Extract what an RFP actually requires, and what to search the KB for.

The Go/No-Go planner used to return a flat list of KB search strings. Nothing
recorded *which requirement* a search was meant to answer, so retrieved hits
could not be attributed, and the capability matrix was whatever the model chose
to write — including "Verified" rows for capabilities the KB never contained.

Here the RFP is decomposed into discrete requirements first. Each carries its
own KB queries, so evidence is gathered per requirement and the matrix is built
from (requirement, its own evidence) pairs rather than from model narrative.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

REQUIREMENT_CATEGORIES = ("service", "role", "technical", "compliance", "logistics")


class RfpRequirement(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    requirement: str
    category: str = "service"
    is_core: bool = Field(default=False, alias="isCore")
    rfp_quote: str = Field(default="", alias="rfpQuote")
    kb_queries: list[str] = Field(default_factory=list, alias="kbQueries")


REQUIREMENT_PLANNER_PROMPT = """You decompose an RFP into the discrete capabilities a vendor must have,
and the knowledge-base searches that would prove each one.

The knowledge base contains ONLY zö agency materials — company facts
(01_companyfacts), org structure and bios (02_MasterTemplate, 04_Bio_*),
case studies (03_CS_*), won/finalist proposals (06_WON_*, 07_FIN_*), and the
pricing guide (00_Guide_Pricing). The RFP's buyer is NOT in the knowledge base.

Read the WHOLE excerpt. Enumerate every distinct capability the vendor must
supply — services, staff roles/disciplines, technical/platform requirements,
and compliance obligations. Split bundled scope into separate requirements:
"website redesign including CMS, hosting and content migration" is FOUR
requirements, not one. Do not merge, do not summarise, do not skip items you
suspect the vendor lacks — those matter most.

For EACH requirement give 1-3 kbQueries phrased the way zö's own materials
would describe that work — job titles, tools, deliverables. Search for the
PERSON or the PROJECT that proves it:
  "user experience (UX) design" -> "zo agency UX designer wireframes information architecture 04_Bio"
  "CMS implementation"          -> "zo agency CMS Drupal WordPress implementation developer 03_CS"
Never use the buyer's name as the search subject.

isCore=true when the RFP makes the requirement mandatory, scores it, or it is
central to the scope of work. isCore=false for incidental or optional items.

Return ONLY JSON:
{"requirements":[{"requirement":"...","category":"service|role|technical|compliance|logistics",
  "isCore":true,"rfpQuote":"short verbatim phrase from the RFP","kbQueries":["...","..."]}]}"""


_MAX_REQUIREMENTS = 24
_MAX_QUERIES_PER_REQUIREMENT = 3


def _clean(value: Any, *, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def parse_requirements(raw: dict[str, Any]) -> list[RfpRequirement]:
    """Coerce planner output into requirements, dropping unusable rows."""
    rows = raw.get("requirements")
    if not isinstance(rows, list):
        return []

    out: list[RfpRequirement] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        requirement = _clean(row.get("requirement"), limit=160)
        if len(requirement) < 3:
            continue
        key = requirement.casefold()
        if key in seen:
            continue
        seen.add(key)

        category = _clean(row.get("category"), limit=24).casefold()
        if category not in REQUIREMENT_CATEGORIES:
            category = "service"

        queries_raw = row.get("kbQueries") or row.get("kb_queries") or []
        queries: list[str] = []
        if isinstance(queries_raw, list):
            for query in queries_raw:
                cleaned = _clean(query, limit=200)
                if cleaned:
                    queries.append(cleaned)
        queries = queries[:_MAX_QUERIES_PER_REQUIREMENT]
        # Always search the requirement's own wording too. Model-written queries
        # can drift off-target, and a document that is never retrieved cannot be
        # recovered later — the adjudicator can only judge what came back.
        literal = f"zö agency {requirement} 03_CS 04_Bio 06_WON"
        if literal.casefold() not in {q.casefold() for q in queries}:
            queries.append(literal)

        out.append(
            RfpRequirement(
                requirement=requirement,
                category=category,
                isCore=bool(row.get("isCore") or row.get("is_core")),
                rfpQuote=_clean(row.get("rfpQuote") or row.get("rfp_quote"), limit=240),
                kbQueries=queries,
            )
        )
        if len(out) >= _MAX_REQUIREMENTS:
            break

    logger.info(
        "go_no_go requirements parsed=%d core=%d",
        len(out),
        sum(1 for r in out if r.is_core),
    )
    return out


def all_queries(requirements: list[RfpRequirement]) -> list[str]:
    """Every requirement's KB queries, de-duplicated, order preserved."""
    seen: set[str] = set()
    out: list[str] = []
    for requirement in requirements:
        for query in requirement.kb_queries:
            key = query.casefold()
            if key in seen:
                continue
            seen.add(key)
            out.append(query)
    return out
