"""Demo-only LangExtract harvest via OpenRouter Gemini Flash.

PDF text is already extracted (RfpDoc). This module runs focused grounded
extraction passes and maps char spans back to page numbers.
"""

from __future__ import annotations

import logging
import os
import textwrap
from typing import Any

from app.core.config import settings

logger = logging.getLogger("rfp-two-agents-demo.langextract")

DEFAULT_LANGEXTRACT_MODEL = "google/gemini-2.5-flash"


def langextract_model_id() -> str:
    return (os.environ.get("LANGEXTRACT_MODEL") or DEFAULT_LANGEXTRACT_MODEL).strip()


def build_page_index(pages: list[str], *, max_chars: int = 100_000) -> tuple[str, list[tuple[int, int, int]]]:
    """Build the same concatenated text as RfpDoc.full_text + (start, end, page) spans.

    Returns (full_text, ranges) where ranges cover each page body in full_text
    (including the ``--- Page N ---\\n`` header for that page).
    """
    parts: list[str] = []
    ranges: list[tuple[int, int, int]] = []
    total = 0
    for i, page in enumerate(pages, start=1):
        if not (page or "").strip():
            continue
        block = f"--- Page {i} ---\n{page}"
        if total + len(block) > max_chars:
            remaining = max(0, max_chars - total)
            if remaining <= 0:
                break
            block = block[:remaining]
            start = total
            # account for join separator if not first
            if parts:
                start += 2  # "\n\n" before this part when joined — handled below
            parts.append(block)
            # rebuild offsets properly after join
            break
        parts.append(block)
        total += len(block) + (2 if len(parts) > 1 else 0)

    full = "\n\n".join(parts).strip()
    # Recompute accurate ranges on the final joined string
    ranges = []
    cursor = 0
    for i, page in enumerate(pages, start=1):
        if not (page or "").strip():
            continue
        header = f"--- Page {i} ---\n"
        marker = header + page
        # find from cursor
        idx = full.find(header, cursor)
        if idx < 0:
            # truncated page
            idx = full.find(header[: min(20, len(header))], cursor)
            if idx < 0:
                continue
            end = len(full)
            ranges.append((idx, end, i))
            break
        end = idx + len(marker)
        if end > len(full):
            end = len(full)
        ranges.append((idx, end, i))
        cursor = end
        if end >= len(full):
            break
    return full, ranges


def page_for_char(ranges: list[tuple[int, int, int]], pos: int | None) -> int | None:
    if pos is None:
        return None
    for start, end, page in ranges:
        if start <= pos < end:
            return page
    if ranges and pos >= ranges[-1][0]:
        return ranges[-1][2]
    return None


def _openrouter_config():
    import langextract as lx
    from langextract.factory import ModelConfig

    api_key = (settings.openrouter_api_key or "").strip()
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY missing for LangExtract")
    base = (settings.openrouter_base_url or "https://openrouter.ai/api/v1").rstrip("/")
    model_id = langextract_model_id()
    return ModelConfig(
        model_id=model_id,
        provider="openai",
        provider_kwargs={"api_key": api_key, "base_url": base},
    ), model_id


def _compliance_examples():
    import langextract as lx

    sample = (
        "Offeror shall submit three professional references. "
        "If subcontractors will be used, Offeror must provide subcontractor resumes."
    )
    return [
        lx.data.ExampleData(
            text=sample,
            extractions=[
                lx.data.Extraction(
                    extraction_class="obligation",
                    extraction_text="Offeror shall submit three professional references.",
                    attributes={
                        "mandatory": "true",
                        "conditional": "false",
                        "owner_hint": "Offeror",
                    },
                ),
                lx.data.Extraction(
                    extraction_class="obligation",
                    extraction_text=(
                        "If subcontractors will be used, Offeror must provide "
                        "subcontractor resumes."
                    ),
                    attributes={
                        "mandatory": "false",
                        "conditional": "true",
                        "owner_hint": "Offeror",
                    },
                ),
            ],
        )
    ]


def _evaluation_examples():
    import langextract as lx

    sample = (
        "5. QUOTE EVALUATION. Technical Approach — 40 points. "
        "Price — 20 points. Total maximum score is 100 points."
    )
    return [
        lx.data.ExampleData(
            text=sample,
            extractions=[
                lx.data.Extraction(
                    extraction_class="scoring_criterion",
                    extraction_text="Technical Approach — 40 points",
                    attributes={"name": "Technical Approach", "weight": "40"},
                ),
                lx.data.Extraction(
                    extraction_class="scoring_criterion",
                    extraction_text="Price — 20 points",
                    attributes={"name": "Price", "weight": "20"},
                ),
                lx.data.Extraction(
                    extraction_class="total_points",
                    extraction_text="Total maximum score is 100 points",
                    attributes={"totalPoints": "100"},
                ),
            ],
        )
    ]


