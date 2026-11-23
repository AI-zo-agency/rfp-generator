"""Refresh Fit after a cite-linked attachment change — attachment only, not full RFP.

Keeps the prior Go/No-Go analysis and runs one light LLM pass on the new (or
removed) exhibit body to (1) resolve/re-flag unresolved cites and (2) nudge
scores. Falls back to a full ``analyze_rfp`` only when there is no prior Fit.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from app.models.go_no_go import (
    GoNoGoAnalysis,
    GoNoGoDecisionMatrixRow,
    RfpPacketRead,
    UnresolvedReference,
)
from app.models.rfp import RfpRecord
from app.services import llm
from app.services.rfp_packet_files import get_packet_file_by_cite, list_packet_files

logger = logging.getLogger(__name__)

_ATTACHMENT_EXCERPT_CHARS = 24_000
_PRIOR_SUMMARY_CHARS = 1_200

AttachmentAction = Literal["upload", "delete"]


def _clamp_score(value: Any, fallback: int | None) -> int | None:
    if value is None:
        return fallback
    try:
        n = int(value)
    except (TypeError, ValueError):
        return fallback
    return max(0, min(5, n))


def _load_attachment_text(rfp_id: str, cite_target: str) -> tuple[str, str]:
    """Return (filename, extracted_text) for this RFP's cite only."""
    item = get_packet_file_by_cite(rfp_id, cite_target)
    if not item:
        return "", ""
    try:
        from app.services.rfp_content import (
            extract_pdf_text_from_bytes,
            is_image_only_pdf,
            load_rfp_pdf_bytes,
        )

        raw = load_rfp_pdf_bytes(rfp_id, item.storage_path)
        if not raw:
            return item.filename, ""
        text = extract_pdf_text_from_bytes(raw, max_chars=_ATTACHMENT_EXCERPT_CHARS)
        if not text.strip() and is_image_only_pdf(raw, extracted_text=text):
            try:
                from app.services.pdf_ocr import extract_text_via_ocr

                ocr_text, _src = extract_text_via_ocr(
                    raw, rfp_id=rfp_id, max_chars=_ATTACHMENT_EXCERPT_CHARS
                )
                text = ocr_text or text
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "attachment OCR failed rfp_id=%s cite=%s: %s",
                    rfp_id,
                    cite_target,
                    str(exc)[:160],
                )
        return item.filename, (text or "")[:_ATTACHMENT_EXCERPT_CHARS]
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "attachment text load failed rfp_id=%s cite=%s: %s",
            rfp_id,
            cite_target,
            str(exc)[:160],
        )
        return item.filename, ""


def _prior_analysis(rfp: RfpRecord) -> GoNoGoAnalysis | None:
    raw = rfp.go_no_go_analysis
    if not raw or not isinstance(raw, dict):
        return None
    try:
        return GoNoGoAnalysis.model_validate(raw)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "prior Fit parse failed rfp_id=%s: %s",
            rfp.id,
            str(exc)[:160],
        )
        return None


def _matrix_brief(analysis: GoNoGoAnalysis) -> list[dict[str, Any]]:
    rows = []
    for row in analysis.decision_matrix or []:
        rows.append(
            {
                "dimension": row.dimension,
                "score": row.score,
                "notes": (row.notes or "")[:240],
            }
        )
    return rows


