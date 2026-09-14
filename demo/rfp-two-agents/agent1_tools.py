"""Demo-only RFP tools + Agent 1 tool-calling extract (does not touch backend)."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any

import httpx
from pypdf import PdfReader

from app.core.config import settings
from app.services import llm
from app.services.llm import LlmError, resolve_llm_model
from app.services.llm_call_context import get_llm_rfp_id, get_llm_run_id
from app.services.llm_call_log import record_llm_call
from app.services.llm_pricing import estimate_cost_usd
from app.services.proposal_intelligence.merged_passes import (
    UNDERSTANDING_FORBIDDEN_KEYS,
    _apply_opportunity_slices,
    _as_dict,
)
from app.services.proposal_intelligence.agent_base import clamp_confidence
from app.services.proposal_intelligence.plan_ops import (
    IntelligenceError,
    append_decision,
    merge_memory,
    set_provider,
)
from app.services.proposal_intelligence.schemas import (
    OpportunityUnderstanding,
    ProposalExecutionPlan,
)

logger = logging.getLogger("rfp-two-agents-demo.tools")

_MAX_TOOL_ROUNDS_REPAIR = 3  # repair path only (budget A1)
_PAGE_CHAR_CAP = 12_000
_SEARCH_HIT_CHARS = 900
_EVIDENCE_SECTION_CHARS = 4500
_OBLIGATION_CANDIDATE_CAP = 80

# Section labels → query tokens for cheap local pack building (no LLM).
_SECTION_SELECTORS: list[tuple[str, list[str]]] = [
    ("key_information", ["key information", "solicitation", "important dates", "rfq schedule", "procurement schedule"]),
    ("submission_instructions", ["instructions to offeror", "proposal submission", "quote submission", "how to submit", "submittal instructions"]),
    ("submittal_items", ["submittal items", "required submittals", "proposal content", "response format", "required documents"]),
    ("evaluation", ["evaluation", "selection criteria", "quote evaluation", "scoring", "award criteria"]),
    ("scope_of_work", ["statement of work", "scope of work", "scope of services", "technical requirements", "deliverables"]),
    ("pricing", ["payment schedule", "compensation", "pricing", "cost proposal", "fee schedule", "budget"]),
    ("contract_term", ["term of agreement", "contract term", "period of performance", "agreement term"]),
    ("insurance", ["insurance requirements", "insurance", "indemnification", "exhibit b"]),
    ("purpose_background", ["purpose", "background", "introduction", "project overview"]),
    ("goals_objectives", ["goals", "objectives", "desired results"]),
    ("draft_agreement", ["draft agreement", "sample agreement", "general provisions"]),
    ("post_award", ["upon award", "if selected", "successful offeror", "post-award"]),
    ("references", ["references", "professional references", "project examples"]),
]

_OBLIGATION_VERBS = (
    "shall submit", "must submit", "shall provide", "must provide",
    "shall include", "must include", "shall complete", "must complete",
    "offeror shall", "proposer shall", "bidder shall", "contractor shall",
    "shall certify", "must certify", "shall attach", "must attach",
)

_SCORING_TERMS = ("points", "score", "scoring", "weight", "weighted", "percentage", "%", "evaluation criteria")
_COND_TERMS = ("if applicable", "if selected", "if submitting", "if used", "as needed", "at county request", "optional", "may ")

# OpenAI-style tools for OpenRouter / Claude.
TOOL_DEFS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search_rfp",
            "description": (
                "Semantic-ish search over RFP pages. Use to locate unknown sections "
                "(evaluation, insurance, SOW). Do not dump the whole document."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "default": 5},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_in_rfp",
            "description": (
                "Exact/near-exact keyword search across pages. Best for shall/must/"
                "submit/provide and other lexical harvests."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "terms": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                    },
                    "limit": {"type": "integer", "default": 40},
                },
                "required": ["terms"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_rfp_pages",
            "description": "Read contiguous page range for full local context.",
            "parameters": {
                "type": "object",
                "properties": {
                    "startPage": {"type": "integer", "minimum": 1},
                    "endPage": {"type": "integer", "minimum": 1},
                },
                "required": ["startPage", "endPage"],
                "additionalProperties": False,
            },
        },
    },
]


@dataclass
class RfpDoc:
    """Page-indexed RFP for targeted retrieval (not whole-PDF dumps)."""

    pages: list[str] = field(default_factory=list)
    filename: str = "rfp.pdf"

    @classmethod
    def from_pdf_bytes(cls, content: bytes, *, filename: str = "rfp.pdf") -> RfpDoc:
        if not content.startswith(b"%PDF"):
            raise ValueError("Not a PDF")
        reader = PdfReader(BytesIO(content))
        pages: list[str] = []
        for page in reader.pages:
            text = re.sub(r"[ \t]+", " ", (page.extract_text() or "")).strip()
            pages.append(text[:_PAGE_CHAR_CAP])
        if not any(pages):
            raise ValueError("PDF has no extractable text")
        return cls(pages=pages, filename=filename)

    @property
    def page_count(self) -> int:
        return len(self.pages)

    def full_text(self, *, max_chars: int = 100_000) -> str:
        parts: list[str] = []
        total = 0
        for i, page in enumerate(self.pages, start=1):
            if not page:
                continue
            block = f"--- Page {i} ---\n{page}"
            if total + len(block) > max_chars:
                parts.append(block[: max(0, max_chars - total)])
                break
            parts.append(block)
            total += len(block) + 2
        return "\n\n".join(parts).strip()

    def search_rfp(self, query: str, *, limit: int = 5) -> dict[str, Any]:
        tokens = [t for t in re.findall(r"[a-z0-9]{3,}", (query or "").lower()) if t]
        if not tokens:
            return {"results": []}
        scored: list[tuple[float, int, str]] = []
        for idx, page in enumerate(self.pages):
            low = page.lower()
            if not low:
                continue
            hits = sum(low.count(t) for t in tokens)
            if hits <= 0:
                continue
            # Density + coverage of query tokens
            covered = sum(1 for t in tokens if t in low)
            score = hits + covered * 2
            scored.append((score, idx + 1, page))
        scored.sort(key=lambda x: (-x[0], x[1]))
        results = []
        for score, page_no, page in scored[: max(1, min(limit, 8))]:
            results.append(
                {
                    "page": page_no,
                    "score": score,
                    "section": f"page {page_no}",
                    "text": page[:_SEARCH_HIT_CHARS],
                }
            )
        return {"results": results}

    def find_in_rfp(self, terms: list[str], *, limit: int = 40) -> dict[str, Any]:
        clean = [t.strip() for t in terms if str(t).strip()]
        if not clean:
            return {"matches": []}
        matches: list[dict[str, Any]] = []
        for page_no, page in enumerate(self.pages, start=1):
            if not page:
                continue
            # Sentence-ish split
            for sent in re.split(r"(?<=[.:;])\s+|\n+", page):
                s = sent.strip()
                if len(s) < 12:
                    continue
                low = s.lower()
                hit_terms = [t for t in clean if t.lower() in low]
                if not hit_terms:
                    continue
                matches.append(
                    {
                        "page": page_no,
                        "terms": hit_terms,
                        "text": s[:500],
                    }
                )
                if len(matches) >= limit:
                    return {"matches": matches}
        return {"matches": matches}

    def read_rfp_pages(self, start_page: int, end_page: int) -> dict[str, Any]:
        start = max(1, int(start_page))
        end = min(self.page_count, int(end_page))
        if end < start:
            start, end = end, start
        # Cap span so tools stay targeted
        if end - start > 4:
            end = start + 4
        pages = []
        for p in range(start, end + 1):
            pages.append({"page": p, "text": self.pages[p - 1]})
        return {"startPage": start, "endPage": end, "pages": pages}

    def run_tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name == "search_rfp":
            return self.search_rfp(str(args.get("query") or ""), limit=int(args.get("limit") or 5))
        if name == "find_in_rfp":
            terms = args.get("terms") or []
            if isinstance(terms, str):
                terms = [terms]
            return self.find_in_rfp(list(terms), limit=int(args.get("limit") or 40))
        if name == "read_rfp_pages":
            return self.read_rfp_pages(
                int(args.get("startPage") or args.get("start_page") or 1),
                int(args.get("endPage") or args.get("end_page") or 1),
            )
        return {"error": f"unknown tool {name}"}


# JSON Schema — additionalProperties:false strips strategy etc. at validation time.
OPPORTUNITY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "understanding",
        "compliance",
        "scope",
        "evaluation",
        "successCriteria",
        "provenance",
    ],
    "properties": {
        "understanding": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "client",
                "industry",
                "orgType",
                "projectType",
                "services",
                "businessGoals",
                "painPoints",
                "desiredOutcomes",
                "complexity",
                "budgetIntel",
                "timelineIntel",
                "contractStructure",
                "confidence",
                "memoryFacts",
            ],
            "properties": {
                "client": {"type": "string"},
                "industry": {"type": "string"},
                "orgType": {"type": "string"},
                "projectType": {"type": "string"},
                "services": {"type": "array", "items": {"type": "string"}},
                "businessGoals": {"type": "array", "items": {"type": "string"}},
                "painPoints": {"type": "array", "items": {"type": "string"}},
                "desiredOutcomes": {"type": "array", "items": {"type": "string"}},
                "complexity": {"type": "string"},
                "budgetIntel": {"type": "object"},
                "timelineIntel": {"type": "object"},
                "contractStructure": {"type": "string"},
                "confidence": {"type": "number"},
                "memoryFacts": {"type": "object"},
            },
        },
        "compliance": {
            "type": "object",
            "additionalProperties": False,
            "required": ["items", "confidence"],
            "properties": {
                "items": {"type": "array"},
                "confidence": {"type": "number"},
            },
        },
        "scope": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "mandatory",
                "optional",
                "futurePhases",
                "outOfScope",
                "dependencies",
                "notes",
                "confidence",
            ],
            "properties": {
                "mandatory": {"type": "array", "items": {"type": "string"}},
                "optional": {"type": "array", "items": {"type": "string"}},
                "futurePhases": {"type": "array", "items": {"type": "string"}},
                "outOfScope": {"type": "array", "items": {"type": "string"}},
                "dependencies": {"type": "array", "items": {"type": "string"}},
                "notes": {"type": "string"},
                "confidence": {"type": "number"},
            },
        },
        "evaluation": {"type": "object"},
        "successCriteria": {"type": "object"},
        "provenance": {"type": "array"},
    },
}


def validate_opportunity_json(raw: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Strip extras + fill required keys. Returns (clean, errors)."""
    errors: list[str] = []
    if not isinstance(raw, dict):
        return {}, ["root must be object"]

    # Drop forbidden / unknown top-level keys (schema additionalProperties:false)
    allowed = set(OPPORTUNITY_SCHEMA["required"])
    cleaned = {k: v for k, v in raw.items() if k in allowed}
    for bad in set(raw) - allowed:
        errors.append(f"stripped top-level key: {bad}")

    for key in allowed:
        if key not in cleaned:
            errors.append(f"missing key: {key}")
            cleaned[key] = {}

    understanding = cleaned.get("understanding")
    if not isinstance(understanding, dict):
        understanding = {}
        cleaned["understanding"] = understanding
        errors.append("understanding must be object")
    for fk in UNDERSTANDING_FORBIDDEN_KEYS:
        if fk in understanding:
            understanding.pop(fk, None)
            errors.append(f"stripped understanding.{fk}")
    # Drop unknown understanding keys
    u_allowed = set(
        OPPORTUNITY_SCHEMA["properties"]["understanding"]["required"]
    )
    for bad in list(understanding):
        if bad not in u_allowed:
            understanding.pop(bad, None)
            errors.append(f"stripped understanding.{bad}")
    for req in u_allowed:
        if req not in understanding:
            if req in ("services", "businessGoals", "painPoints", "desiredOutcomes"):
                understanding[req] = []
            elif req in ("budgetIntel", "timelineIntel", "memoryFacts"):
                understanding[req] = {}
            elif req == "confidence":
                understanding[req] = 0.0
            elif req == "contractStructure":
                understanding[req] = ""
            else:
                understanding[req] = ""
            errors.append(f"missing_required:understanding.{req}")
    mf = understanding.get("memoryFacts")
    if not isinstance(mf, dict):
        mf = {}
        understanding["memoryFacts"] = mf
    if not str(mf.get("clientName") or "").strip():
        mf["clientName"] = str(understanding.get("client") or "").strip()
    if not str(mf.get("organizationType") or "").strip():
        mf["organizationType"] = str(understanding.get("orgType") or "").strip()
    if not mf.get("clientName"):
        errors.append("hard:memoryFacts.clientName empty")
    if not mf.get("organizationType"):
        errors.append("hard:memoryFacts.organizationType empty")

    scope = cleaned.get("scope")
    if not isinstance(scope, dict):
        scope = {}
        cleaned["scope"] = scope
    for req in ("mandatory", "optional", "futurePhases", "outOfScope", "dependencies"):
        if not isinstance(scope.get(req), list):
            scope[req] = []
    if "notes" not in scope or scope.get("notes") is None:
        scope["notes"] = ""
        errors.append("hard:scope.notes missing")
    # Drop unknown scope keys except notes + known
    s_allowed = {
        "mandatory",
        "optional",
        "futurePhases",
        "outOfScope",
        "dependencies",
        "notes",
        "confidence",
    }
    for bad in list(scope):
        if bad not in s_allowed:
            scope.pop(bad, None)
            errors.append(f"stripped scope.{bad}")
    scope.setdefault("confidence", 0.0)

    compliance = cleaned.get("compliance")
    if not isinstance(compliance, dict):
        compliance = {"items": [], "confidence": 0.0}
        cleaned["compliance"] = compliance
    if not isinstance(compliance.get("items"), list):
        compliance["items"] = []
    # Deduplicate compliance ids
    seen_ids: set[str] = set()
    items = []
    for i, item in enumerate(compliance["items"]):
        if not isinstance(item, dict):
            continue
        cid = str(item.get("id") or f"comp-{i+1}")
        if cid in seen_ids:
            cid = f"{cid}-{i+1}"
            errors.append(f"renamed duplicate compliance id → {cid}")
        seen_ids.add(cid)
        item = dict(item)
        item["id"] = cid
        items.append(item)
    compliance["items"] = items
    compliance.setdefault("confidence", 0.0)

    cleaned.setdefault("evaluation", {})
    cleaned.setdefault("successCriteria", {"items": [], "confidence": 0.0})
    prov = cleaned.get("provenance")
    if not isinstance(prov, list):
        cleaned["provenance"] = []
        if prov is not None:
            errors.append("provenance must be array")
    return cleaned, errors


