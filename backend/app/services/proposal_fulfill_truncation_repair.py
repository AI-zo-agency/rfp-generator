"""Scan RFP — fix sections cut off mid-sentence (output limits / bad reframe)."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from app.models.proposal import ProposalDraft, ProposalSection
from app.models.rfp import RfpRecord
from app.services import llm
from app.services.proposal_drafting_graph import _looks_truncated_prose
from app.services.proposal_fulfill_guard import fulfill_scan_preserves_section

logger = logging.getLogger(__name__)

_CLOSING_MIN_COMPLETE: dict[str, str] = {
    "rfp-closing-cvc": """## Contractor Vendor Certification (CVC) / Exhibit H

[MANUAL FILL: Firm Name] acknowledges the requirement to complete and submit the Contractor Vendor Certification (CVC) / Exhibit H on the buyer's official template, signed by an authorized representative, and returned with this proposal.

- Complete every field on the buyer's Exhibit H / CVC form — do not substitute a custom layout.
- [MANUAL FILL: attach signed Exhibit H / CVC PDF with the submission package]
- Do not invent vendor certification numbers, DUNS, or registration IDs.
""",
    "rfp-closing-signature": """## Authorized Signature

By signing below, the undersigned certifies that the information provided in this proposal is accurate and complete, and that the offeror agrees to all terms and conditions of this RFP.