def apply_attachment_judgment(
    prior: GoNoGoAnalysis,
    *,
    cite_target: str,
    action: AttachmentAction,
    raw: dict[str, Any],
    cite_quote: str = "",
) -> GoNoGoAnalysis:
    """Merge light LLM judgment onto the prior analysis (testable, no network)."""
    packet = prior.packet_read or RfpPacketRead()
    prior_refs = list(packet.unresolved_references or [])

    if action == "delete":
        # Re-flag this cite; keep every other unresolved row.
        remaining = [
            r
            for r in prior_refs
            if r.target.strip().casefold() != cite_target.strip().casefold()
        ]
        remaining.append(
            UnresolvedReference(
                target=cite_target,
                quote=(cite_quote or f"{cite_target} was removed from this RFP packet."),
            )
        )
        packet = packet.model_copy(update={"unresolved_references": remaining})
    else:
        resolved_raw = raw.get("resolvedTargets") or raw.get("resolved_targets") or []
        resolved = {
            str(x).strip().casefold()
            for x in resolved_raw
            if str(x).strip()
        }
        # Always consider the upload's own cite when the model says the body matches.
        body_matches = bool(raw.get("bodyMatchesCite") or raw.get("body_matches_cite"))
        if body_matches:
            resolved.add(cite_target.strip().casefold())
        remaining = [
            r
            for r in prior_refs
            if r.target.strip().casefold() not in resolved
        ]
        # If prior had no list but body does not match, keep/flag this cite.
        if not body_matches and not any(
            r.target.strip().casefold() == cite_target.strip().casefold()
            for r in remaining
        ):
            remaining.append(
                UnresolvedReference(
                    target=cite_target,
                    quote=(
                        cite_quote
                        or f"Cited {cite_target} — attached file does not supply that body."
                    ),
                )
            )
        packet = packet.model_copy(update={"unresolved_references": remaining})

    fit = _clamp_score(raw.get("fitScore") or raw.get("fit_score"), prior.fit_score)
    worth = _clamp_score(
        raw.get("worthScore") or raw.get("worth_score"), prior.worth_score
    )
    rec = raw.get("recommendation")
    if rec not in ("go", "review", "no_go", None):
        rec = prior.recommendation
    if isinstance(rec, str):
        rec = rec.strip().casefold()
        if rec not in ("go", "review", "no_go"):
            rec = prior.recommendation

    matrix = list(prior.decision_matrix or [])
    updates = raw.get("decisionMatrix") or raw.get("decision_matrix") or []
    if isinstance(updates, list) and updates:
        by_dim = {
            str(u.get("dimension") or "").strip().casefold(): u
            for u in updates
            if isinstance(u, dict)
        }
        next_matrix: list[GoNoGoDecisionMatrixRow] = []
        for row in matrix:
            key = (row.dimension or "").strip().casefold()
            u = by_dim.get(key)
            if not u:
                next_matrix.append(row)
                continue
            score = _clamp_score(u.get("score"), row.score)
            notes = str(u.get("notes") or row.notes or "")
            next_matrix.append(
                row.model_copy(
                    update={
                        "score": score if score is not None else row.score,
                        "notes": notes[:800],
                    }
                )
            )
        matrix = next_matrix

    note = str(raw.get("attachmentNote") or raw.get("attachment_note") or "").strip()
    summary = prior.summary or ""
    if note:
        summary = f"{note}\n\n{summary}".strip()

    gaps = list(prior.critical_gaps or [])
    extra_gaps = raw.get("criticalGaps") or raw.get("critical_gaps") or []
    if isinstance(extra_gaps, list):
        for g in extra_gaps:
            s = str(g).strip()
            if s and s not in gaps:
                gaps.append(s)

    return prior.model_copy(
        update={
            "fit_score": fit,
            "worth_score": worth,
            "recommendation": rec if rec is not None else prior.recommendation,
            "summary": summary[:8_000],
            "decision_matrix": matrix,
            "critical_gaps": gaps[:24],
            "packet_read": packet,
            "provider": (prior.provider or "attachment-refresh"),
        }
    )


