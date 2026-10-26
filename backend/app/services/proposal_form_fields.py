"""RFP built-in forms — fill field values from the KB and keep every RFP label.

Generation-time counterpart of Ask Ralph's manual-fill resolution: each RFP
form label is looked up in the knowledge base and a value is kept only when it
is copied from the retrieved evidence. Labels with no KB answer stay as
``[MANUAL FILL: Sonja — <label>]`` so they show in the manual-fill list and can
be answered in Ask Ralph. Later passes (Ralph hard-fit, dedup, rewrites) must
never lose a label, so ``restore_missing_form_fields`` rebuilds the table.
"""

from __future__ import annotations

import asyncio
import logging

from app.models.proposal import ProposalDraft, RfpSectionMap
from app.services.proposal_submission_authority import (
    FORM_FIELDS_HEADING,
    _fold_for_grounding,
    instrument_is_checklist,
)

logger = logging.getLogger(__name__)

_FILL_AGENT = "rfp_form_field_fill"
_LABELS_PER_RETRIEVE = 5
_MAX_LABELS = 40
_MAX_EVIDENCE_CHARS = 40_000

_FILL_SYSTEM = """You fill in the buyer's RFP form for zö agency (the offeror).

You receive the form's field labels exactly as the RFP prints them, and
evidence copied from zö agency's knowledge base.

For each label return the value zö agency should enter, COPIED character for
character from the evidence. Rules:
- Use only the evidence. Never answer from memory, never guess, never combine
  facts into a value the evidence does not state.
- Return null when the evidence does not state the value, when the field needs
  a wet/e-signature, a signing date, a notary, or a yes/no attestation that a
  person must make.
- Keep each label exactly as given — do not rename, merge, or add labels.

Return ONLY JSON:
{"fields": [{"label": "<label as given>", "value": "<copied value or null>"}]}"""


def form_field_sections(rfp_sections: list[RfpSectionMap]) -> list[RfpSectionMap]:
    """Form / disclosure / references tabs whose RFP printed field labels."""
    return [
        mapped
        for mapped in rfp_sections or []
        if instrument_is_checklist(getattr(mapped, "submission_instrument", None))
        and (mapped.form_fields or [])
    ]


def _clean_label(label: str) -> str:
    return " ".join(str(label or "").split()).strip()


def collect_form_field_labels(rfp_sections: list[RfpSectionMap]) -> list[str]:
    seen: set[str] = set()
    labels: list[str] = []
    for mapped in form_field_sections(rfp_sections):
        for field in mapped.form_fields:
            label = _clean_label(field)
            key = label.casefold()
            if label and key not in seen:
                seen.add(key)
                labels.append(label)
    return labels


async def _retrieve_label_evidence(
    labels: list[str],
    *,
    rfp_client: str,
    rfp_sector: str,
) -> str:
    from app.services.proposal_section_kb_evidence import (
        fetch_packed_section_kb_evidence,
    )

    batches = [
        labels[i : i + _LABELS_PER_RETRIEVE]
        for i in range(0, len(labels), _LABELS_PER_RETRIEVE)
    ]

    async def _one(batch: list[str]) -> str:
        try:
            block, _sources = await fetch_packed_section_kb_evidence(
                section_title="RFP form fields",
                requirements=[f"zö agency {label}" for label in batch],
                rfp_client=rfp_client,
                rfp_sector=rfp_sector,
                max_chars=8_000,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("form field KB retrieve failed for %s: %s", batch[:2], exc)
            return ""
        return block

    blocks = await asyncio.gather(*(_one(batch) for batch in batches))
    return "\n\n".join(b for b in blocks if (b or "").strip())


async def resolve_form_field_values_from_kb(
    labels: list[str],
    *,
    rfp_client: str = "",
    rfp_sector: str = "",
    static_section_text: str = "",
) -> dict[str, str]:
    """Map RFP form labels → values copied from KB evidence (ungrounded dropped).

    ``static_section_text`` — already-drafted Zo Sections 1–3 (Business
    Information is drafted from companyfacts), reused as extra evidence.
    """
    labels = [label for label in (_clean_label(x) for x in labels) if label][:_MAX_LABELS]
    if not labels:
        return {}

    kb_block = await _retrieve_label_evidence(
        labels, rfp_client=rfp_client, rfp_sector=rfp_sector
    )
    parts: list[str] = []
    if (static_section_text or "").strip():
        parts.append(
            "=== ZÖ SECTIONS 1–3 (already drafted from KB company facts) ===\n"
            + static_section_text.strip()[:12_000]
        )
    if kb_block.strip():
        parts.append(kb_block)
    evidence = "\n\n".join(parts)[:_MAX_EVIDENCE_CHARS]
    if not evidence.strip():
        logger.info("form field fill: no KB evidence for %d labels", len(labels))
        return {}

    from app.services.proposal_intelligence.agent_base import safe_chat_json

    user = (
        "FORM FIELD LABELS:\n"
        + "\n".join(f"- {label}" for label in labels)
        + "\n\nEVIDENCE:\n"
        + evidence
    )
    raw, _provider = await safe_chat_json(
        [
            {"role": "system", "content": _FILL_SYSTEM},
            {"role": "user", "content": user},
        ],
        max_tokens=2_000,
        temperature=0.0,
        agent_name=_FILL_AGENT,
    )
    rows = raw.get("fields") if isinstance(raw, dict) else None
    if not isinstance(rows, list):
        return {}

    by_key = {label.casefold(): label for label in labels}
    folded_evidence = _fold_for_grounding(evidence)
    values: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        label = by_key.get(_clean_label(row.get("label") or "").casefold())
        value = _clean_label(row.get("value") or "")
        if not label or not value or "[" in value:
            continue
        folded_value = _fold_for_grounding(value)
        # Same grounding bar as RFP quotes: the value must be in the evidence.
        if not folded_value or folded_value not in folded_evidence:
            logger.info("form field fill: dropped ungrounded value for %r", label)
            continue
        values[label] = value
    logger.info(
        "form field fill: %d/%d labels filled from KB", len(values), len(labels)
    )
    return values


def _table_cell_label(label: str) -> str:
    return _clean_label(label).replace("|", "/")


def _split_row(line: str) -> list[str]:
    inner = line.strip().strip("|")
    return [cell.strip() for cell in inner.split("|")]


def _form_table_span(lines: list[str]) -> tuple[int, int] | None:
    """[start, end) line span of the RFP form-field table block, if present."""
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == FORM_FIELDS_HEADING)
    except StopIteration:
        return None
    end = start + 1
    while end < len(lines) and not lines[end].strip():
        end += 1
    while end < len(lines) and lines[end].strip().startswith("|"):
        end += 1
    if end < len(lines) and not lines[end].strip():
        end += 1
    return start, end


