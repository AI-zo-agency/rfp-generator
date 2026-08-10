"""LLM-driven multi-section manuscript fixes from chat.
The model reads the user query + full proposal (+ recent chat), decides what to
change, and we apply that plan. No keyword/regex routing for intent, budget,
KB hunts, or add-sections.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Literal

from app.models.proposal import ProposalDraft, ProposalResearchCache
from app.models.rfp import RfpRecord
from app.services import llm
from app.services.llm import LlmError
from app.services.proposal_chat_structure import (
    StructureAddition,
    StructurePlan,
    _proposal_manuscript_for_chat,
    apply_chat_structure_plan,
)

logger = logging.getLogger(__name__)

ChatIntent = Literal["advisory", "single_edit", "multi_patch", "structure", "none"]


def _format_recent_chat(
    conversation_history: list[dict[str, str]] | None,
    *,
    max_chars: int = 8_000,
) -> str:
    if not conversation_history:
        return "(no prior chat)"
    lines: list[str] = []
    for turn in conversation_history[-12:]:
        role = str(turn.get("role") or "").strip().upper() or "USER"
        content = str(turn.get("content") or "").strip()
        if not content:
            continue
        lines.append(f"{role}: {content[:1200]}")
    blob = "\n".join(lines).strip()
    if len(blob) > max_chars:
        blob = blob[-max_chars:]
    return blob or "(no prior chat)"


async def _classify_chat_edit_intent_once(
    *,
    user_message: str,
    draft: ProposalDraft,
    focus_section_id: str | None = None,
    rfp_title: str = "",
    rfp_client: str = "",
    conversation_history: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """One classification attempt. Callers should use classify_chat_edit_intent."""
    outline = "\n".join(
        f"- {s.id}: {s.title}" for s in draft.sections[:40] if s.id and s.title
    )
    recent = _format_recent_chat(conversation_history, max_chars=4_000)
    try:
        raw, _ = await llm.chat_json(
            [
                {
                    "role": "system",
                    "content": (
                        "Classify the user's chat message for a proposal editor.\n\n"
                        "Read the message and recent chat. Understand intent — do not "
                        "match keywords.\n"
                        "Classify ONLY the user's ask. Ignore any Evidence policy / "
                        "prompt scaffolding if it appears in the message.\n\n"
                        '- "advisory" — user asks what is wrong / for a review / analysis / '
                        "gaps / missing items / compliance / whole-proposal audit WITHOUT "
                        "asking you to change the draft yet. Reviews of the full proposal "
                        "are ALWAYS advisory (never single_edit on the open tab).\n"
                        '- "single_edit" — user wants ONE named or pinned section changed now.\n'
                        '- "multi_patch" — user wants you to APPLY fixes now across several '
                        "sections (or the whole proposal), including 'apply these fixes', "
                        "'patch-wise', 'rebuild cost and clean leftovers', etc.\n"
                        '- "structure" — user wants to ADD, DELETE, or RENAME sidebar '
                        "sections/tabs (new H2, new bio tab, new case study tab, split content "
                        "into its own section, delete a tab). Any phrasing counts — e.g. "
                        "'add section new name Planning and methodology', 'create a case study "
                        "for Bend', 'add another team bio', 'remove section 2.1'.\n"
                        '- "none" — unrelated / cannot act.\n'
                        "CRITICAL: add/create/delete sidebar section/tab/H2/bio/case study → "
                        "structure (never single_edit on the open tab).\n"
                        "CRITICAL: revise/patch/fix/improve THIS or THAT section/tab/part/"
                        "paragraph → ALWAYS single_edit (never multi_patch). multi_patch ONLY "
                        "when they clearly ask to change multiple sections or the whole "
                        "proposal (across the proposal / every section / apply these fixes).\n"
                        "If the user lists problems and says to apply/fix/patch them across "
                        "the proposal → multi_patch. Never single_edit for that.\n"
                        "If the user pastes a multi-item CONTENT ISSUES / content-risk audit "
                        "(references incomplete, unsubstantiated claims, fabricated tagline, "
                        "exec summary criteria restatement, thin case studies) and asks to "
                        "fix/change/solve/address it → multi_patch (never advisory-only).\n"
                        "Never choose single_edit merely because a tab is open/focused when "
                        "the ask is about the whole proposal, missing sections, or gaps.\n"
                        "For multi_patch and whole-proposal advisory, primarySectionId may "
                        "be null.\n\n"
                        "Return JSON:\n"
                        '{"intent":"advisory|single_edit|multi_patch|structure|none",'
                        '"primarySectionId":"id or null","reason":"short"}'
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"RFP: {rfp_title} — {rfp_client}\n"
                        f"Pinned/focus section: {focus_section_id or '(none)'}\n"
                        f"Outline:\n{outline}\n\n"
                        f"Recent chat:\n{recent}\n\n"
                        f"User message:\n{user_message.strip()}"
                    ),
                },
            ],
            max_tokens=512,
            temperature=0.0,
            tier="light",
            node_name="chat_manuscript_intent",
        )
        if not isinstance(raw, dict):
            return {"intent": "none", "reason": "bad classifier payload"}
        intent = str(raw.get("intent") or "none").strip()
        if intent not in {
            "advisory",
            "single_edit",
            "multi_patch",
            "structure",
            "none",
        }:
            intent = "none"
        return {
            "intent": intent,
            "primarySectionId": raw.get("primarySectionId"),
            "reason": str(raw.get("reason") or "")[:300],
        }
    except LlmError as exc:
        logger.warning("Chat intent classify failed: %s", exc)
        # `degraded` separates "the model decided none" from "the classifier could
        # not run". Callers previously saw plain "none" for both and fell back to
        # the keyword gate, whose safe default is advisory — so a provider
        # rate-limit silently answered every edit request with an essay.
        return {"intent": "none", "degraded": True, "reason": str(exc)[:200]}


#: Extra attempts before reporting degraded routing. Bounded at one so a chat turn
#: does not stall behind repeated classifier calls.
_INTENT_CLASSIFY_RETRIES = 1
_INTENT_CLASSIFY_BACKOFF_SECONDS = 1.5


async def classify_chat_edit_intent(
    *,
    user_message: str,
    draft: ProposalDraft,
    focus_section_id: str | None = None,
    rfp_title: str = "",
    rfp_client: str = "",
    conversation_history: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Classify a chat turn, retrying once when the classifier cannot run.

    Returns ``degraded: True`` when every attempt failed. The caller must not
    treat that as a confident "none" — routing then falls back to the keyword
    gate, whose safe default is advisory, which is indistinguishable from the
    assistant deciding not to edit.
    """
    attempt = 0
    while True:
        result = await _classify_chat_edit_intent_once(
            user_message=user_message,
            draft=draft,
            focus_section_id=focus_section_id,
            rfp_title=rfp_title,
            rfp_client=rfp_client,
            conversation_history=conversation_history,
        )
        if not result.get("degraded"):
            return result
        if attempt >= _INTENT_CLASSIFY_RETRIES:
            logger.warning(
                "Chat intent classifier degraded after %d attempt(s) — "
                "routing falls back to keyword gate",
                attempt + 1,
            )
            return result
        attempt += 1
        await asyncio.sleep(_INTENT_CLASSIFY_BACKOFF_SECONDS * (2 ** (attempt - 1)))