| Field | Response |
| --- | --- |
| Authorized Representative (signature) | [MANUAL FILL: wet/digital signature] |
| Printed Name | [MANUAL FILL: authorized signatory] |
| Title | [MANUAL FILL] |
| Date | [MANUAL FILL] |
| Firm Name | [MANUAL FILL: legal entity name from Section 1] |
""",
}


def looks_truncated_for_fulfill(content: str) -> bool:
    stripped = (content or "").rstrip()
    if not stripped:
        return False
    if _looks_truncated_prose(stripped):
        return True
    if len(stripped) < 60:
        return False
    if stripped.endswith("[") or stripped.endswith("("):
        return True
    if re.search(r"\bas outlined in\s*$", stripped, re.I):
        return True
    if re.search(r"complete and submit\s*$", stripped, re.I):
        return True
    return False


def _closing_template_for_section(section: ProposalSection) -> str | None:
    sid = section.id or ""
    if sid in _CLOSING_MIN_COMPLETE:
        return _CLOSING_MIN_COMPLETE[sid]
    title_cf = (section.title or "").casefold()
    if "vendor certification" in title_cf or "cvc" in title_cf:
        return _CLOSING_MIN_COMPLETE["rfp-closing-cvc"]
    if "authorized signature" in title_cf:
        return _CLOSING_MIN_COMPLETE["rfp-closing-signature"]
    return None


async def _llm_complete_truncated_section(
    *,
    section: ProposalSection,
    rfp: RfpRecord,
) -> str | None:
    if not llm.is_configured():
        return None
    body = section.content or ""
    system = (
        "Complete ONE truncated proposal section. Finish cut-off sentences and lists only.\n"
        "Rules:\n"
        "- Do NOT invent clients, case studies, metrics, or reference contacts.\n"
        "- Do NOT add new Case Study blocks.\n"
        "- Keep existing facts; append the minimum text needed for a complete section.\n"
        'Return JSON: {"content": "full markdown section"}'
    )
    user = (
        f"Client: {rfp.client}\nRFP: {rfp.title}\nSection: {section.title}\n\n"
        f"Truncated draft (complete it):\n{body[-12000:]}"
    )
    try:
        raw, _ = await llm.chat_json(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            max_tokens=4096,
            temperature=0.15,
        )
        content = str((raw or {}).get("content") or "").strip()
        if content and not looks_truncated_for_fulfill(content):
            return content
    except Exception:  # noqa: BLE001
        return None
    return None


async def repair_truncated_manuscript_sections(
    *,
    draft: ProposalDraft,
    rfp: RfpRecord,
    skip_section_ids: set[str],
    use_llm: bool,
) -> tuple[ProposalDraft, list[str]]:
    logs: list[str] = []
    sections = list(draft.sections)
    changed = False

    for idx, section in enumerate(sections):
        if section.id in skip_section_ids or fulfill_scan_preserves_section(section):
            continue
        body = section.content or ""
        if not looks_truncated_for_fulfill(body):
            continue

        template = _closing_template_for_section(section)
        if template:
            sections[idx] = section.model_copy(update={"content": template, "status": "generated"})
            logs.append(f"Truncation repair: restored complete closing template for “{section.title}”.")
            changed = True
            continue

        if use_llm:
            completed = await _llm_complete_truncated_section(section=section, rfp=rfp)
            if completed and completed != body:
                sections[idx] = section.model_copy(
                    update={"content": completed, "status": "generated"}
                )
                logs.append(f"Truncation repair: completed cut-off section “{section.title}”.")
                changed = True

    if not changed:
        return draft, logs
    now = datetime.now(timezone.utc).isoformat()
    return draft.model_copy(update={"sections": sections, "updated_at": now}), logs


# ---------------------------------------------------------------------------
# Task 12: KB-grounded truncation repair for the Scan-RFP button.
#
# repair_truncated_manuscript_sections above only ever runs on mode="full"
# (proposal_generator.py, proposal_fulfill_rfp_gaps.py) — the Scan-RFP button
# always calls mode="verify_scrub_only" (proposal_verify_optional_scrub.
# run_verify_scrub_only_scan), which never repaired truncation at all; it
# only detected and reported it. Worse, repair_truncated_manuscript_sections
# skips fulfill_scan_preserves_section (team bios / case studies) entirely —
# so even the mode="full" path never repairs the exact section kinds a real
# user's live run showed cut off mid-sentence (5 bios + 2 case studies).
#
# This function is deliberately separate rather than a change to
# repair_truncated_manuscript_sections/_llm_complete_truncated_section:
#   - it detects with the same T1 scanner (proposal_t1_validators.
#     scan_truncation_artifacts) that produces the Scan-RFP banner's
#     "N section(s) with truncated content" count, so "repaired" vs "still
#     truncated" is measured on the signal the user actually sees;
#   - it is safe to run on bios/case studies because it can ONLY append a
#     KB-grounded completion after the section's existing verbatim prefix —
#     _shared_word_prefix_ratio rejects anything that reads as a rewrite
#     rather than a completion, so it cannot silently replace bio/case-study
#     facts the way a full LLM rewrite could;
#   - it grounds the completion on retrieved KB evidence (zero extra LLM
#     calls — retrieve_for_section is a Supermemory search) and instructs the
#     model to drop a narrow [VERIFY: field] instead of inventing a fact the
#     evidence does not contain, per the same never-invent contract every
#     other Scan-RFP drafting path already uses (see
#     proposal_rfp_compliance._draft_one_added_section).
#
# LLM budget: exactly one llm.chat_json call per truncated section (routed
# via node_name="scan_truncation_kb_repair" — see llm_routing.py). No query-
# planning call: retrieve_for_section's own fallback query (built from the
# section title + a tail slice of its existing content) is used instead of
# spending an LLM call planning queries for content that already exists.
# Never raises: any per-section failure leaves that section's content
# untouched, and the caller's own post-repair T1 rescan reports it as still
# truncated.
# ---------------------------------------------------------------------------


def _shared_word_prefix_ratio(original: str, updated: str) -> float:
    """Fraction of `original`'s words that `updated` still starts with, in order.

    Used to tell a genuine completion (only the cut-off tail changes) apart
    from a wholesale rewrite (which could silently swap out bio/case-study
    facts the KB never confirmed).
    """
    orig_words = original.split()
    if not orig_words:
        return 0.0
    upd_words = updated.split()
    n = min(len(orig_words), len(upd_words))
    i = 0
    while i < n and orig_words[i] == upd_words[i]:
        i += 1
    return i / len(orig_words)


# A completion must preserve at least this much of the original section,
# in order, before its first word-level divergence. High enough to reject a
# rewrite; low enough to tolerate the model tidying the last few words right
# at the cut-off point (e.g. re-closing a clause it is completing).
_MIN_PREFIX_RATIO = 0.8


async def _complete_one_truncated_section_from_kb(
    *,
    section: ProposalSection,
    rfp: RfpRecord,
    rfp_context: str,
) -> ProposalSection | None:
    """KB-grounded completion of one section's cut-off tail. None = leave as-is."""
    body = section.content or ""
    if not body.strip():
        return None

    from app.services.proposal_intelligence.jit_retrieval import retrieve_for_section
    from app.services.proposal_intelligence.schemas import RetrievalEntry
    from app.services.proposal_section_editor import _format_evidence

    tail = body[-600:]
    try:
        entry = RetrievalEntry(
            sectionId=section.id,
            requiredAssets=[section.title or section.id],
            queries=[f"{rfp.client} {section.title} {tail}".strip()[:220]],
        )
        evidence = await retrieve_for_section(entry, rfp_client=rfp.client)
    except Exception:
        logger.warning(
            "truncation-repair:kb — retrieval raised for %s", section.id, exc_info=True
        )
        evidence = []

    evidence_block = _format_evidence(evidence) if evidence else "(no relevant KB evidence found)"

    system = (
        "You repair ONE proposal section that was cut off mid-sentence, mid-list, or "
        "mid-table by an earlier generation pass. You are completing it, not rewriting "
        "it.\n"
        "RULES:\n"
        "1. Reproduce the section VERBATIM up to the point it was cut off. Do not "
        "paraphrase, reorder, shorten, or otherwise touch any sentence that already "
        "reads as complete.\n"
        "2. Finish ONLY the trailing cut-off sentence, clause, list item, or table row.\n"
        "3. Use a fact from the KB EVIDENCE below to complete it ONLY if the evidence "
        "actually contains that fact.\n"
        "4. If the missing fact is not in the KB evidence and not already stated "
        "earlier in the section, insert a narrow [VERIFY: short field name] in its "
        "place and still close the sentence properly — never invent a name, number, "
        "date, certification, or client.\n"
        "5. Do not add new paragraphs, sections, bullet points, or case studies beyond "
        "what is needed to close the existing cut-off.\n"
        '6. Return JSON only: {"content": "full corrected section markdown"}'
    )
    user = (
        f"Client: {rfp.client}\nRFP: {rfp.title}\nSection: {section.title}\n\n"
        f"KB evidence:\n{evidence_block}\n\n"
        f"RFP excerpt:\n{rfp_context[:2000]}\n\n"
        f"Truncated section (complete it):\n{body}"
    )
    try:
        raw, provider = await llm.chat_json(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            max_tokens=4096,
            temperature=0.1,
            node_name="scan_truncation_kb_repair",
        )
    except Exception:
        logger.warning(
            "truncation-repair:kb — completion call raised for %s", section.id, exc_info=True
        )
        return None

    if provider == "failed":
        return None

    content = str((raw or {}).get("content") or "").strip()
    if not content:
        return None

    if _shared_word_prefix_ratio(body.rstrip(), content) < _MIN_PREFIX_RATIO:
        logger.warning(
            "truncation-repair:kb — rejected completion for %s: reads like a rewrite, "
            "not a completion",
            section.id,
        )
        return None

    if looks_truncated_for_fulfill(content):
        return None

    return section.model_copy(update={"content": content, "status": "generated"})


