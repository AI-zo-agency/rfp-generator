"""RFP-compulsory content counts — case studies, references, key personnel.

If THIS RFP says the proposer shall submit N examples / references / bios,
generation must try to meet N and Scan must treat a shortfall as a
qualification / non-responsive risk. Silent defaults (e.g. always 2 case
studies) are not the same as an RFP minimum.

Does not invent extra case studies when the KB is short — flags the gap.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from typing import Any

from app.models.proposal import ProposalDraft, ProposalSection
from app.services.proposal_section_health import is_dead_section

logger = logging.getLogger(__name__)

KIND_CASE_STUDIES = "case_studies"
KIND_REFERENCES = "references"
KIND_KEY_PERSONNEL = "key_personnel"
KIND_SAMPLE_WORK = "sample_work"

_KNOWN_KINDS = frozenset(
    {KIND_CASE_STUDIES, KIND_REFERENCES, KIND_KEY_PERSONNEL, KIND_SAMPLE_WORK}
)

# When the RFP is silent, Section 3 still prefers two real studies — not a DQ.
DEFAULT_CASE_STUDY_PREFERENCE = 2

_CACHE: dict[str, list["CompulsoryContentAsk"]] = {}


@dataclass(frozen=True)
class CompulsoryContentAsk:
    kind: str
    minimum: int
    rfp_quote: str
    pass_fail: bool = True


@dataclass
class CompulsoryShortfall:
    ask: CompulsoryContentAsk
    found: int
    message: str


def _cache_key(rfp_text: str) -> str:
    return hashlib.sha256((rfp_text or "")[:24_000].encode("utf-8", "ignore")).hexdigest()


async def extract_compulsory_content_asks(rfp_text: str) -> list[CompulsoryContentAsk]:
    """LLM: numeric submission minima THIS RFP actually states. Empty if silent."""
    text = (rfp_text or "").strip()
    if not text:
        return []
    key = _cache_key(text)
    if key in _CACHE:
        return list(_CACHE[key])

    from app.services import llm
    from app.services.proposal_rfp_excerpt import submission_documents_excerpt

    if not llm.is_configured():
        _CACHE[key] = []
        return []

    excerpt = submission_documents_excerpt(text) or text[:40_000]
    asks: list[CompulsoryContentAsk] = []
    try:
        raw, _ = await llm.chat_json(
            [
                {
                    "role": "system",
                    "content": (
                        "Read ONE RFP. Return only compulsory QUANTITY rules the proposer "
                        "must submit with the proposal (pass/fail / non-responsive if short).\n"
                        "Examples of meaning — not a checklist to invent: 'provide three "
                        "case studies', 'minimum of two similar projects', 'three client "
                        "references with contact information', 'resumes for all key personnel'.\n"
                        "Do NOT invent a default cover-letter / methodology / three-study stack. "
                        "If this RFP does not state a number, omit that kind.\n"
                        "kind must be one of: case_studies, references, key_personnel, sample_work.\n"
                        "minimum is an integer >= 1. rfpQuote is a short verbatim clause.\n"
                        "passFail true when missing the count can disqualify or render "
                        "non-responsive; false if merely scored/preferred.\n"
                        'Return JSON: {"requirements":[{"kind":"case_studies","minimum":3,'
                        '"rfpQuote":"...","passFail":true}]}'
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "Extract compulsory content counts from this RFP excerpt. "
                        "Return the JSON object.\n\n"
                        f"{excerpt[:45_000]}"
                    ),
                },
            ],
            max_tokens=1024,
            temperature=0.0,
            tier="light",
            node_name="rfp_compulsory_content_counts",
            cache_prefix=excerpt[:20_000],
        )
        rows = (raw or {}).get("requirements") if isinstance(raw, dict) else None
        if isinstance(rows, list):
            seen: set[str] = set()
            for row in rows:
                if not isinstance(row, dict):
                    continue
                kind = str(row.get("kind") or "").strip().casefold().replace(" ", "_")
                if kind not in _KNOWN_KINDS or kind in seen:
                    continue
                try:
                    minimum = int(row.get("minimum") or 0)
                except (TypeError, ValueError):
                    continue
                if minimum < 1 or minimum > 20:
                    continue
                quote = str(row.get("rfpQuote") or row.get("rfp_quote") or "").strip()[:280]
                pass_fail = bool(row.get("passFail", row.get("pass_fail", True)))
                seen.add(kind)
                asks.append(
                    CompulsoryContentAsk(
                        kind=kind,
                        minimum=minimum,
                        rfp_quote=quote,
                        pass_fail=pass_fail,
                    )
                )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Compulsory content extract failed: %s", exc)

    _CACHE[key] = asks
    return list(asks)


async def stated_case_study_minimum(rfp_text: str) -> int | None:
    """RFP-stated case-study / sample-work floor, or None when the RFP is silent."""
    asks = await extract_compulsory_content_asks(rfp_text)
    floors = [
        a.minimum
        for a in asks
        if a.kind in {KIND_CASE_STUDIES, KIND_SAMPLE_WORK}
    ]
    if floors:
        return max(floors)
    return None


async def required_case_study_minimum(rfp_text: str) -> int:
    """RFP-stated case-study / sample-work floor, else the silent preference of 2."""
    stated = await stated_case_study_minimum(rfp_text)
    return stated if stated else DEFAULT_CASE_STUDY_PREFERENCE


def count_usable_case_study_cards(draft: ProposalDraft) -> int:
    from app.services.proposal_case_study_eligibility import (
        is_eligible_section3_case_study_title,
    )

    n = 0
    for section in draft.sections:
        sid = section.id or ""
        if sid == "section-3-our-work":
            if (section.content or "").strip():
                n += 1
            continue
        if not (sid.startswith("section-3-work-") and sid != "section-3-work-placeholder"):
            continue
        if not (section.content or "").strip() or is_dead_section(section.content or ""):
            continue
        if is_eligible_section3_case_study_title(
            section.title
        ) and is_eligible_section3_case_study_title(sid):
            n += 1
    return n


def count_key_personnel_bios(draft: ProposalDraft) -> int:
    n = 0
    for section in draft.sections:
        sid = section.id or ""
        if not sid.startswith("section-2-bio-") or sid.endswith("placeholder"):
            continue
        body = section.content or ""
        if body.strip() and not is_dead_section(body):
            n += 1
    return n


def count_reference_entries(draft: ProposalDraft) -> int:
    """Count named reference blocks in a References tab — not a synonym map."""
    chunks: list[str] = []
    for section in draft.sections:
        title = (section.title or "").casefold()
        sid = (section.id or "").casefold()
        if "reference" not in title and "reference" not in sid:
            continue
        body = section.content or ""
        if not body.strip() or is_dead_section(body):
            continue
        chunks.append(body)
    if not chunks:
        return 0
    blob = "\n".join(chunks)
    table_rows = 0
    for line in blob.splitlines():
        if "|" not in line:
            continue
        cells = [c.strip() for c in line.split("|") if c.strip()]
        if len(cells) < 2:
            continue
        joined = " ".join(cells).casefold()
        if re.search(r"^[-:|\s]+$", joined):
            continue
        if cells[0].casefold() in {"name", "client", "organization", "firm"}:
            continue
        table_rows += 1
    numbered = len(re.findall(r"(?m)^\s*(?:\d+[.)]|[-*])\s+\S+", blob))
    headings = len(re.findall(r"(?m)^#{2,3}\s+\S+", blob))
    return max(table_rows, numbered, headings)


def found_count_for_kind(draft: ProposalDraft, kind: str) -> int:
    if kind in {KIND_CASE_STUDIES, KIND_SAMPLE_WORK}:
        return count_usable_case_study_cards(draft)
    if kind == KIND_REFERENCES:
        return count_reference_entries(draft)
    if kind == KIND_KEY_PERSONNEL:
        return count_key_personnel_bios(draft)
    return 0


def audit_compulsory_content(
    draft: ProposalDraft,
    asks: list[CompulsoryContentAsk],
) -> list[CompulsoryShortfall]:
    """Compare the manuscript to RFP-stated minima. Never invent missing proof."""
    out: list[CompulsoryShortfall] = []
    for ask in asks:
        found = found_count_for_kind(draft, ask.kind)
        if found >= ask.minimum:
            continue
        label = ask.kind.replace("_", " ")
        q = f" RFP: “{ask.rfp_quote}”" if ask.rfp_quote else ""
        severity = (
            "Qualification / non-responsive risk"
            if ask.pass_fail
            else "Scored-content shortfall"
        )
        message = (
            f"{severity}: {label} — manuscript has {found}, RFP requires {ask.minimum}."
            f"{q} Do not invent extra {label}; add a verified KB item or MANUAL FILL."
        )
        out.append(CompulsoryShortfall(ask=ask, found=found, message=message))
    return out


async def audit_draft_against_rfp_compulsory_content(
    draft: ProposalDraft,
    rfp_text: str,
) -> list[CompulsoryShortfall]:
    asks = await extract_compulsory_content_asks(rfp_text)
    return audit_compulsory_content(draft, asks)


def shortfall_stub_section(shortfall: CompulsoryShortfall) -> dict[str, Any]:
    """Honest gap card — never a fabricated third case study."""
    ask = shortfall.ask
    quote = ask.rfp_quote or f"minimum {ask.minimum}"
    return {
        "id": f"rfp-compulsory-gap-{ask.kind}",
        "title": f"RFP minimum — {ask.kind.replace('_', ' ')}",
        "content": (
            f"This RFP requires **{ask.minimum}** {ask.kind.replace('_', ' ')} "
            f"({quote}). The manuscript currently has **{shortfall.found}** verified "
            f"item(s). Additional entries were not invented from the knowledge base.\n\n"
            f"[MANUAL FILL: Sonja — add verified {ask.kind.replace('_', ' ')} "
            f"from ClientList / 03_CS / 04_Bio to meet the RFP minimum, or confirm "
            f"the buyer will accept {shortfall.found}.]"
        ),
        "status": "generated",
        "source": "generated",
    }


def merge_compulsory_gap_stubs(
    draft: ProposalDraft,
    shortfalls: list[CompulsoryShortfall],
) -> tuple[ProposalDraft, list[str]]:
    """Attach one gap stub per shortfall kind if that tab is not already present."""
    logs: list[str] = []
    existing_ids = {s.id for s in draft.sections}
    extra: list[ProposalSection] = []
    for shortfall in shortfalls:
        stub = shortfall_stub_section(shortfall)
        if stub["id"] in existing_ids:
            continue
        extra.append(ProposalSection.model_validate(stub))
        logs.append(shortfall.message)
    if not extra:
        return draft, logs
    return draft.model_copy(update={"sections": [*draft.sections, *extra]}), logs
