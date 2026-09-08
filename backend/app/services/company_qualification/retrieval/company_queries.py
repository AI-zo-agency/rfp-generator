"""JIT company-scoped Supermemory retrieval for the Company Truth Agent."""

from __future__ import annotations

import logging
import re
from typing import Any

from app.services import supermemory

logger = logging.getLogger("app.sections_agents")

# Never retrieve bios or case-study decks for company truth.
# Won/finalist proposals MAY contain agency insurance/COI tables — keep them
# when the filename is proposal-shaped; still drop 03_CS / 04_Bio.
_EXCLUDED_SOURCE = re.compile(r"04_Bio_|03_CS_", re.I)

# Fixed company queries — NEVER append RFP client/sector (company facts are RFP-agnostic).
COMPANY_TRUTH_QUERIES: tuple[str, ...] = (
    "01_companyfacts verified legal name EIN DBA ownership women-owned",
    "zo agency business registration state IDs DUNS SAM CAGE",
    "company office mailing remittance address phone email website",
    "WBENC WOSB certifications certifying agency certification numbers",
    "zö agency insurance Commercial General Liability Next Insurance limits "
    "professional liability workers compensation ACORD COI.pdf 01_companyfacts",
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
    max_chars: int = 120_000,
    log_label: str = "Company Truth Agent",
) -> tuple[str, list[str]]:
    """Run fixed company queries via chunk-first brain RAG (same bar as kb_qa_loop).

    rfp_* args are accepted for call-site compatibility but intentionally unused.
    Company facts do not depend on the solicitation.
    """
    del rfp_client, rfp_sector, rfp_context

    if not supermemory.is_configured():
        return "(Supermemory not configured.)", []

    from app.services.kb_rag_retrieve import retrieve_for_question

    parts: list[str] = []
    sources: list[str] = []
    seen_src: set[str] = set()
    total = 0
    char_budget = max(2_000, int(max_chars))
    per_query = max(10_000, char_budget // max(len(COMPANY_TRUTH_QUERIES), 1))

    for i, query in enumerate(COMPANY_TRUTH_QUERIES, start=1):
        from app.services.proposal_generation_cancel import check_cancelled_for_active

        await check_cancelled_for_active()
        if total >= char_budget:
            break
        logger.info(
            "  └─ [%s] JIT query %d/%d (chunk-first): %s",
            log_label,
            i,
            len(COMPANY_TRUTH_QUERIES),
            query[:100],
        )
        remaining = char_budget - total
        ctx, srcs, _ = await retrieve_for_question(
            query,
            limit=12,
            max_chars=min(per_query, remaining),
            threshold=0.35,
            fallback_threshold=0.22,
            expand_queries=False,
        )
        if not (ctx or "").strip() or ctx.startswith("(No matching"):
            continue
        # Drop bio/case-study only packs that slipped through.
        kept_labels = [s for s in srcs if is_company_source(s)]
        if srcs and not kept_labels:
            continue
        parts.append(ctx.strip())
        total += len(ctx)
        for label in kept_labels or srcs:
            if label and label not in seen_src:
                seen_src.add(label)
                sources.append(label)

    if not parts:
        return "(No company knowledge-base matches.)", []
    merged = "\n\n---\n\n".join(parts)
    if len(merged) > char_budget:
        merged = merged[:char_budget]
    logger.info(
        "  └─ [%s] packed %d chars from chunk-first company RAG (%d sources)",
        log_label,
        len(merged),
        len(sources),
    )
    return merged, sources


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