async def repair_truncated_sections_from_kb(
    *,
    draft: ProposalDraft,
    rfp: RfpRecord,
    rfp_context: str,
) -> tuple[ProposalDraft, list[str], list[str], list[str]]:
    """Complete every section the T1 truncation scanner flags, grounded on the KB.

    Returns (updated_draft, repaired_section_ids, still_truncated_section_ids, logs).
    `still_truncated_section_ids` is exactly the set this pass could not fix
    (LLM not configured, retrieval/LLM failure, or the guard above rejected
    the result) — the caller's final T1 rescan is the source of truth for
    what ships still-truncated, but this list lets the caller log why.
    Never raises.
    """
    from app.services.proposal_t1_validators import scan_truncation_artifacts

    findings = scan_truncation_artifacts(draft)
    truncated_ids: list[str] = []
    for finding in findings:
        sid = finding.get("section_id")
        if sid and sid not in truncated_ids:
            truncated_ids.append(sid)

    if not truncated_ids:
        return draft, [], [], []

    if not llm.is_configured():
        return (
            draft,
            [],
            truncated_ids,
            [
                "truncation-repair:kb — LLM not configured, left "
                f"{len(truncated_ids)} truncated section(s) unchanged."
            ],
        )

    sections = list(draft.sections)
    by_id = {s.id: i for i, s in enumerate(sections)}
    repaired_ids: list[str] = []
    still_truncated_ids: list[str] = []
    logs: list[str] = []
    changed = False

    for sid in truncated_ids:
        idx = by_id.get(sid)
        if idx is None:
            still_truncated_ids.append(sid)
            continue
        section = sections[idx]
        try:
            repaired = await _complete_one_truncated_section_from_kb(
                section=section, rfp=rfp, rfp_context=rfp_context
            )
        except Exception:
            logger.exception(
                "truncation-repair:kb — unexpected failure for %s; leaving as-is", sid
            )
            repaired = None

        if repaired is None:
            still_truncated_ids.append(sid)
            logs.append(
                f"truncation-repair:kb — {sid}: could not complete from KB evidence, "
                "left truncated for review."
            )
            continue

        sections[idx] = repaired
        repaired_ids.append(sid)
        changed = True
        logs.append(f"truncation-repair:kb — {sid}: completed cut-off content from KB evidence.")

    if not changed:
        return draft, [], still_truncated_ids, logs
    now = datetime.now(timezone.utc).isoformat()
    updated_draft = draft.model_copy(update={"sections": sections, "updated_at": now})
    return updated_draft, repaired_ids, still_truncated_ids, logs