def _scope_examples():
    import langextract as lx

    sample = (
        "Contractor shall develop a Comprehensive Plan and Master Messaging Document. "
        "County may request up to six CBSM presentations annually as needed."
    )
    return [
        lx.data.ExampleData(
            text=sample,
            extractions=[
                lx.data.Extraction(
                    extraction_class="scope_mandatory",
                    extraction_text="Contractor shall develop a Comprehensive Plan and Master Messaging Document.",
                    attributes={
                        "verb": "develop",
                        "actor": "Contractor",
                        "qualifier": "",
                    },
                ),
                lx.data.Extraction(
                    extraction_class="scope_optional",
                    extraction_text="County may request up to six CBSM presentations annually as needed.",
                    attributes={
                        "verb": "provide",
                        "actor": "Contractor",
                        "qualifier": "up to six",
                        "frequency": "annually",
                    },
                ),
            ],
        )
    ]


def _facts_examples():
    import langextract as lx

    sample = (
        "The County anticipates the contract term to begin July 1, 2027. "
        "Not-to-exceed amount is $250,000."
    )
    return [
        lx.data.ExampleData(
            text=sample,
            extractions=[
                lx.data.Extraction(
                    extraction_class="date",
                    extraction_text="July 1, 2027",
                    attributes={"role": "projectStart"},
                ),
                lx.data.Extraction(
                    extraction_class="money",
                    extraction_text="$250,000",
                    attributes={"role": "ceiling"},
                ),
            ],
        )
    ]


def _hit_from_extraction(
    ext: Any,
    *,
    ranges: list[tuple[int, int, int]],
) -> dict[str, Any] | None:
    interval = getattr(ext, "char_interval", None)
    if interval is None:
        return None
    start = getattr(interval, "start_pos", None)
    end = getattr(interval, "end_pos", None)
    if start is None:
        return None
    page = page_for_char(ranges, int(start))
    text = str(getattr(ext, "extraction_text", "") or "").strip()
    if not text:
        return None
    attrs = getattr(ext, "attributes", None) or {}
    if not isinstance(attrs, dict):
        attrs = {}
    return {
        "class": str(getattr(ext, "extraction_class", "") or ""),
        "text": text[:500],
        "page": page,
        "charStart": int(start),
        "charEnd": int(end) if end is not None else None,
        "attributes": {str(k): str(v) if not isinstance(v, list) else v for k, v in attrs.items()},
        "sourceText": text[:500],
    }


def _run_pass(
    *,
    text: str,
    ranges: list[tuple[int, int, int]],
    prompt: str,
    examples: list[Any],
    config: Any,
) -> tuple[list[dict[str, Any]], int]:
    import langextract as lx

    result = lx.extract(
        text_or_documents=text,
        prompt_description=prompt,
        examples=examples,
        config=config,
    )
    extractions = list(getattr(result, "extractions", None) or [])
    hits: list[dict[str, Any]] = []
    dropped = 0
    for ext in extractions:
        hit = _hit_from_extraction(ext, ranges=ranges)
        if hit is None:
            dropped += 1
            continue
        hits.append(hit)
    return hits, dropped