def _parse_tool_args(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _extract_json_object(text: str) -> dict[str, Any] | None:
    if not text:
        return None
    parsed: dict[str, Any] | None = None
    try:
        parsed = llm._parse_json_response(text)  # noqa: SLF001 — demo reuse
    except Exception:
        parsed = None
    if parsed is None:
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            try:
                obj = json.loads(match.group(0))
                parsed = obj if isinstance(obj, dict) else None
            except json.JSONDecodeError:
                parsed = None
    if not parsed:
        return None
    # Classification salvage can return {industry, servicesRequested} and must not
    # be treated as opportunity JSON (leads to missing client/projectType → 502).
    if "understanding" not in parsed and "compliance" not in parsed and "scope" not in parsed:
        if "industry" in parsed or "servicesRequested" in parsed or "buyerType" in parsed:
            logger.warning(
                "Rejected non-opportunity JSON keys=%s",
                sorted(parsed.keys())[:12],
            )
            return None
    return parsed


async def _openrouter_tools_round(
    *,
    messages: list[dict[str, Any]],
    model: str,
    api_key: str,
    tools: list[dict[str, Any]] | None,
    max_tokens: int,
    node_name: str,
    reasoning_effort: str = "medium",
) -> tuple[dict[str, Any], dict[str, Any]]:
    """One OpenRouter chat turn; may include tool_calls. Returns (message, meta)."""
    from app.services.llm import _claude_adaptive_reasoning_body

    url = f"{settings.openrouter_base_url.rstrip('/')}/chat/completions"
    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
    }
    model_l = model.lower()
    if "anthropic" in model_l or "claude" in model_l:
        # exclude=True required — otherwise Sonnet 5 burns max_tokens on thinking
        # and returns empty content → "opportunity_extract returned no JSON".
        body["reasoning"] = _claude_adaptive_reasoning_body(reasoning_effort)
    else:
        body["temperature"] = 0.1
    if tools is not None:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    else:
        body["response_format"] = {"type": "json_object"}

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://zo.agency",
        "X-Title": "zo-rfp-two-agents-demo",
    }
    started = time.perf_counter()
    async with httpx.AsyncClient(timeout=300.0) as client:
        resp = await client.post(url, headers=headers, json=body)
    latency_ms = int((time.perf_counter() - started) * 1000)
    if resp.status_code >= 400:
        raise LlmError(
            f"OpenRouter tools call failed: {resp.status_code} {resp.text[:300]}",
            status_code=resp.status_code,
        )
    data = resp.json()
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    try:
        cost = float(usage.get("cost") or 0) if usage.get("cost") is not None else None
    except (TypeError, ValueError):
        cost = None
    if cost is None:
        cost = estimate_cost_usd(
            model=model,
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
        )
    try:
        record_llm_call(
            run_id=get_llm_run_id() or "demo",
            rfp_id=get_llm_rfp_id() or "",
            node_name=node_name,
            model=model,
            tier="heavy",
            provider="openrouter",
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
            cost_usd=float(cost or 0),
            latency_ms=latency_ms,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("cost log failed: %s", exc)

    choices = data.get("choices") or []
    if not choices:
        raise LlmError("OpenRouter returned no choices")
    choice0 = choices[0] if isinstance(choices[0], dict) else {}
    message = choice0.get("message") or {}
    if not isinstance(message, dict):
        message = {}
    finish_reason = choice0.get("finish_reason") or choice0.get("native_finish_reason")
    reasoning_tokens = int(
        usage.get("reasoning_tokens")
        or (usage.get("completion_tokens_details") or {}).get("reasoning_tokens")
        or 0
    )
    meta = {
        "finish_reason": finish_reason,
        "usage": usage,
        "reasoning_tokens": reasoning_tokens,
        "completion_tokens": int(usage.get("completion_tokens") or 0),
        "prompt_tokens": int(usage.get("prompt_tokens") or 0),
    }
    logger.info(
        "OpenRouter %s finish=%s prompt=%s completion=%s reasoning=%s",
        node_name,
        finish_reason,
        meta["prompt_tokens"],
        meta["completion_tokens"],
        reasoning_tokens,
    )
    return message, meta


def _message_text(msg: dict[str, Any]) -> str:
    content = msg.get("content") or ""
    if isinstance(content, list):
        return "".join(
            str(part.get("text") or "") if isinstance(part, dict) else str(part)
            for part in content
        )
    return str(content or "")


def normalize_provenance(entries: Any) -> list[dict[str, Any]]:
    """Keep only well-formed provenance rows (internal audit trail)."""
    if not isinstance(entries, list):
        return []
    out: list[dict[str, Any]] = []
    for row in entries:
        if not isinstance(row, dict):
            continue
        path = str(row.get("path") or "").strip()
        source_text = str(row.get("sourceText") or row.get("source_text") or "").strip()
        if not path:
            continue
        page = row.get("sourcePage", row.get("source_page"))
        try:
            page_i = int(page) if page is not None and str(page).strip() != "" else None
        except (TypeError, ValueError):
            page_i = None
        out.append(
            {
                "path": path,
                "value": row.get("value"),
                "sourcePage": page_i,
                "sourceSection": str(
                    row.get("sourceSection") or row.get("source_section") or ""
                )[:200],
                "sourceText": source_text[:500],
            }
        )
    return out


def merge_split_results(
    part_a: dict[str, Any],
    part_b: dict[str, Any],
    part_c: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Deterministic merge of A/B/C slices + provenance lists."""
    merged: dict[str, Any] = {
        "understanding": part_a.get("understanding")
        if isinstance(part_a.get("understanding"), dict)
        else {},
        "scope": part_a.get("scope") if isinstance(part_a.get("scope"), dict) else {},
        "successCriteria": part_a.get("successCriteria")
        if isinstance(part_a.get("successCriteria"), dict)
        else {"items": [], "confidence": 0.0},
        "compliance": part_b.get("compliance")
        if isinstance(part_b.get("compliance"), dict)
        else {"items": [], "confidence": 0.0},
        "evaluation": part_c.get("evaluation")
        if isinstance(part_c.get("evaluation"), dict)
        else {},
    }
    prov: list[dict[str, Any]] = []
    for part in (part_a, part_b, part_c):
        prov.extend(normalize_provenance(part.get("provenance")))
    return merged, prov



def build_evidence_pack(doc: "RfpDoc") -> dict[str, Any]:
    """Cheap local pack: section excerpts + obligation/scoring/date candidates (no LLM)."""
    pack: dict[str, Any] = {
        "pageCount": doc.page_count,
        "filename": doc.filename,
        "sections": {},
        "obligationCandidates": [],
        "scoringHits": [],
        "dateMoneyHits": [],
        "missingSections": [],
    }
    for key, queries in _SECTION_SELECTORS:
        best: dict[str, Any] | None = None
        best_score = 0.0
        for q in queries:
            res = doc.search_rfp(q, limit=3)
            for hit in res.get("results") or []:
                sc = float(hit.get("score") or 0)
                if sc > best_score:
                    best_score = sc
                    best = hit
        if best and best_score >= 2:
            page = int(best["page"])
            # contiguous ±1 page for local context
            block = doc.read_rfp_pages(max(1, page - 1), min(doc.page_count, page + 1))
            text = "\n\n".join(
                f"[p{p['page']}] {p['text']}" for p in block.get("pages") or []
            )[:_EVIDENCE_SECTION_CHARS]
            pack["sections"][key] = {
                "found": True,
                "anchorPage": page,
                "score": best_score,
                "text": text,
            }
        else:
            pack["sections"][key] = {"found": False, "text": None}
            pack["missingSections"].append(key)

    # Lexical obligation harvest (free)
    obl = doc.find_in_rfp(list(_OBLIGATION_VERBS) + list(_COND_TERMS), limit=_OBLIGATION_CANDIDATE_CAP)
    pack["obligationCandidates"] = (obl.get("matches") or [])[:_OBLIGATION_CANDIDATE_CAP]

    scoring = doc.find_in_rfp(list(_SCORING_TERMS), limit=25)
    pack["scoringHits"] = (scoring.get("matches") or [])[:25]
    pack["explicitScoringLikely"] = bool(pack["scoringHits"]) and any(
        any(t in (m.get("text") or "").lower() for t in ("point", "score", "weight", "%"))
        for m in pack["scoringHits"]
    )

    # Dates / money highlights
    date_re = re.compile(
        r"\b(?:January|February|March|April|May|June|July|August|September|"
        r"October|November|December)\s+\d{1,2},\s+\d{4}\b"
    )
    money_re = re.compile(r"\$[\d,]+(?:\.\d{2})?")
    dm: list[dict[str, Any]] = []
    for page_no, page in enumerate(doc.pages, start=1):
        for m in date_re.finditer(page):
            dm.append({"page": page_no, "kind": "date", "text": m.group(0)})
        for m in money_re.finditer(page):
            dm.append({"page": page_no, "kind": "money", "text": m.group(0)})
        if len(dm) >= 40:
            break
    pack["dateMoneyHits"] = dm[:40]

    from section_classifier import (
        build_bounded_sections,
        classify_document_sections,
        section_text_for_type,
    )

    classification = classify_document_sections(doc)
    bounded = build_bounded_sections(doc)
    pack["sectionClassification"] = classification
    pack["boundedSections"] = bounded
    pack["templatePages"] = classification.get("templatePages") or []

    # Override keyword-snippet sections with heading-bounded text when available
    _BOUNDED_MAP = {
        "scope_of_work": "statement_of_work",
        "submittal_items": "submittal_items",
        "evaluation": "evaluation",
        "pricing": "pricing",
        "references": "references",
    }
    for pack_key, bkey in _BOUNDED_MAP.items():
        bsec = bounded.get(bkey) or {}
        if isinstance(bsec, dict) and bsec.get("found") and bsec.get("text"):
            pack["sections"][pack_key] = {
                "found": True,
                "anchorPage": bsec.get("anchorPage"),
                "score": 10.0,
                "text": str(bsec["text"])[:_EVIDENCE_SECTION_CHARS],
                "heading": bsec.get("heading"),
                "bounded": True,
            }

    sow_bounded = bounded.get("statement_of_work") or {}
    sow_text = str(sow_bounded.get("text") or "") or section_text_for_type(
        doc, classification, "statement_of_work", char_cap=8000, bounded=bounded
    )
    if sow_text:
        pack["sowExcerpt"] = sow_text[:12_000]
        pack["sowBoundedText"] = sow_text[:28_000]
    sow_verbs = (
        "shall ",
        "must ",
        "will ",
        "is responsible",
        "required to",
        "contractor shall",
        "consultant shall",
    )
    sow_hits: list[dict[str, Any]] = []
    sow_start = int(sow_bounded.get("anchorPage") or 1) if sow_bounded.get("found") else 1
    sow_end = min(doc.page_count, sow_start + 35)
    for page_no, page in enumerate(doc.pages, start=1):
        if page_no < sow_start or page_no > sow_end:
            continue
        low = (page or "").casefold()
        if not sow_text and "statement of work" not in low:
            continue
        for sent in re.split(r"(?<=[.:;])\s+|\n+", page or ""):
            s = sent.strip()
            if len(s) < 20:
                continue
            sl = s.casefold()
            if any(v in sl for v in sow_verbs) or "as needed" in sl or "up to " in sl:
                sow_hits.append({"page": page_no, "text": s[:500]})
            if len(sow_hits) >= 120:
                break
        if len(sow_hits) >= 120:
            break
    pack["sowCandidates"] = sow_hits[:120]
    pack["pages"] = doc.pages  # internal — for reporting-note scan; stripped before Sonnet payload
    return pack


def merge_langextract_into_pack(pack: dict[str, Any], harvest: dict[str, Any]) -> None:
    """Prefer LangExtract hits for obligation/scoring/scope recall."""
    pack["langextract"] = harvest
    stats = harvest.get("stats") or {}
    if stats.get("error"):
        pack.setdefault("missingSections", [])
        if "langextract_error" not in pack["missingSections"]:
            pack["missingSections"].append(f"langextract_error:{stats['error'][:80]}")

    lx_obl = [
        {
            "page": h.get("page"),
            "text": h.get("sourceText") or h.get("text"),
            "terms": ["langextract"],
            "class": h.get("class"),
            "attributes": h.get("attributes"),
        }
        for h in (harvest.get("complianceHits") or [])
        if isinstance(h, dict)
    ]
    if lx_obl:
        seen = {str(c.get("text") or "")[:120] for c in pack.get("obligationCandidates") or []}
        merged = list(pack.get("obligationCandidates") or [])
        for row in lx_obl:
            key = str(row.get("text") or "")[:120]
            if key and key not in seen:
                seen.add(key)
                merged.append(row)
        pack["obligationCandidates"] = merged[:120]

    if harvest.get("evaluationHits"):
        pack["scoringHits"] = [
            {
                "page": h.get("page"),
                "text": h.get("sourceText") or h.get("text"),
                "terms": [h.get("class") or "evaluation"],
            }
            for h in harvest.get("evaluationHits") or []
            if isinstance(h, dict)
        ] + (pack.get("scoringHits") or [])[:10]
        pack["explicitScoringLikely"] = any(
            h.get("class") in ("scoring_criterion", "total_points")
            for h in harvest.get("evaluationHits") or []
            if isinstance(h, dict)
        )

    pack["scopeCandidates"] = harvest.get("scopeHits") or []


def repair_triggers(cleaned: dict[str, Any], pack: dict[str, Any], schema_errs: list[str]) -> list[str]:
    """When to spend a second (cheap) repair call."""
    reasons: list[str] = []
    if schema_errs:
        reasons.append("schema_issues")
    compliance = cleaned.get("compliance") if isinstance(cleaned.get("compliance"), dict) else {}
    items = compliance.get("items") if isinstance(compliance.get("items"), list) else []
    if not items:
        reasons.append("no_compliance_items")
    elif len(items) < 25 and len(pack.get("obligationCandidates") or []) > 40:
        reasons.append("compliance_retention_suspect")
    u = cleaned.get("understanding") if isinstance(cleaned.get("understanding"), dict) else {}
    try:
        conf = float(u.get("confidence") or compliance.get("confidence") or 0)
    except (TypeError, ValueError):
        conf = 0.0
    if conf and conf < 0.85:
        reasons.append("low_confidence")
    ev = cleaned.get("evaluation") if isinstance(cleaned.get("evaluation"), dict) else {}
    if ev.get("scoredResponseForm") is True:
        has_weight = False
        for c in ev.get("criteria") or []:
            if isinstance(c, dict) and c.get("weight") not in (None, "", 0):
                has_weight = True
                break
        if not has_weight and not pack.get("explicitScoringLikely"):
            reasons.append("scoring_claimed_without_evidence")
    if pack.get("sections", {}).get("evaluation", {}).get("found") is False and not items:
        reasons.append("evaluation_section_missing")
    if int(pack.get("pageCount") or 0) > 80:
        reasons.append("long_rfp")
    # mandatory+optional duplicate strings
    scope = cleaned.get("scope") if isinstance(cleaned.get("scope"), dict) else {}
    mand = {str(x).casefold() for x in (scope.get("mandatory") or []) if x}
    opt = {str(x).casefold() for x in (scope.get("optional") or []) if x}
    if mand & opt:
        reasons.append("mandatory_optional_overlap")
    return reasons


def _pack_for_llm(pack: dict[str, Any]) -> dict[str, Any]:
    """Evidence pack for Sonnet — omit internal full-page arrays."""
    slim = {k: v for k, v in pack.items() if k not in ("pages",)}
    if "boundedSections" in slim:
        slim["boundedSections"] = {
            k: {kk: vv for kk, vv in (v or {}).items() if kk != "text"} | (
                {"textPreview": str((v or {}).get("text") or "")[:4000]}
                if isinstance(v, dict) and v.get("text")
                else {}
            )
            for k, v in (slim.get("boundedSections") or {}).items()
        }
    return slim


async def _single_json_call(
    *,
    messages: list[dict[str, Any]],
    model: str,
    api_key: str,
    node_name: str,
    max_tokens: int,
    tools: list[dict[str, Any]] | None = None,
    max_tool_rounds: int = 0,
    doc: "RfpDoc | None" = None,
) -> tuple[dict[str, Any], list[str]]:
    """One Sonnet extraction; optional capped tool rounds (default 0)."""
    from app.services.llm import _usage_reasoning_exhausted_output

    trace: list[str] = []
    msgs = list(messages)
    rounds = max(1, max_tool_rounds + 1)
    last_diag = ""
    reasoning_effort = "medium"
    for round_i in range(rounds):
        use_tools = tools if (tools and round_i < max_tool_rounds) else None
        if tools and round_i == max_tool_rounds and max_tool_rounds > 0:
            msgs.append(
                {
                    "role": "user",
                    "content": "No more tools. Return final opportunity JSON only.",
                }
            )
        msg, meta = await _openrouter_tools_round(
            messages=msgs,
            model=model,
            api_key=api_key,
            tools=use_tools,
            max_tokens=max_tokens,
            node_name=node_name,
            reasoning_effort=reasoning_effort,
        )
        tool_calls = msg.get("tool_calls") or []
        content = _message_text(msg)
        if tool_calls and doc is not None and use_tools:
            msgs.append({"role": "assistant", "content": content or None, "tool_calls": tool_calls})
            for call in tool_calls:
                fn = (call.get("function") or {}) if isinstance(call, dict) else {}
                name = str(fn.get("name") or "")
                args = _parse_tool_args(fn.get("arguments"))
                result = doc.run_tool(name, args)
                trace.append(f"tool:{name}({json.dumps(args)[:100]})")
                msgs.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id") or name,
                        "content": json.dumps(result)[:10000],
                    }
                )
            continue
        parsed = _extract_json_object(content)
        if parsed:
            return parsed, trace

        finish = meta.get("finish_reason")
        last_diag = (
            f"chars={len(content)} finish={finish} "
            f"completion={meta.get('completion_tokens')} "
            f"reasoning={meta.get('reasoning_tokens')} "
            f"preview={content[:180]!r}"
        )
        logger.warning("opportunity JSON missing round=%s %s", round_i, last_diag)
        trace.append(f"json_miss:{last_diag[:200]}")

        # Thinking ate the budget → lower effort (higher max_tokens just buys more thinking).
        if (not content.strip()) or _usage_reasoning_exhausted_output(meta.get("usage")):
            reasoning_effort = "low"
            tools = None
            max_tool_rounds = 0
            trace.append("retry:reasoning_effort=low")

        msgs.append({"role": "assistant", "content": content or "(empty)"})
        msgs.append(
            {
                "role": "user",
                "content": (
                    "Return ONLY valid opportunity JSON with top-level keys "
                    "understanding, compliance, scope, evaluation, successCriteria, provenance. "
                    "No markdown, no preamble."
                ),
            }
        )
    raise IntelligenceError(f"{node_name} returned no JSON ({last_diag})")


def _targeted_repair_instruction(triggers: list[str], pack: dict[str, Any]) -> str:
    parts = ["Repair ONLY the issues listed — return FULL opportunity JSON (same schema)."]
    if "thin_scope_vs_sow_section" in triggers:
        parts.append(
            "Expand scope.mandatory/optional/dependencies from sowExcerpt and scopeCandidates. "
            "Do not drop qualifiers (at least, up to, annually)."
        )
    if "aggregated_compliance_items" in triggers:
        parts.append(
            "Split bundled compliance requirements into independently answerable items "
            "(references, submittal forms, project examples)."
        )
    if "missing_provenance" in triggers:
        parts.append("Add provenance rows with verbatim sourceText for major fields.")
    if "missing_pain_points" in triggers:
        parts.append(
            "Populate understanding.painPoints from purpose/background/SOW — not from draft agreement boilerplate."
        )
    if "qualifier_preservation" in triggers:
        parts.append("Restore exact numeric/temporal qualifiers from evidence; do not normalize.")
    if "scoring_claimed_without_evidence" in triggers:
        parts.append("Set scoredResponseForm=false, criteria=[], totalPoints=null.")
    if any("retention" in t or "anchor" in t for t in triggers):
        parts.append(
            "COMPLIANCE RETENTION: Include EVERY submittal/required-form obligation from "
            "obligationCandidates and langextract complianceHits. One item per independently "
            "answerable requirement (PC600, PC601, PC610, PC620, references, staffing, payment "
            "schedule, draft agreement, exceptions). Do not stop at ~18 items."
        )
    if any(t.startswith("hard:") for t in triggers):
        parts.append(
            "HARD SCHEMA: populate understanding.memoryFacts, contractStructure, "
            "timelineIntel.questionsDue, timelineIntel.quotesDue, scope.notes (string)."
        )
    if pack.get("sowBoundedText"):
        parts.append(f"SOW BOUNDED:\n{str(pack['sowBoundedText'])[:14000]}")
    elif pack.get("sowExcerpt"):
        parts.append(f"SOW EXCERPT:\n{str(pack['sowExcerpt'])[:12000]}")
    sub = (pack.get("sections") or {}).get("submittal_items") or {}
    if sub.get("text"):
        parts.append(f"SUBMITTAL ITEMS:\n{str(sub['text'])[:10000]}")
    return "\n".join(parts)


async def extract_opportunity_with_tools(
    *,
    doc: RfpDoc,
    system_prompt: str,
    rfp_meta: dict[str, str],
    node_name: str = "opportunity_extract",
    on_progress: Any | None = None,
) -> tuple[dict[str, Any], list[str], list[dict[str, Any]]]:
    """Stage 1 classify → LangExtract harvest → Sonnet normalize → deterministic validators."""
    from final_provenance import build_final_provenance
    from langextract_harvest import run_langextract_harvest
    from opportunity_validators import apply_deterministic_pipeline, validator_repair_triggers

    async def _prog(step: str, label: str, status: str = "active", detail: str = "") -> None:
        if not on_progress:
            return
        try:
            if hasattr(on_progress, "emit"):
                await on_progress.emit(step=step, label=label, status=status, detail=detail)
            else:
                maybe = on_progress(step=step, label=label, status=status, detail=detail)
                if asyncio.iscoroutine(maybe):
                    await maybe
        except Exception:  # noqa: BLE001
            pass

    api_key = (settings.openrouter_api_key or "").strip()
    if not api_key:
        raise IntelligenceError("OPENROUTER_API_KEY missing")
    model = resolve_llm_model("heavy", node_name=node_name)

    await _prog("evidence_pack", "Build evidence pack", "active")
    pack = build_evidence_pack(doc)
    await _prog(
        "evidence_pack",
        "Build evidence pack",
        "done",
        f"{pack.get('pageCount')} pages · {sum(1 for s in pack['sections'].values() if s.get('found'))} sections",
    )
    try:
        lx_cb = on_progress.sync_callback() if hasattr(on_progress, "sync_callback") else None
        harvest = await asyncio.to_thread(run_langextract_harvest, doc, lx_cb)
        merge_langextract_into_pack(pack, harvest)
    except Exception as exc:  # noqa: BLE001
        logger.warning("LangExtract harvest skipped: %s", exc)
        pack["langextract"] = {"stats": {"error": str(exc)[:200], "grounded": 0}}
        for sid, lab in (
            ("lx_compliance", "LangExtract · compliance"),
            ("lx_evaluation", "LangExtract · evaluation"),
            ("lx_facts", "LangExtract · dates & money"),
            ("lx_scope", "LangExtract · statement of work"),
        ):
            await _prog(sid, lab, "skipped", str(exc)[:80])

    trace: list[str] = [
        f"evidence_pack:pages={pack['pageCount']}",
        f"sections_found={sum(1 for s in pack['sections'].values() if s.get('found'))}",
        f"obligation_candidates={len(pack['obligationCandidates'])}",
        f"scope_candidates={len(pack.get('scopeCandidates') or [])}",
        f"lx_grounded={(pack.get('langextract') or {}).get('stats', {}).get('grounded')}",
        f"scoring_hits={len(pack['scoringHits'])}",
        f"missing={pack['missingSections']}",
    ]
    logger.info("A1 evidence pack ready %s", trace[:5])

    await _prog("sonnet_normalize", "Sonnet · normalize opportunity", "active")
    user_content = (
        f"RFP meta: {json.dumps(rfp_meta)}\n\n"
        "EVIDENCE PACK (use this first — do not re-read the whole RFP):\n"
        f"{json.dumps(_pack_for_llm(pack), indent=2)[:55000]}\n\n"
        "Extract the full opportunity JSON now. You MUST represent LangExtract compliance "
        "candidates in compliance.items (no silent drops). Tools only if a required field "
        "cannot be filled from this pack."
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]

    # Main call: no tools by default (0–3 only if model insists — capped at 2)
    raw, t1 = await _single_json_call(
        messages=messages,
        model=model,
        api_key=api_key,
        node_name=node_name,
        max_tokens=16384,
        tools=TOOL_DEFS,
        max_tool_rounds=2,
        doc=doc,
    )
    trace.extend(t1)
    await _prog("sonnet_normalize", "Sonnet · normalize opportunity", "done")

    await _prog("validators", "Deterministic validators", "active")
    model_prov = normalize_provenance(raw.pop("provenance", None))
    cleaned, errs = validate_opportunity_json(raw)
    for e in errs:
        trace.append(f"schema:{e}")

    cleaned["_pack_sow_hits"] = pack.get("scopeCandidates") or pack.get("sowCandidates")
    cleaned, det_fixes, det_warnings = apply_deterministic_pipeline(
        cleaned, pack, provenance=model_prov, skip_provenance=True, doc=doc
    )
    rs = cleaned.pop("_retention_stats", None)
    if rs:
        trace.append(
            f"compliance_retention:{rs.get('retentionRate')} "
            f"items={len((cleaned.get('compliance') or {}).get('items') or [])} "
            f"merged={rs.get('mergedIntoSonnet')} scope_class={rs.get('classifiedAsScope')}"
        )
    ledger = pack.get("_candidateDisposition") or {}
    if ledger.get("stats"):
        trace.append(f"disposition_stats:{ledger.get('stats')}")
    for f in det_fixes[:30]:
        trace.append(f"deterministic:{f}")
    for w in det_warnings[:15]:
        trace.append(f"validator_warn:{w}")

    triggers = repair_triggers(cleaned, pack, errs)
    triggers.extend(validator_repair_triggers(cleaned, pack, det_warnings))
    triggers = list(dict.fromkeys(triggers))
    await _prog("validators", "Deterministic validators", "done", f"{len(det_fixes)} fixes")
    if triggers:
        trace.append(f"repair_triggers:{triggers}")
        await _prog("repair", "Targeted repair (if needed)", "active", ", ".join(triggers[:4]))
        snapshot = json.loads(json.dumps(cleaned))  # deep copy before repair
        repair_user = (
            _targeted_repair_instruction(triggers, pack)
            + f"\n\nIssues: {triggers}\n\n"
            f"Current JSON:\n{json.dumps({k: v for k, v in cleaned.items() if k != 'provenance'})[:20000]}\n\n"
            f"Evidence pack (abbreviated):\n{json.dumps({k: pack[k] for k in pack if k not in ('sections', 'pages', '_candidateDisposition')})[:20000]}\n\n"
            "Use at most 2 targeted tool calls if needed. Do NOT shrink successCriteria, "
            "compliance, or evaluation.emphasis if already populated."
        )
        repaired, t2 = await _single_json_call(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": repair_user},
            ],
            model=model,
            api_key=api_key,
            node_name=f"{node_name}_repair",
            max_tokens=12288,
            tools=TOOL_DEFS,
            max_tool_rounds=_MAX_TOOL_ROUNDS_REPAIR,
            doc=doc,
        )
        trace.extend(t2)
        model_prov.extend(normalize_provenance(repaired.pop("provenance", None)))
        cleaned, errs2 = validate_opportunity_json(repaired)
        for e in errs2:
            trace.append(f"schema_after_repair:{e}")
        from opportunity_validators import accept_repair_if_improved

        cleaned, reject_notes = accept_repair_if_improved(snapshot, cleaned)
        for n in reject_notes:
            trace.append(n)
        cleaned, det_fixes2, det_warnings2 = apply_deterministic_pipeline(
            cleaned, pack, skip_provenance=True, doc=doc
        )
        det_warnings.extend(det_warnings2)
        for f in det_fixes2[:20]:
            trace.append(f"deterministic_after_repair:{f}")
    else:
        trace.append("repair:skipped")
        await _prog("repair", "Targeted repair (if needed)", "skipped")

    if triggers:
        await _prog("repair", "Targeted repair (if needed)", "done")

    cleaned, _, _ = apply_deterministic_pipeline(cleaned, pack, skip_provenance=True, doc=doc)
    cleaned.pop("_retention_stats", None)
    cleaned.pop("_pack_sow_hits", None)
    await _prog("provenance", "Ground provenance", "active")
    deduped = build_final_provenance(cleaned, pack)
    await _prog("provenance", "Ground provenance", "done", f"{len(deduped)} rows")
    # Never fall back to model provenance that may self-quote
    trace.append(f"provenance_count:{len(deduped)}")
    comp = cleaned.get("compliance") if isinstance(cleaned.get("compliance"), dict) else {}
    comp.pop("dispositionLedger", None)
    out = dict(cleaned)
    out.pop("_retention_stats", None)
    out["provenance"] = deduped
    # Internal disposition available in toolTrace only
    return out, trace, deduped




_QA_PROMPT = """You are a procurement extraction QA agent.
Given RFP tool excerpts and a draft opportunity JSON, return ONLY JSON patches:

{
  "patches": [
    {"operation":"replace","path":"/compliance/items/0/mandatory","value":false,"reason":"..."}
  ],
  "conditionalityErrors": [],
  "quantityMismatches": [],
  "contradictionsNotFlagged": [],
  "schemaErrors": []
}

Do NOT regenerate the whole document. Prefer empty patches when correct.
Paths use JSON Pointer. Allowed operations: replace, add, remove.
Focus on: conditionality, quantity fidelity, successCriteria pollution, unflagged contradictions.
"""


def _apply_pointer_patch(doc: dict[str, Any], path: str, value: Any, op: str) -> bool:
    if not path.startswith("/"):
        return False
    parts = [p for p in path.strip("/").split("/") if p]
    if not parts:
        return False
    cur: Any = doc
    for part in parts[:-1]:
        if isinstance(cur, list):
            cur = cur[int(part)]
        elif isinstance(cur, dict):
            cur = cur.setdefault(part, {})
        else:
            return False
    last = parts[-1]
    if op == "remove":
        if isinstance(cur, list):
            cur.pop(int(last))
        elif isinstance(cur, dict):
            cur.pop(last, None)
        return True
    if isinstance(cur, list):
        idx = int(last)
        if op == "add" and idx == len(cur):
            cur.append(value)
        else:
            cur[idx] = value
        return True
    if isinstance(cur, dict):
        cur[last] = value
        return True
    return False


async def _qa_patch_pass(
    *,
    doc: RfpDoc,
    payload: dict[str, Any],
    model: str,
    api_key: str,
    node_name: str,
) -> tuple[dict[str, Any], list[str]]:
    """Cheap correction pass — patches only."""
    trace: list[str] = []
    try:
        excerpt = doc.find_in_rfp(
            ["shall", "must", "optional", "as needed", "points", "score", "subcontractor"],
            limit=20,
        )
        messages = [
            {"role": "system", "content": _QA_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Draft JSON:\n{json.dumps(payload)[:20000]}\n\n"
                    f"RFP excerpts:\n{json.dumps(excerpt)[:8000]}"
                ),
            },
        ]
        msg, _meta = await _openrouter_tools_round(
            messages=messages,
            model=model,
            api_key=api_key,
            tools=None,
            max_tokens=4096,
            node_name=node_name,
        )
        content = _message_text(msg)
        qa = _extract_json_object(content) or {}
        patches = qa.get("patches") if isinstance(qa.get("patches"), list) else []
        out = json.loads(json.dumps(payload))  # deep copy
        for patch in patches[:25]:
            if not isinstance(patch, dict):
                continue
            op = str(patch.get("operation") or "replace")
            path = str(patch.get("path") or "")
            if _apply_pointer_patch(out, path, patch.get("value"), op):
                trace.append(f"qa:{op} {path}")
        return out, trace
    except Exception as exc:  # noqa: BLE001
        logger.warning("QA patch skipped: %s", exc)
        trace.append(f"qa_skipped:{exc}")
        return payload, trace


async def apply_opportunity_to_plan(
    *,
    plan: ProposalExecutionPlan,
    raw: dict[str, Any],
    provider: str = "openrouter",
) -> ProposalExecutionPlan:
    """Map validated demo JSON onto ProposalExecutionPlan (Agent 2 compatible)."""
    understanding_raw = _as_dict(raw, "understanding") or {}
    for key in UNDERSTANDING_FORBIDDEN_KEYS:
        understanding_raw.pop(key, None)
    memory_facts = (
        understanding_raw.get("memoryFacts")
        or understanding_raw.get("memory_facts")
        or {}
    )
    if isinstance(memory_facts, dict) and memory_facts:
        understanding_raw["memoryFacts"] = {
            str(k): str(v) for k, v in memory_facts.items() if v is not None
        }

    scope_raw = _as_dict(raw, "scope")
    scope_notes = str(scope_raw.get("notes") or "") if isinstance(scope_raw, dict) else ""

    try:
        understanding = OpportunityUnderstanding.model_validate(understanding_raw)
    except Exception as exc:
        logger.warning("OpportunityUnderstanding validation failed: %s", exc)
        understanding = OpportunityUnderstanding(confidence=0.3)
    understanding.confidence = clamp_confidence(understanding.confidence)
    if not understanding.client or not understanding.project_type:
        raise IntelligenceError("Opportunity extract missing client or projectType")

    plan.opportunity.understanding = understanding
    plan = set_provider(plan, provider)
    facts = {
        "clientName": understanding.client,
        "organizationType": understanding.org_type,
        "projectType": understanding.project_type,
        "industry": understanding.industry,
    }
    if isinstance(memory_facts, dict):
        for key, value in memory_facts.items():
            if value:
                facts[str(key)] = str(value)
    if scope_notes:
        facts["scopeNotes"] = scope_notes[:500]
    plan = merge_memory(plan, "opportunity_extract", facts)
    plan = append_decision(
        plan,
        agent="opportunity_extract",
        decision_text=f"Normalized opportunity for {understanding.client}",
        reason=f"projectType={understanding.project_type}; budget A1 evidence-pack",
        confidence=understanding.confidence,
    )
    # Re-inject scope without notes for pydantic
    apply_raw = dict(raw)
    apply_raw.pop("provenance", None)
    plan = _apply_opportunity_slices(plan, apply_raw, provider)
    return plan