def _existing_responses(lines: list[str], span: tuple[int, int] | None) -> dict[str, str]:
    if span is None:
        return {}
    out: dict[str, str] = {}
    for line in lines[span[0] : span[1]]:
        if not line.strip().startswith("|"):
            continue
        cells = _split_row(line)
        if len(cells) < 2 or not cells[0] or set(cells[0]) <= {"-", ":", " "}:
            continue
        if cells[0].casefold() == "field" and cells[1].casefold() == "response":
            continue
        if cells[1]:
            out[cells[0]] = cells[1]
    return out


def restore_missing_form_fields(
    draft: ProposalDraft,
    rfp_sections: list[RfpSectionMap],
) -> tuple[ProposalDraft, list[str]]:
    """Rebuild a form tab's field table when any RFP label went missing.

    Values already in the table are kept; a label that lost its row comes
    back as MANUAL FILL — never invented.
    """
    mapped_by_id = {mapped.id: mapped for mapped in form_field_sections(rfp_sections)}
    if not mapped_by_id or draft is None:
        return draft, []

    logs: list[str] = []
    sections = []
    changed = False
    for section in draft.sections:
        mapped = mapped_by_id.get(section.id)
        if mapped is None:
            sections.append(section)
            continue
        lines = (section.content or "").splitlines()
        span = _form_table_span(lines)
        existing = _existing_responses(lines, span)
        present = {label.casefold() for label in existing}
        if span is not None:
            for line in lines[span[0] : span[1]]:
                if line.strip().startswith("|"):
                    present.add(_split_row(line)[0].casefold())
        labels = [_clean_label(f) for f in mapped.form_fields if _clean_label(f)]
        missing = [l for l in labels if _table_cell_label(l).casefold() not in present]
        if not missing:
            sections.append(section)
            continue

        # Exact label → value only: containment would copy "Company Name" into "Name".
        kept = {cell.casefold(): value for cell, value in existing.items()}
        table = [FORM_FIELDS_HEADING, "", "| Field | Response |", "| --- | --- |"]
        for label in labels:
            cell = _table_cell_label(label)
            value = kept.get(cell.casefold()) or f"[MANUAL FILL: Sonja — {label}]"
            table.append(f"| {cell} | {value.replace('|', '/')} |")
        table.append("")
        if span is not None:
            lines[span[0] : span[1]] = table
        else:
            insert_at = next(
                (
                    i
                    for i, line in enumerate(lines)
                    if line.strip().startswith("[DESIGNER NOTE")
                    or line.strip().startswith("[MANUAL FILL")
                ),
                len(lines),
            )
            block = list(table)
            if insert_at and lines[insert_at - 1].strip():
                block.insert(0, "")
            lines[insert_at:insert_at] = block
        sections.append(section.model_copy(update={"content": "\n".join(lines)}))
        changed = True
        logs.append(
            f"form-fields:restored {len(missing)} RFP label(s) in "
            f"{section.title or section.id}: {', '.join(missing[:6])}"
        )

    if not changed:
        return draft, []
    return draft.model_copy(update={"sections": sections}), logs