async def _plan_manuscript_fixes(
    *,
    draft: ProposalDraft,
    user_message: str,
    rfp_title: str,
    rfp_client: str,
    rfp_context: str,
    conversation_history: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    manuscript = _proposal_manuscript_for_chat(draft)
    recent = _format_recent_chat(conversation_history, max_chars=10_000)
    try:
        raw, _ = await llm.chat_json(
            [
                {
                    "role": "system",
                    "content": (
                        "You read the user's APPLY request, recent chat, and the FULL proposal.\n"
                        "Understand what they want changed. Build a PATCH PLAN for THIS RFP only.\n"
                        "Do NOT use keyword matching — infer from meaning.\n\n"
                        "Rules:\n"
                        "- Put each distinct change into fixes[] with the correct sectionId "
                        "from the outline and a surgical brief (exactly what to change).\n"
                        "- Prefer patching EXISTING sections. Technical Ability / Past "
                        "Performance / Cost usually already cover understanding, references, "
                        "compliance, and timeline.\n"
                        "- addSections[] ONLY if the user clearly wants a new tab/section "
                        "added. If they say no new sections, outline already covers it, or "
                        "delete unnecessary sections → addSections must be [].\n"
                        "- runBudgetAgent defaults to FALSE. Set true ONLY when the user "
                        "clearly asks to rebuild or regenerate the cost/fee/budget/pricing "
                        "TABLE or Cost of base proposal (new/changed line items). "
                        "If they only want summary paragraphs recalculated from the "
                        "existing fee table (agency fee vs pass-through vs total must be "
                        "distinct numbers), runBudgetAgent MUST be false — do not touch "
                        "pricing lines. Case studies, past performance, bios, flags, "
                        "leftovers, references → runBudgetAgent MUST be false.\n"
                        "- needsKbExcerpts=true on a fix ONLY when that brief needs new "
                        "case-study / reference material from the zö knowledge base. "
                        "Cleaning leftover flags or wrong-RFP notes does NOT need KB.\n"
                        "- Never invent dollars, phones, emails, or contacts. Use [VERIFY: …] "
                        "when unknown.\n"
                        "- Budget briefs (when runBudgetAgent): RFP thresholds + guide tiers "
                        "only — no invented client-specific rates.\n"
                        "- Wrong-RFP leftovers (other universities, GSU, health-coalition "
                        "flags like Recovery Network of Oregon on a non-health RFP): strip "
                        "or rewrite for THIS client only.\n"
                        "- Thin higher-ed references: if named schools have no delivered "
                        "outcomes, either substantiate from KB (needsKbExcerpts) or mark "
                        "[VERIFY] / remove unverified claims — do not leave empty name-drops.\n"
                        "- Max 10 fixes + 4 addSections.\n"
                        "- If nothing should change, return empty arrays and runBudgetAgent "
                        "false.\n\n"
                        "Return JSON:\n"
                        "{"
                        '"fixes":[{"sectionId":"...","brief":"...","needsKbExcerpts":false}],'
                        '"addSections":[{"title":"...","brief":"..."}],'
                        '"runBudgetAgent":false,'
                        '"summary":"short user-facing recap"'
                        "}"
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"RFP: {rfp_title} — {rfp_client}\n\n"
                        f"{manuscript}\n\n"
                        f"RFP excerpt:\n{rfp_context[:6000]}\n\n"
                        f"Recent chat (issues previously called out):\n"
                        f"{recent}\n\n"
                        f"User APPLY request:\n{user_message.strip()}"
                    ),
                },
            ],
            max_tokens=3072,
            temperature=0.1,
            tier="light",
            node_name="chat_manuscript_fix_plan",
        )
        return raw if isinstance(raw, dict) else {}
    except LlmError as exc:
        logger.warning("Manuscript fix plan failed: %s", exc)
        return {}


def _plan_wants_budget_agent(plan: dict[str, Any]) -> bool:
    """Honor the planner's runBudgetAgent flag (LLM-decided, not keyword-gated)."""
    val = plan.get("runBudgetAgent")
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.strip().casefold() in {"true", "1", "yes"}
    return False


def _fix_wants_kb(fix: dict[str, Any]) -> bool:
    val = fix.get("needsKbExcerpts")
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.strip().casefold() in {"true", "1", "yes"}
    return False


def _is_budget_section_id(draft: ProposalDraft, section_id: str) -> bool:
    from app.services.proposal_budget_content import budget_section_score

    sec = next((s for s in draft.sections if s.id == section_id), None)
    if not sec:
        return False
    return budget_section_score(sec.title or "") > 0


_CHAT_PATCH_SYSTEM = """You apply ONE surgical patch to ONE proposal section.

Rules:
1. Change ONLY what the Task brief requires. Preserve every other sentence, heading, list, and table.
2. Do NOT rewrite the whole section. Do NOT add intros, marketing fluff, or new case studies unless the Task says to.
3. Do NOT invent phones, emails, dollar amounts, contacts, or percent-time / FTE figures.
4. When the Task says to cross-check against KB and flag unsourced figures: keep ONLY values
   supported by the KB excerpts provided; replace unsourced numbers/percentages with a precise
   [VERIFY: …] tag (e.g. [VERIFY: percent time]). That IS the required change — do not leave
   invented percentages in place.
5. Wrong-RFP leftovers (other universities/agencies, health-coalition flags like Recovery Network of Oregon on a non-health college RFP): remove or rewrite for THIS RFP client only.
6. Never invent fee totals here — full budget rebuilds are handled by the Stage 3 budget agent when the plan says so.
7. Return ONLY JSON: {"content":"full updated section text"}"""


async def _run_full_budget_agent(
    *,
    rfp_id: str,
    draft: ProposalDraft,
    research: ProposalResearchCache | None,
) -> tuple[ProposalDraft, ProposalResearchCache | None, bool, str]:
    """Thorough Stage 3.5 budget rebuild — not a surgical prose patch."""
    try:
        from app.services.proposal_generator import run_phase3_5_budget

        new_draft, new_research, budget = await run_phase3_5_budget(rfp_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Full budget agent failed for %s: %s", rfp_id, exc)
        return draft, research, False, f"budget agent failed: {exc}"

    revenue = budget.agency_revenue_estimate
    n_lines = len(budget.line_items or [])
    tier = budget.pricing_tier or "?"
    detail = (
        f"Stage 3.5 budget agent: tier={tier}, {n_lines} lines, "
        f"total={revenue}"
    )
    return new_draft, new_research, True, detail


async def _apply_surgical_section_fix(
    *,
    rfp_id: str,
    section_id: str,
    brief: str,
    rfp: RfpRecord,
    draft: ProposalDraft,
    research: ProposalResearchCache | None,
    rfp_context: str = "",
    needs_kb_excerpts: bool = False,
    allow_budget_agent: bool = False,
) -> tuple[ProposalDraft, ProposalResearchCache | None, bool, str]:
    """Patch ONE section for the brief only.

    Budget rebuild runs only when the planner set allow_budget_agent / runBudgetAgent.
    """
    from datetime import datetime, timezone

    from app.services.proposal_consistency import patch_improves_section
    from app.services.proposal_repository import asave_proposal_draft

    existing = next((s for s in draft.sections if s.id == section_id), None)
    if not existing:
        return draft, research, False, "missing section"

    # Full fee-table rebuild only when the plan asked for Stage 3.5.
    if allow_budget_agent and _is_budget_section_id(draft, section_id):
        return await _run_full_budget_agent(
            rfp_id=rfp_id, draft=draft, research=research
        )

    before = existing
    prior = before.content or ""
    if not prior.strip():
        return draft, research, False, "empty section"

    extra_blocks: list[str] = []
    detail_bits: list[str] = ["chat-surgical"]

    if needs_kb_excerpts:
        detail_bits.append("kb-refs")
        try:
            from app.services import proposal_knowledge_base_tools

            # Query from the task brief + section — not a hardcoded case-study hunt.
            kb_query = (
                f"zö agency {brief.strip()[:220]} {before.title} "
                f"team staffing percent time allocation FTE roles bios"
            ).strip()
            text, _ = await proposal_knowledge_base_tools.search_knowledge_base(
                kb_query,
                limit=6,
                max_chars=10_000,
                rfp_client=rfp.client,
                rfp_sector=rfp.sector,
                rfp_title=rfp.title,
            )
            if text and not text.startswith("("):
                extra_blocks.append(
                    f"KB excerpts (use only facts present here; otherwise [VERIFY]):\n"
                    f"{text[:8000]}"
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("KB ref fetch for chat patch skipped: %s", exc)
    else:
        detail_bits.append("no-kb-hunt")

    user_content = (
        f"RFP client: {rfp.client}\n"
        f"Section: {before.title} ({section_id})\n\n"
        f"Task (do ONLY this):\n{brief.strip()}\n\n"
        f"Current section:\n{prior[:12_000]}\n"
    )
    if extra_blocks:
        user_content += "\n\n" + "\n\n".join(extra_blocks)

    try:
        raw, provider = await llm.chat_json(
            [
                {"role": "system", "content": _CHAT_PATCH_SYSTEM},
                {"role": "user", "content": user_content},
            ],
            max_tokens=4096,
            temperature=0.15,
            tier="heavy",
            node_name="chat_manuscript_surgical_patch",
        )
    except LlmError as exc:
        logger.warning("Chat surgical patch LLM failed for %s: %s", section_id, exc)
        return draft, research, False, f"llm failed: {exc}"

    content = str((raw or {}).get("content") or "").strip()
    if not content or content == prior.strip():
        return draft, research, False, "no content change"

    after = before.model_copy(update={"content": content, "status": "generated"})
    typed_budget = research.budget if research else None

    # Removing wrong-RFP / Sonja FLAGS is always an improvement even if the
    # strict scorer sees "no quality gain" (shorter section, fewer tags).
    removed_flag = bool(
        re.search(r"\[FLAG FOR SONJA:", prior, re.I)
        and not re.search(r"\[FLAG FOR SONJA:", content, re.I)
    )
    from app.services.proposal_section_quality import is_integrity_verify_flagging

    integrity_flag = is_integrity_verify_flagging(before, after)
    if (
        not removed_flag
        and not integrity_flag
        and not patch_improves_section(
            before, after, rfp=rfp, budget=typed_budget
        )
    ):
        return draft, research, False, "patch rejected (no improvement)"

    updated_draft = draft.model_copy(
        update={
            "sections": [after if s.id == section_id else s for s in draft.sections],
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "provider": provider,
        }
    )
    await asave_proposal_draft(updated_draft)
    return (
        updated_draft,
        research,
        True,
        f"{'+'.join(detail_bits)} via {provider}",
    )


async def run_manuscript_wide_fixes(
    *,
    rfp_id: str,
    draft: ProposalDraft,
    rfp: RfpRecord,
    rfp_context: str,
    research: ProposalResearchCache | None,
    user_message: str,
    conversation_history: list[dict[str, str]] | None = None,
    max_fixes: int = 10,
) -> tuple[ProposalDraft, ProposalResearchCache | None, str, bool]:
    """Apply LLM-planned surgical patches across sections."""
    plan = await _plan_manuscript_fixes(
        draft=draft,
        user_message=user_message,
        rfp_title=rfp.title,
        rfp_client=rfp.client,
        rfp_context=rfp_context,
        conversation_history=conversation_history,
    )
    fixes = [f for f in (plan.get("fixes") or []) if isinstance(f, dict)]
    adds = [a for a in (plan.get("addSections") or []) if isinstance(a, dict)]
    summary = str(plan.get("summary") or "").strip()
    run_budget = _plan_wants_budget_agent(plan)
    # Hard override: summary-only reconcile never regenerates the fee table.
    try:
        from app.services.proposal_budget_playbook import (
            user_asks_budget_summary_reconcile,
        )

        if user_asks_budget_summary_reconcile(user_message):
            run_budget = False
    except Exception:  # noqa: BLE001
        pass

    logger.info(
        "Manuscript multi-patch plan for %s: %d fix(es), %d add(s), "
        "runBudgetAgent=%s ask=%r",
        rfp_id,
        len(fixes),
        len(adds),
        run_budget,
        user_message[:100],
    )

    if not fixes and not adds and not run_budget:
        reply = (
            summary
            or "I understood the apply-fixes request, but could not map concrete "
            "section patches from the current draft and recent chat. "
            "Reply with the specific sections/issues to patch."
        )
        return draft, research, reply, False

    logs: list[str] = []
    changed = False
    section_ids = {s.id for s in draft.sections}
    seen_fix: set[str] = set()
    budget_agent_ran = False

    # Stage 3.5 only when the planner understood a budget rebuild is needed.
    if run_budget:
        draft, research, improved, detail = await _run_full_budget_agent(
            rfp_id=rfp_id, draft=draft, research=research
        )
        budget_agent_ran = True
        logs.append(
            f"{'Rebuilt' if improved else 'Skipped'} **Budget (Stage 3.5 agent)**: {detail[:160]}"
        )
        if improved:
            changed = True
        section_ids = {s.id for s in draft.sections}

    for raw in fixes[:max_fixes]:
        sid = str(raw.get("sectionId") or "").strip()
        brief = str(raw.get("brief") or "").strip()
        if not sid or sid not in section_ids or not brief:
            continue
        if sid in seen_fix:
            continue
        # Budget table already rebuilt — skip duplicate cost-section fixes.
        if budget_agent_ran and _is_budget_section_id(draft, sid):
            seen_fix.add(sid)
            continue
        merged = " | ".join(
            str(f.get("brief") or "").strip()
            for f in fixes
            if str(f.get("sectionId") or "").strip() == sid
            and str(f.get("brief") or "").strip()
        )[:1200]
        seen_fix.add(sid)
        draft, research, improved, detail = await _apply_surgical_section_fix(
            rfp_id=rfp_id,
            section_id=sid,
            brief=merged or brief,
            rfp=rfp,
            draft=draft,
            research=research,
            rfp_context=rfp_context,
            needs_kb_excerpts=_fix_wants_kb(raw),
            allow_budget_agent=False,
        )
        section_ids = {s.id for s in draft.sections}
        title = next((s.title for s in draft.sections if s.id == sid), sid)
        logs.append(
            f"{'Patched' if improved else 'Skipped'} **{title}**: {detail[:140]}"
        )
        if improved:
            changed = True

    if adds:
        additions = [
            StructureAddition(
                title=str(a.get("title") or "New section").strip(),
                kind="custom",
                draftHint=str(a.get("brief") or "").strip() or None,
            )
            for a in adds[:4]
            if str(a.get("title") or "").strip()
        ]
        if additions:
            struct = StructurePlan(action="add_sections", additions=additions)
            draft, _focus, msg = await apply_chat_structure_plan(
                draft=draft,
                plan=struct,
                rfp_client=rfp.client,
                rfp_sector=rfp.sector or "",
                rfp_context=rfp_context or "",
            )
            logs.append(msg or f"Added {len(additions)} section(s)")
            changed = True

    if not changed:
        reply = (
            summary
            or "I planned the patch-wise fixes, but none of the section patches "
            "landed. Try naming the sections again, or pin one and ask for a "
            "single surgical edit."
        )
        if logs:
            reply += "\n\n**Attempted:**\n" + "\n".join(f"- {line}" for line in logs[:12])
        return draft, research, reply, False

    reply_parts = [summary] if summary else ["Applied patch-wise fixes across the proposal."]
    reply_parts.append("**Patches applied:**")
    reply_parts.extend(f"- {line}" for line in logs[:12])
    return draft, research, "\n".join(reply_parts), True