async def _judge_attachment(
    *,
    cite_target: str,
    cite_quote: str,
    filename: str,
    attachment_text: str,
    action: AttachmentAction,
    prior: GoNoGoAnalysis,
) -> dict[str, Any]:
    if not llm.is_configured():
        return {}

    prior_refs = []
    if prior.packet_read and prior.packet_read.unresolved_references:
        prior_refs = [
            {"target": r.target, "quote": r.quote}
            for r in prior.packet_read.unresolved_references
        ]

    system = (
        "You refresh Stage 1 Fit for zö after ONE packet attachment change on "
        "THIS RFP only. Do NOT re-read or re-judge the whole solicitation. "
        "Use only the prior Fit snapshot and the attachment excerpt.\n\n"
        "Rules:\n"
        "- bodyMatchesCite=true only when the attachment body actually is the "
        "cited part (by meaning: scope, agreement sample, deliverables). A "
        "filename or label alone does not count. Unrelated/random pages → false.\n"
        "- resolvedTargets: prior unresolved cite names this body truly supplies.\n"
        "- Adjust fitScore, worthScore, recommendation, and decisionMatrix only "
        "when the attachment (or its continued absence) changes Fit. Prefer "
        "small deltas; keep prior scores when nothing material changed.\n"
        "- attachmentNote: one short sentence for the dashboard.\n"
        "Return JSON only."
    )
    user = (
        f"Action: {action}\n"
        f"Cite target: {cite_target}\n"
        f"Cite quote: {(cite_quote or '')[:500]}\n"
        f"Filename: {filename or '(none)'}\n\n"
        f"Prior unresolved references:\n{prior_refs}\n\n"
        f"Prior scores: fit={prior.fit_score} worth={prior.worth_score} "
        f"recommendation={prior.recommendation}\n"
        f"Prior decisionMatrix:\n{_matrix_brief(prior)}\n"
        f"Prior summary (truncated):\n{(prior.summary or '')[:_PRIOR_SUMMARY_CHARS]}\n\n"
        f"Attachment excerpt:\n{(attachment_text or '(empty — file removed or unreadable)')[:_ATTACHMENT_EXCERPT_CHARS]}\n\n"
        "Return JSON with keys: bodyMatchesCite (bool), resolvedTargets (string[]), "
        "fitScore, worthScore, recommendation, decisionMatrix "
        "([{dimension, score, notes}]), attachmentNote, criticalGaps (string[])."
    )
    try:
        raw, _provider = await llm.chat_json(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=2048,
            temperature=0.0,
            tier="light",
            node_name="go_no_go_attachment_refresh",
        )
        return raw if isinstance(raw, dict) else {}
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "attachment Fit judge failed cite=%s: %s",
            cite_target,
            str(exc)[:200],
        )
        return {}


async def refresh_go_no_go_for_attachment(
    rfp: RfpRecord,
    *,
    cite_target: str,
    action: AttachmentAction = "upload",
    cite_quote: str = "",
) -> GoNoGoAnalysis:
    """Attachment-scoped Fit refresh; full analyze only when no prior Fit exists."""
    target = (cite_target or "").strip()
    if not target:
        raise ValueError("cite_target is required")

    prior = _prior_analysis(rfp)
    if prior is None:
        logger.info(
            "attachment Fit refresh: no prior analysis for %s — full analyze",
            rfp.id,
        )
        from app.services.go_no_go_service import analyze_rfp

        return await analyze_rfp(rfp)

    filename = ""
    attachment_text = ""
    quote = (cite_quote or "").strip()
    if action == "upload":
        item = get_packet_file_by_cite(rfp.id, target)
        if item and not quote:
            quote = item.cite_quote or ""
        filename, attachment_text = _load_attachment_text(rfp.id, target)
    else:
        # Delete: no body; still nudge scores / re-flag from prior + cite label.
        attachment_text = ""
        filename = ""

    raw = await _judge_attachment(
        cite_target=target,
        cite_quote=quote,
        filename=filename,
        attachment_text=attachment_text,
        action=action,
        prior=prior,
    )
    if not raw:
        # Deterministic fallback: upload without judge → keep flag unless empty body
        # is somehow fine; delete → always re-flag.
        raw = {
            "bodyMatchesCite": False,
            "resolvedTargets": [],
            "attachmentNote": (
                f"Attachment for {target} removed — cite re-flagged; scores kept."
                if action == "delete"
                else (
                    f"Attached “{filename or 'file'}” for {target}; Fit could not "
                    "confirm the cite body — flag kept; scores kept."
                )
            ),
            "fitScore": prior.fit_score,
            "worthScore": prior.worth_score,
            "recommendation": prior.recommendation,
        }

    updated = apply_attachment_judgment(
        prior,
        cite_target=target,
        action=action,
        raw=raw,
        cite_quote=quote,
    )
    logger.info(
        "attachment Fit refresh rfp_id=%s cite=%s action=%s unresolved=%s fit=%s worth=%s",
        rfp.id,
        target,
        action,
        len(updated.packet_read.unresolved_references) if updated.packet_read else 0,
        updated.fit_score,
        updated.worth_score,
    )
    return updated
