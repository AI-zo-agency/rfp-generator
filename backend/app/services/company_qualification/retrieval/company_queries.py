"""JIT company-scoped Supermemory retrieval for the Company Truth Agent."""

from __future__ import annotations

import logging
import re
from typing import Any

from app.services import supermemory

logger = logging.getLogger("app.sections_agents")

# Never retrieve bios or case studies for company truth.
_EXCLUDED_SOURCE = re.compile(r"04_Bio_|03_CS_|06_WON_", re.I)

# Fixed company queries — NEVER append RFP client/sector (company facts are RFP-agnostic).
COMPANY_TRUTH_QUERIES: tuple[str, ...] = (
    "01_companyfacts verified legal name EIN DBA ownership women-owned",
    "zo agency business registration state IDs DUNS SAM CAGE",
    "company office mailing remittance address phone email website",
    "WBENC WOSB certifications certifying agency certification numbers",
    "insurance coverage general liability professional liability workers compensation",
    "zo agency capabilities service lines departments expertise",
    "organization structure departments leadership Client Services Creative Digital Development",
    "company founded year history years in operation zo agency",
)


def is_company_source(source_name: str) -> bool:
    return not _EXCLUDED_SOURCE.search(source_name or "")


def filter_company_sources(sources: list[str]) -> list[str]:
    return [s for s in sources if is_company_source(s)]


async def fetch_company_truth_corpus(
    *,
    rfp_client: str = "",
    rfp_sector: str = "",
    rfp_context: str = "",
) -> tuple[str, list[str]]:
    """Run fixed company queries — snippets only, no RFP client, no bulk full-doc dump.

    rfp_* args are accepted for call-site compatibility but intentionally unused.
    Company facts do not depend on the solicitation.
    """
    del rfp_client, rfp_sector, rfp_context

    if not supermemory.is_configured():
        return "(Supermemory not configured.)", []

    async def _one(query: str, index: int) -> list[dict[str, Any]]:
        logger.info(
            "  └─ [Company Truth Agent] JIT query %d/%d: %s",
            index,
            len(COMPANY_TRUTH_QUERIES),
            query[:100],
        )
        try:
            hits = await supermemory.search_documents(
                query=query,
                limit=3,
                include_full_docs=False,
                search_mode="hybrid",
                filters=supermemory.KNOWLEDGE_BASE_SEARCH_FILTERS,
            )
        except supermemory.SupermemoryError:
            return []
        return [h for h in hits if supermemory.is_knowledge_base_hit(h)]

    # One query at a time — never fan out parallel Supermemory/LLM pressure.
    hit_groups: list[list[dict[str, Any]]] = []
    for i, q in enumerate(COMPANY_TRUTH_QUERIES):
        from app.services.proposal_generation_cancel import check_cancelled_for_active

        await check_cancelled_for_active()
        hit_groups.append(await _one(q, i + 1))

    # Prefer known company docs; keep at most a few unique sources as snippets.
    seen: set[str] = set()
    preferred: list[dict[str, Any]] = []
    other: list[dict[str, Any]] = []
    for hits in hit_groups:
        for hit in hits:
            label = supermemory.hit_file_name(hit)
            if not label or not is_company_source(label):
                continue
            key = label.casefold()
            if key in seen:
                continue
            seen.add(key)
            lowered = key
            if "companyfacts" in lowered or "01_company" in lowered or "mastertemplate" in lowered:
                preferred.append(hit)
            else:
                other.append(hit)

    selected = (preferred + other)[:8]
    # Fetch full text only for the shortlisted unique company docs (not every search hit).
    parts: list[str] = []
    sources: list[str] = []
    total = 0
    max_chars = 120_000

    for hit in selected:
        from app.services.proposal_generation_cancel import check_cancelled_for_active

        await check_cancelled_for_active()
        label = supermemory.hit_file_name(hit)
        content = await supermemory.resolve_hit_document_content(hit)
        if not content.strip():
            content = supermemory.hit_text(hit)
        if not content.strip():
            continue
        remaining = max_chars - total
        if remaining <= 0:
            break
        block = f"### {label}\n{content}"[:remaining]
        parts.append(block)
        sources.append(label)
        total += len(block)

    logger.info(
        "  └─ [Company Truth Agent] shortlisted %d company docs (%d chars) — no RFP client in queries",
        len(sources),
        total,
    )
    return "\n\n".join(parts), sources


def company_truth_extraction_schema() -> dict[str, Any]:
    """JSON schema description for LLM extraction prompt."""
    return {
        "legalName": "string or null",
        "dba": "string or null",
        "founded": "string or null",
        "yearsInOperation": "integer or null",
        "ownership": "string or null",
        "locations": {"office": "string|null", "mailing": "string|null", "remittance": "string|null"},
        "contact": {"phone": "string|null", "email": "string|null", "website": "string|null"},
        "businessRegistration": {
            "ein": "string|null",
            "stateIds": [{"state": "string", "id": "string"}],
        },
        "employeeCount": "string or null",
        "departments": [{"name": "string", "head": "string|null", "summary": "string|null"}],
        "capabilities": ["string"],
        "certifications": [
            {"name": "string", "agency": "string|null", "number": "string|null", "expires": "string|null"}
        ],
        "insurance": [{"type": "string", "amount": "string|null — use [VERIFY: amount] if unknown"}],
        "sources": ["document filenames used"],
    }