def run_langextract_harvest(
    doc: Any,
    on_progress: Any | None = None,
) -> dict[str, Any]:
    """Sync harvest — call via asyncio.to_thread from the demo server."""
    empty = {
        "complianceHits": [],
        "evaluationHits": [],
        "factHits": [],
        "stats": {
            "passes": 0,
            "grounded": 0,
            "ungrounded_dropped": 0,
            "model": langextract_model_id(),
            "error": None,
        },
    }
    try:
        config, model_id = _openrouter_config()
    except Exception as exc:  # noqa: BLE001
        logger.warning("LangExtract config failed: %s", exc)
        empty["stats"]["error"] = str(exc)[:200]
        return empty

    pages = list(getattr(doc, "pages", None) or [])
    text, ranges = build_page_index(pages)
    if len(text) < 80:
        empty["stats"]["error"] = "RFP text too short for LangExtract"
        return empty

    # Cap very long docs for spike cost control
    if len(text) > 80_000:
        text = text[:80_000]
        logger.info("LangExtract text truncated to 80k chars")

    total_dropped = 0
    compliance: list[dict[str, Any]] = []
    evaluation: list[dict[str, Any]] = []
    facts: list[dict[str, Any]] = []
    scope: list[dict[str, Any]] = []

    passes = [
        (
            "compliance",
            textwrap.dedent(
                """\
                Extract bidder/contractor obligations from the RFP.
                Use exact text for extraction_text. Do not paraphrase.
                Classes: obligation.
                Attributes: mandatory (true/false), conditional (true/false), owner_hint.
                Prefer independently answerable requirements (submit, provide, include, certify…).
                Preserve conditionality in the extracted text when present."""
            ),
            _compliance_examples(),
        ),
        (
            "evaluation",
            textwrap.dedent(
                """\
                Extract explicit evaluation/scoring language only.
                Use exact text. Do not invent points.
                Classes: scoring_criterion, total_points, evaluation_factor.
                Attributes when present: name, weight, totalPoints.
                If no numeric scoring exists, extract nothing rather than inventing scores."""
            ),
            _evaluation_examples(),
        ),
        (
            "facts",
            textwrap.dedent(
                """\
                Extract dates, dollar amounts, and numeric limits from the RFP.
                Use exact text. Do not paraphrase.
                Classes: date, money, quantity.
                Attributes: role when clear (projectStart, completion, ceiling, references, etc.)."""
            ),
            _facts_examples(),
        ),
        (
            "scope",
            textwrap.dedent(
                """\
                Extract Statement of Work performance obligations with HIGH RECALL.
                Use exact source sentences — do not summarize away qualifiers.
                Classes: scope_mandatory, scope_optional, scope_dependency, scope_exclusion.
                Attributes when present: verb, actor, object, condition, quantity, frequency,
                deadline, qualifier, section_hint.
                Preserve: at least, up to, may, as needed, annually, monthly, quarterly.
                Do not convert may→shall or up to six→six."""
            ),
            _scope_examples(),
        ),
    ]

    _lx_labels = {
        "compliance": "LangExtract · compliance",
        "evaluation": "LangExtract · evaluation",
        "facts": "LangExtract · dates & money",
        "scope": "LangExtract · statement of work",
    }
    for i, (name, prompt, examples) in enumerate(passes):
        step_id = f"lx_{name}"
        label = _lx_labels.get(name, f"LangExtract · {name}")
        if on_progress:
            try:
                on_progress(step_id, label, "active", "", i, len(passes))
            except Exception:  # noqa: BLE001
                pass
        try:
            hits, dropped = _run_pass(
                text=text,
                ranges=ranges,
                prompt=prompt,
                examples=examples,
                config=config,
            )
            total_dropped += dropped
            if name == "compliance":
                compliance = hits
            elif name == "evaluation":
                evaluation = hits
            elif name == "scope":
                scope = hits
            else:
                facts = hits
            logger.info(
                "LangExtract pass=%s grounded=%s dropped=%s model=%s",
                name,
                len(hits),
                dropped,
                model_id,
            )
            if on_progress:
                try:
                    on_progress(
                        step_id, label, "done", f"{len(hits)} grounded", i, len(passes)
                    )
                except Exception:  # noqa: BLE001
                    pass
        except Exception as exc:  # noqa: BLE001
            logger.warning("LangExtract pass %s failed: %s", name, exc)
            empty["stats"]["error"] = f"{name}:{str(exc)[:160]}"
            if on_progress:
                try:
                    on_progress(step_id, label, "error", str(exc)[:120], i, len(passes))
                except Exception:  # noqa: BLE001
                    pass

    grounded = len(compliance) + len(evaluation) + len(facts) + len(scope)
    return {
        "complianceHits": compliance[:120],
        "evaluationHits": evaluation[:50],
        "factHits": facts[:50],
        "scopeHits": scope[:120],
        "stats": {
            "passes": 4,
            "grounded": grounded,
            "ungrounded_dropped": total_dropped,
            "model": model_id,
            "error": empty["stats"].get("error"),
            "textChars": len(text),
        },
    }


def provenance_from_langextract(harvest: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for i, hit in enumerate(harvest.get("complianceHits") or []):
        if not isinstance(hit, dict):
            continue
        rows.append(
            {
                "path": f"/langextract/compliance/{i}",
                "value": hit.get("text"),
                "sourcePage": hit.get("page"),
                "sourceSection": hit.get("class") or "obligation",
                "sourceText": hit.get("sourceText") or hit.get("text"),
            }
        )
    for i, hit in enumerate(harvest.get("evaluationHits") or []):
        if not isinstance(hit, dict):
            continue
        rows.append(
            {
                "path": f"/langextract/evaluation/{i}",
                "value": hit.get("text"),
                "sourcePage": hit.get("page"),
                "sourceSection": hit.get("class") or "evaluation",
                "sourceText": hit.get("sourceText") or hit.get("text"),
            }
        )
    for i, hit in enumerate(harvest.get("factHits") or []):
        if not isinstance(hit, dict):
            continue
        rows.append(
            {
                "path": f"/langextract/facts/{i}",
                "value": hit.get("text"),
                "sourcePage": hit.get("page"),
                "sourceSection": hit.get("class") or "fact",
                "sourceText": hit.get("sourceText") or hit.get("text"),
            }
        )
    return rows
