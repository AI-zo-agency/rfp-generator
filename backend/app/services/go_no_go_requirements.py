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

REQUIREMENT_CATEGORIES = (
    "service",
    "role",
    "technical",
    "compliance",
    "logistics",
    "submission",
)

# Proposal-content asks. Real requirements — the proposal is non-responsive
# without them — but they are authored at submission time, not demonstrated
# from past work, so no knowledge base can ever evidence them. Scoring them as
# capabilities put permanent zero-earned rows in the Technical denominator.
SUBMISSION_CATEGORY = "submission"


class RfpRequirement(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    requirement: str
    category: str = "service"
    is_core: bool = Field(default=False, alias="isCore")
    disqualifying: bool = False
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

For EACH requirement give 1 short seed kbQuery (fallback only — a dedicated
evidence agent plans the real searches). Phrase it the way zö materials are
written (roles, tools, deliverables). Never use the buyer's name as subject.

isCore=true when the RFP makes the requirement mandatory, scores it, or it is
central to the scope of work. isCore=false for incidental or optional items.

disqualifying=true ONLY for a stated minimum threshold the vendor must ALREADY
MEET before bidding, which makes a proposal non-responsive when unmet — a
counted track record ("at least five comparable municipal projects completed
within the past five years"), or a mandatory license, registration,
certification, or bond required to bid. These are pass/fail, not scored
preferences: a vendor that cannot meet one cannot win by writing well.
Everything the RFP merely scores, weights, or prefers is disqualifying=false —
do NOT flag a capability just because it is important or heavily weighted.

Never set disqualifying=true on something the vendor SUPPLIES in the proposal.
"Provide three client references" is a form any agency with clients fills out;
"have completed five comparable projects in five years" is a track record you
either have or do not. Only the second is a qualification.

category MUST be accurate — scoring depends on it:
- technical = platforms/tools/methods (CMS, WordPress, ADA/WCAG audit, hosting,
  content migration, SEO, security, integrations, QA)
- service = delivery work types (website redesign, brand campaign, training)
- role = named staff titles to assign (project manager, UX designer, trainer)
- compliance = certifications, insurance, registrations, EEO/policy affirmations,
  ability to contract with the buyer (search 01_companyfacts — never the buyer name)
- logistics = office location, geography, on-site presence
- submission = what the PROPOSAL DOCUMENT must contain, authored at submission
  time rather than demonstrated from past work: client references, an itemized
  budget or fee schedule, a cost breakdown, a project plan / timeline / approach
  narrative, a statement of philosophy or understanding, resumes or an org chart
  compiled for this bid, signed forms, addenda acknowledgements, page limits,
  and requests to "include N case studies" as an exhibit.
Do NOT label a platform skill as "role" just because a person would do it.

Read the RFP's submission-instructions section for submission rows. The scope
of work is where capabilities come from. A capability is something zö either
can or cannot DO; a submission item is something zö WRITES. "Sponsorship
package development" is a service. "Include an itemized budget" is submission.

Do NOT re-extract the evaluation criteria as requirements. Scoring sections
("demonstrated ability to work with an established brand", "strong capabilities
in data-driven optimization") restate scope you have already enumerated —
emitting them again creates duplicate rows that double-count the same gap.
Enumerate each capability ONCE, from the scope of work.

Return ONLY JSON:
{"requirements":[{"requirement":"...","category":"service|role|technical|compliance|logistics|submission",
  "isCore":true,"disqualifying":false,"rfpQuote":"short verbatim phrase from the RFP",
  "kbQueries":["...","..."]}]}"""


_MAX_REQUIREMENTS = 24
# Seeds only — evidence query planner owns the real search set.
_MAX_QUERIES_PER_REQUIREMENT = 2


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
        if category == "compliance":
            literal = (
                "zö agency 01_companyfacts WBENC WOSB women-owned certifications "
                "insurance equal opportunity"
            )
        elif category == "logistics":
            literal = (
                "zö agency 01_companyfacts office location registration geography"
            )
        else:
            literal = f"zö agency {requirement} 03_CS 04_Bio 06_WON"
        if literal.casefold() not in {q.casefold() for q in queries}:
            queries.append(literal)

        # A submission deliverable is authored, not demonstrated: every bidder
        # can satisfy it, so it can never be the threshold that ends a pursuit.
        # Enforced here rather than trusted to the prompt — one mis-flagged row
        # forces NO-GO outright and suppresses every calibration floor.
        disqualifying = bool(row.get("disqualifying"))
        if disqualifying and category == SUBMISSION_CATEGORY:
            logger.info(
                "go_no_go dropped disqualifying flag on submission row: %s",
                requirement,
            )
            disqualifying = False

        out.append(
            RfpRequirement(
                requirement=requirement,
                category=category,
                isCore=bool(row.get("isCore") or row.get("is_core")),
                disqualifying=disqualifying,
                rfpQuote=_clean(row.get("rfpQuote") or row.get("rfp_quote"), limit=240),
                kbQueries=queries,
            )
        )
        if len(out) >= _MAX_REQUIREMENTS:
            break

    logger.info(
        "go_no_go requirements parsed=%d core=%d disqualifying=%d",
        len(out),
        sum(1 for r in out if r.is_core),
        sum(1 for r in out if r.disqualifying),
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
