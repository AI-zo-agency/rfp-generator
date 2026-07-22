"""Chat-driven proposal structure changes: add / delete sections, or ask when unclear."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.models.proposal import ProposalDraft, ProposalSection
from app.services import llm
from app.services.llm import LlmError
from app.services.proposal_common import ProposalError

logger = logging.getLogger(__name__)

StructureAction = Literal["edit", "add_sections", "delete_sections", "clarify"]


class StructureAddition(BaseModel):
    title: str = ""
    kind: Literal["bio", "case_study", "custom", "rfp"] = "custom"
    member_name: str | None = Field(default=None, alias="memberName")
    case_study_name: str | None = Field(default=None, alias="caseStudyName")
    insert_after_section_id: str | None = Field(
        default=None, alias="insertAfterSectionId"
    )
    draft_hint: str | None = Field(default=None, alias="draftHint")

    model_config = {"populate_by_name": True}


class StructureDeletion(BaseModel):
    section_id: str | None = Field(default=None, alias="sectionId")
    title: str | None = None

    model_config = {"populate_by_name": True}


class StructurePlan(BaseModel):
    action: StructureAction = "edit"
    clarify_question: str | None = Field(default=None, alias="clarifyQuestion")
    edit_section_id: str | None = Field(default=None, alias="editSectionId")
    additions: list[StructureAddition] = Field(default_factory=list)
    deletions: list[StructureDeletion] = Field(default_factory=list)
    assistant_note: str | None = Field(default=None, alias="assistantNote")

    model_config = {"populate_by_name": True}


STRUCTURE_PLAN_PROMPT = """You plan structural changes to a zö agency proposal outline (sidebar sections).

The user may want to EDIT one section, ADD new sidebar sections, DELETE sections, or you may need to CLARIFY.

Return ONLY JSON:
{
  "action": "edit" | "add_sections" | "delete_sections" | "clarify",
  "clarifyQuestion": "ask the user only when action is clarify",
  "editSectionId": "section id to rewrite when action is edit (or null)",
  "additions": [
    {
      "title": "2.2 — Name" or descriptive title,
      "kind": "bio" | "case_study" | "custom" | "rfp",
      "memberName": "Full Name for bios only",
      "caseStudyName": "Client/project name for case studies only",
      "insertAfterSectionId": "existing section id to insert after, or null",
      "draftHint": "optional short note of what to write"
    }
  ],
  "deletions": [{"sectionId": "...", "title": "..."}],
  "assistantNote": "one short sentence of what you will do"
}

Rules:
1. Prefer "edit" when they want to change prose in an existing tab (improve, rewrite, fill VERIFY).
2. Use "add_sections" when they want NEW sidebar items (more bios, more case studies, new form, new custom tab).
   - Team bios → kind "bio", title like "2.2 — First Last"; memberName only if user/RFP named them.
   - Our Work / case studies → kind "case_study", caseStudyName required.
   - Other new tabs → kind "custom" or "rfp".
3. Use "delete_sections" when they clearly ask to remove/delete a section.
4. Use "clarify" when intent is ambiguous (e.g. "add more people" with no count,
   "fix section 2" could mean rewrite or add, delete without naming which).
   Ask one concise question — do not guess destructive deletes.
5. Never stuff a second bio into an existing bio section — that requires add_sections.
6. Do not invent people or VERIFY placeholders. If count is clear but names unknown,
   use add_sections with kind "bio" and memberName null — backend picks real roster
   people and drafts full bios in one pass.
7. insertAfterSectionId should be the last related sibling when possible (last bio / last case study).
8. Prefer concrete names only when the user or RFP clearly names them.
9. "Instead of X add/use Y" / "replace X with Y" for ANY section type (bio, case study,
   form, custom) → include BOTH deletions for X and additions for Y. Infer kind from
   the section being replaced (bio tab → bio + memberName; Our Work → case_study;
   otherwise custom/rfp). NEVER action=edit that rewrites Y's content under X's title.
10. Ignore the focus section when the user clearly names a different sidebar title/person —
    structure changes are proposal-wide.
"""


def _slug(text: str) -> str:
    raw = re.sub(r"[^a-z0-9]+", "-", (text or "").casefold()).strip("-")
    return raw[:40] or "section"


def _bio_sections(sections: list[ProposalSection]) -> list[ProposalSection]:
    return [
        s
        for s in sections
        if s.id.startswith("section-2-bio-")
        and s.id != "section-2-bio-placeholder"
    ]


def _case_study_sections(sections: list[ProposalSection]) -> list[ProposalSection]:
    return [
        s
        for s in sections
        if s.id.startswith("section-3-work-")
        and s.id != "section-3-work-placeholder"
    ]


def renumber_dynamic_group_titles(sections: list[ProposalSection]) -> list[ProposalSection]:
    """Keep 2.N / 3.N titles aligned with sidebar order after add/delete."""
    out: list[ProposalSection] = []
    bio_i = 0
    case_i = 0
    for section in sections:
        if section.id.startswith("section-2-bio-") and section.id != "section-2-bio-placeholder":
            bio_i += 1
            name = section.title.split("—", 1)[-1].strip() if "—" in section.title else section.title
            name = re.sub(r"^2\.\d+\s*[—\-–:]\s*", "", name).strip() or name
            out.append(section.model_copy(update={"title": f"2.{bio_i} — {name}"}))
            continue
        if section.id.startswith("section-3-work-") and section.id != "section-3-work-placeholder":
            case_i += 1
            name = section.title.split("—", 1)[-1].strip() if "—" in section.title else section.title
            name = re.sub(r"^3\.\d+\s*[—\-–:]\s*", "", name).strip() or name
            out.append(section.model_copy(update={"title": f"3.{case_i} — {name}"}))
            continue
        out.append(section)
    return out


def _insert_after(
    sections: list[ProposalSection],
    new_section: ProposalSection,
    after_id: str | None,
) -> list[ProposalSection]:
    if after_id:
        for i, section in enumerate(sections):
            if section.id == after_id:
                return [*sections[: i + 1], new_section, *sections[i + 1 :]]
    # Default: after last bio / case study / at end
    if new_section.id.startswith("section-2-bio-"):
        bios = _bio_sections(sections)
        if bios:
            return _insert_after(sections, new_section, bios[-1].id)
    if new_section.id.startswith("section-3-work-"):
        cases = _case_study_sections(sections)
        if cases:
            return _insert_after(sections, new_section, cases[-1].id)
    return [*sections, new_section]


def _outline_digest(draft: ProposalDraft) -> str:
    lines: list[str] = []
    for section in draft.sections[:60]:
        filled = "filled" if (section.content or "").strip() else "empty"
        lines.append(f"- {section.id} | {section.title} | {filled}")
    return "\n".join(lines)


def _section_label(title: str) -> str:
    """Human label from a sidebar title (strip 2.1 — prefix)."""
    text = (title or "").strip()
    if "—" in text:
        text = text.split("—", 1)[-1].strip()
    elif "–" in text:
        text = text.split("–", 1)[-1].strip()
    return re.sub(r"^\d+\.\d+\s*[—\-–:]\s*", "", text).strip()


def _clean_section_label(raw: str) -> str:
    name = (raw or "").strip()
    name = re.sub(
        r"\b(bio|resume|section|tab|the|a|an|case\s*study|case|study)\b",
        " ",
        name,
        flags=re.I,
    )
    name = re.sub(r"\s+", " ", name).strip(" .,;:-")
    return name


def _person_name_from_section_title(title: str) -> str:
    return _section_label(title)


def _clean_person_name(raw: str) -> str:
    return _clean_section_label(raw)


def _infer_addition_kind(section: ProposalSection) -> Literal["bio", "case_study", "custom", "rfp"]:
    sid = section.id or ""
    if sid.startswith("section-2-bio-"):
        return "bio"
    if sid.startswith("section-3-work-"):
        return "case_study"
    if sid.startswith("rfp-") or section.source == "rfp":
        return "rfp"
    return "custom"


def _find_section_by_label(
    sections: list[ProposalSection], label: str
) -> ProposalSection | None:
    needle = _clean_section_label(label).casefold()
    if len(needle) < 3:
        return None
    ranked = sorted(sections, key=lambda s: len(s.title or ""), reverse=True)
    for section in ranked:
        if section.id.endswith("-placeholder"):
            continue
        title = (section.title or "").casefold()
        label_part = _section_label(section.title).casefold()
        if needle == label_part or needle in label_part or label_part in needle:
            return section
        if needle in title:
            return section
    return None


def _find_bio_by_person_name(
    sections: list[ProposalSection], person: str
) -> ProposalSection | None:
    hit = _find_section_by_label(_bio_sections(sections), person)
    return hit


def _extract_swap_labels_from_text(text: str) -> list[str]:
    """Labels the user might want as the NEW section (names, clients, titles)."""
    stop_first = {
        "section",
        "verify",
        "fill",
        "tags",
        "team",
        "work",
        "key",
        "all",
        "get",
        "add",
        "see",
        "and",
        "the",
        "for",
        "with",
        "from",
        "put",
        "use",
        "want",
        "need",
        "show",
        "make",
        "bring",
        "our",
        "this",
        "that",
        "more",
    }
    stop_last = {
        "bio",
        "resume",
        "section",
        "tags",
        "verify",
        "there",
        "history",
        "accounts",
        "bios",
        "info",
        "information",
        "study",
        "case",
        "form",
        "overview",
    }
    found: list[str] = []

    def _add(first: str, last: str, third: str | None = None) -> None:
        parts = [first, last]
        if third:
            parts.append(third)
        if parts[0].casefold() in stop_first:
            return
        if parts[-1].casefold() in stop_last:
            parts = parts[:-1]
        if len(parts) < 2:
            return
        if any(p.casefold() in stop_first | stop_last for p in parts):
            # drop leading stopwords only
            while parts and parts[0].casefold() in stop_first:
                parts.pop(0)
            while parts and parts[-1].casefold() in stop_last:
                parts.pop()
        if len(parts) < 2:
            return
        label = _clean_section_label(" ".join(p.capitalize() for p in parts))
        if len(label) < 3:
            return
        if label.casefold() not in {n.casefold() for n in found}:
            found.append(label)

    # Explicit: "ron comer bio" (do not allow leading get/add to steal the match)
    for match in re.finditer(
        r"\b([A-Za-z][a-z']+)\s+([A-Za-z][a-z']+)\s+(?:bio|resume)\b",
        text or "",
        re.I,
    ):
        _add(match.group(1), match.group(2))

    # Explicit: "hampton lumber case study" / "deschutes brewery case study"
    for match in re.finditer(
        r"\b([A-Za-z][a-z']+)\s+([A-Za-z][a-z']+)(?:\s+([A-Za-z][a-z']+))?\s+case\s*study\b",
        text or "",
        re.I,
    ):
        _add(match.group(1), match.group(2), match.group(3))

    if found:
        return found

    # Fallback two-word labels when replace/get intent is present
    if re.search(
        r"\b(instead|replace|swap|change|get|put|use|add|verify)\b",
        text or "",
        re.I,
    ):
        for match in re.finditer(
            r"\b([A-Za-z][a-z']+)\s+([A-Za-z][a-z']+)\b",
            text or "",
            re.I,
        ):
            _add(match.group(1), match.group(2))
    return found


def _extract_person_names_from_text(text: str) -> list[str]:
    """Back-compat alias — any swap labels, not bios only."""
    return _extract_swap_labels_from_text(text)


def _heuristic_section_replace_plan(
    user_message: str,
    draft: ProposalDraft,
    *,
    focus_section_id: str | None = None,
) -> StructurePlan | None:
    """Deterministic 'instead of X → Y' for ANY section kind — rename tab, don't rewrite under old title."""
    text = (user_message or "").strip()
    if not text:
        return None

    old_label = ""
    new_label = ""
    patterns = (
        r"instead\s+of\s+(.+?)\s+(?:[,:]?\s*)?(?:add|use|put|include|with)\s+(.+?)\s*$",
        r"replace\s+(.+?)\s+with\s+(.+?)\s*$",
        r"swap\s+(?:out\s+)?(.+?)\s+(?:for|with)\s+(.+?)\s*$",
        r"change\s+(.+?)\s+to\s+(.+?)\s*$",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            old_label = _clean_section_label(match.group(1))
            new_label = _clean_section_label(match.group(2))
            break

    focus = next((s for s in draft.sections if s.id == focus_section_id), None)
    focus_label = _section_label(focus.title) if focus else ""

    if not new_label:
        named = _extract_swap_labels_from_text(text)
        swap_intent = bool(
            re.search(
                r"\b(instead|replace|swap|change|get|put|use|add|verify|there)\b",
                text,
                re.I,
            )
        )
        if swap_intent and named:
            for candidate in named:
                if focus_label and candidate.casefold() == focus_label.casefold():
                    continue
                new_label = candidate
                break
            if new_label and focus and focus_label:
                old_label = focus_label

    if not new_label or len(new_label) < 3:
        return None
    if old_label and old_label.casefold() == new_label.casefold():
        return None

    target: ProposalSection | None = None
    if old_label:
        target = _find_section_by_label(draft.sections, old_label)
    if target is None and focus is not None:
        if focus_label.casefold() != new_label.casefold():
            # Only auto-replace focus when the message clearly asks for a different entity
            if re.search(
                r"\b(instead|replace|swap|change|get|put|use)\b", text, re.I
            ):
                target = focus
                old_label = focus_label or old_label or "current section"

    if target is None:
        return None

    if _section_label(target.title).casefold() == new_label.casefold():
        return None

    kind = _infer_addition_kind(target)
    if kind == "bio":
        add_title = f"2.x — {new_label}"
    elif kind == "case_study":
        add_title = f"3.x — {new_label}"
    else:
        add_title = new_label
    addition = StructureAddition(
        kind=kind,
        title=add_title,
        memberName=new_label if kind == "bio" else None,
        caseStudyName=new_label if kind == "case_study" else None,
        insertAfterSectionId=None,
        draftHint=f"Replace prior section with {new_label}",
    )

    logger.info(
        "Section replace heuristic: %s (%s) → %s [%s]",
        old_label or target.title,
        target.id,
        new_label,
        kind,
    )
    return StructurePlan(
        action="add_sections",
        deletions=[
            StructureDeletion(sectionId=target.id, title=target.title),
        ],
        additions=[addition],
        assistantNote=f"Replacing {_section_label(target.title) or old_label} with {new_label}.",
    )


# Back-compat name used by tests / callers
def _heuristic_bio_replace_plan(
    user_message: str,
    draft: ProposalDraft,
    *,
    focus_section_id: str | None = None,
) -> StructurePlan | None:
    return _heuristic_section_replace_plan(
        user_message, draft, focus_section_id=focus_section_id
    )


async def plan_chat_structure_action(
    *,
    draft: ProposalDraft,
    user_message: str,
    focus_section_id: str,
    rfp_title: str,
    rfp_client: str,
    rfp_context: str,
) -> StructurePlan:
    """Decide edit vs add/delete sections vs ask the user."""
    heuristic = _heuristic_section_replace_plan(
        user_message, draft, focus_section_id=focus_section_id
    )
    if heuristic is not None:
        return heuristic

    focus = next((s for s in draft.sections if s.id == focus_section_id), None)
    prompt = (
        f"RFP: {rfp_title} — {rfp_client}\n\n"
        f"Focus section id: {focus_section_id}\n"
        f"Focus section title: {(focus.title if focus else '')}\n\n"
        f"Current outline:\n{_outline_digest(draft)}\n\n"
        f"RFP context (short):\n{rfp_context[:4000]}\n\n"
        f"User message:\n{user_message.strip()}\n\n"
        "CRITICAL: If the user wants a DIFFERENT sidebar section/person/case study than the "
        "focus tab title, you MUST use add_sections with deletions for the old tab and "
        "additions for the new one. NEVER action=edit that puts new content under the old title."
    )
    try:
        raw, _ = await llm.chat_json(
            [
                {"role": "system", "content": STRUCTURE_PLAN_PROMPT},
                {"role": "user", "content": prompt},
            ],
            max_tokens=1200,
            temperature=0.1,
        )
    except LlmError as exc:
        logger.warning("Structure plan LLM failed: %s — defaulting to edit", exc)
        return StructurePlan(action="edit", editSectionId=focus_section_id)

    try:
        plan = StructurePlan.model_validate(raw)
    except Exception:
        logger.warning("Structure plan invalid JSON shape — defaulting to edit")
        return StructurePlan(action="edit", editSectionId=focus_section_id)

    if plan.action == "clarify" and not (plan.clarify_question or "").strip():
        plan.clarify_question = (
            "Do you want me to **edit the current section**, **add new sidebar sections**, "
            "or **delete** something specific? Tell me names/titles if you can."
        )
    if plan.action == "add_sections" and not plan.additions:
        plan.action = "clarify"
        plan.clarify_question = (
            "I can add new sidebar sections — which titles/people/case studies should I create?"
        )
    if plan.action == "delete_sections" and not plan.deletions:
        plan.action = "clarify"
        plan.clarify_question = (
            "Which section should I delete? Reply with the exact sidebar title (e.g. 2.1 — Sonja Anderson)."
        )
    if plan.action == "edit" and not plan.edit_section_id:
        plan.edit_section_id = focus_section_id
    if plan.action == "edit":
        forced = _heuristic_section_replace_plan(
            user_message, draft, focus_section_id=focus_section_id
        )
        if forced is not None:
            return forced
    return plan


async def _build_bio_section(
    *,
    member_name: str,
    index: int,
    rfp_client: str,
) -> ProposalSection:
    from app.services.proposal_sections_graph import (
        _apply_verified_corrections,
        _extract_member_bio_facts,
        _fetch_member_bio_kb,
        _format_member_bio_content,
        _sanitize_content,
    )

    member = member_name.strip() or f"[VERIFY: team member {index}]"
    safe_id = _slug(member.replace("[VERIFY:", "").replace("]", ""))
    sec_id = f"section-2-bio-{safe_id}"
    title = f"2.{index} — {member}"

    kb_text, bio_sources = await _fetch_member_bio_kb(member)
    if kb_text.strip() and len(kb_text) >= 200:
        extracted = await _extract_member_bio_facts(member, kb_text)
        content = _apply_verified_corrections(
            _sanitize_content(_format_member_bio_content(member, extracted)),
            rfp_client=rfp_client,
        )
    else:
        content = (
            f"### {member}\n\n"
            f"[VERIFY: Bio for {member} — add 04_Bio file or confirm name]\n"
        )

    return ProposalSection(
        id=sec_id,
        title=title,
        wordTarget=500,
        required=True,
        custom=False,
        source="template",
        mode="select",
        content=content,
        status="generated" if content.strip() else "outline",
        designerNote=f"Bio for {member}. From 04_Bio when available.",
        kbRefs=bio_sources[:6] if bio_sources else [],
    )


def _build_stub_section(
    *,
    section_id: str,
    title: str,
    kind: str,
    draft_hint: str | None,
) -> ProposalSection:
    hint = (draft_hint or "").strip()
    if kind == "case_study":
        body = (
            f"### {title}\n\n"
            f"{hint + chr(10) + chr(10) if hint else ''}"
            "Client overview\n\n[VERIFY: Client overview]\n\n"
            "Challenge:\n\n[VERIFY: Challenge]\n\n"
            "Solution / Our Approach:\n\n[VERIFY: Approach]\n\n"
            "Results:\n\n- [VERIFY: Results]\n\n"
            "Why Relevant:\n\n[VERIFY: Why relevant to this RFP]\n"
        )
        mode = "select"
        source = "template"
        word_target = 700
    else:
        body = (
            f"### {title}\n\n"
            f"{hint + chr(10) + chr(10) if hint else ''}"
            "[VERIFY: Draft this section for the RFP]\n"
        )
        mode = "write"
        source = "rfp" if kind == "rfp" else "generated"
        word_target = 600

    return ProposalSection(
        id=section_id,
        title=title,
        wordTarget=word_target,
        required=True,
        custom=kind == "custom",
        source=source,  # type: ignore[arg-type]
        mode=mode,  # type: ignore[arg-type]
        content=body,
        status="outline",
        designerNote=hint or None,
    )


def _resolve_deletion_id(
    draft: ProposalDraft, deletion: StructureDeletion
) -> str | None:
    if deletion.section_id and any(s.id == deletion.section_id for s in draft.sections):
        return deletion.section_id
    title = (deletion.title or "").strip().casefold()
    if not title:
        return None
    for section in draft.sections:
        if (section.title or "").casefold() == title:
            return section.id
        if title and title in (section.title or "").casefold():
            return section.id
    return None


def _existing_bio_names(sections: list[ProposalSection]) -> set[str]:
    names: set[str] = set()
    for section in _bio_sections(sections):
        name = section.title.split("—", 1)[-1].strip() if "—" in section.title else ""
        name = re.sub(r"^2\.\d+\s*[—\-–:]\s*", "", name).strip()
        if name and not name.casefold().startswith("[verify"):
            names.add(name.casefold())
        # Also parse heading from content when present
        m = re.search(r"^###\s+([^—\n]+)", section.content or "", re.M)
        if m:
            names.add(m.group(1).strip().casefold())
    return names


def _is_placeholder_member_name(name: str | None) -> bool:
    text = (name or "").strip()
    if not text:
        return True
    lower = text.casefold()
    return lower.startswith("[verify") or "team member" in lower


def _pick_roster_members(
    roster_profiles: list[dict[str, Any]],
    *,
    exclude: set[str],
    count: int,
) -> list[str]:
    """Deterministic roster pick — no extra LLM tokens."""
    scored: list[tuple[int, str]] = []
    for profile in roster_profiles:
        name = str(profile.get("name") or "").strip()
        if not name or name.casefold() in exclude:
            continue
        title = f"{profile.get('title') or ''} {profile.get('snippet') or ''}".casefold()
        score = 0
        for hint, points in (
            ("director", 3),
            ("principal", 3),
            ("strateg", 2),
            ("develop", 2),
            ("design", 2),
            ("account", 2),
            ("producer", 1),
            ("manager", 1),
            ("intern", -3),
            ("assistant", -1),
        ):
            if hint in title:
                score += points
        scored.append((score, name))
    scored.sort(key=lambda row: (-row[0], row[1].casefold()))
    picked: list[str] = []
    seen: set[str] = set()
    for _, name in scored:
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        picked.append(name)
        if len(picked) >= count:
            break
    return picked


async def _resolve_bio_member_names(
    *,
    draft: ProposalDraft,
    additions: list[StructureAddition],
    rfp_client: str,
    rfp_sector: str,
    rfp_context: str,
) -> list[StructureAddition]:
    """Replace missing/placeholder bio names with real Master Team Roster people."""
    need = sum(
        1
        for a in additions
        if a.kind == "bio" and _is_placeholder_member_name(a.member_name)
    )
    if need <= 0:
        return additions

    from app.services import proposal_knowledge_base_tools
    from app.services.company_qualification.agents.team_selection import (
        build_roster_profiles,
    )

    roster_text, _ = await proposal_knowledge_base_tools.fetch_master_team_roster(
        rfp_client=rfp_client,
        rfp_sector=rfp_sector,
        rfp_context=rfp_context,
    )
    profiles = build_roster_profiles(roster_text or "")
    exclude = _existing_bio_names(draft.sections)
    # Also exclude names already explicitly requested in this plan
    for addition in additions:
        if addition.kind == "bio" and not _is_placeholder_member_name(addition.member_name):
            exclude.add((addition.member_name or "").casefold())

    picks = _pick_roster_members(profiles, exclude=exclude, count=need)
    if not picks:
        raise ProposalError(
            "Could not find additional team members on the Master Team Roster. "
            "Name the people you want added.",
            status_code=422,
        )

    pick_i = 0
    filled: list[StructureAddition] = []
    for addition in additions:
        if addition.kind != "bio" or not _is_placeholder_member_name(addition.member_name):
            filled.append(addition)
            continue
        if pick_i >= len(picks):
            break
        name = picks[pick_i]
        pick_i += 1
        filled.append(
            addition.model_copy(
                update={
                    "member_name": name,
                    "title": addition.title
                    if addition.title and not _is_placeholder_member_name(addition.title)
                    else f"2.x — {name}",
                }
            )
        )
    return filled


def _is_verify_bio_section(section: ProposalSection) -> bool:
    title_name = section.title.split("—", 1)[-1].strip() if "—" in section.title else section.title
    if _is_placeholder_member_name(title_name):
        return True
    content = section.content or ""
    return "[VERIFY" in content.upper() and (
        "team member" in content.casefold() or "bio for" in content.casefold()
    )


def _replace_section(
    sections: list[ProposalSection],
    *,
    old_id: str,
    new_section: ProposalSection,
) -> list[ProposalSection]:
    return [new_section if s.id == old_id else s for s in sections]


async def apply_chat_structure_plan(
    *,
    draft: ProposalDraft,
    plan: StructurePlan,
    rfp_client: str,
    rfp_sector: str = "",
    rfp_context: str = "",
) -> tuple[ProposalDraft, ProposalSection, str]:
    """Apply add/delete plan. Returns (draft, focus_section, assistant_message)."""
    sections = list(draft.sections)
    notes: list[str] = []

    focus: ProposalSection | None = None

    if plan.action == "delete_sections":
        remove_ids: list[str] = []
        for deletion in plan.deletions:
            sid = _resolve_deletion_id(draft, deletion)
            if sid:
                remove_ids.append(sid)
        if not remove_ids:
            raise ProposalError(
                "Could not match sections to delete. Name the exact sidebar title.",
                status_code=400,
            )
        if len(remove_ids) >= max(3, len(sections) - 1):
            raise ProposalError(
                "That would remove almost the whole proposal — please delete one section at a time.",
                status_code=400,
            )
        removed_titles = [s.title for s in sections if s.id in set(remove_ids)]
        sections = [s for s in sections if s.id not in set(remove_ids)]
        sections = renumber_dynamic_group_titles(sections)
        notes.append(
            "Deleted: " + ", ".join(f"**{t}**" for t in removed_titles[:8])
        )
        focus = sections[0] if sections else None

    if plan.action == "add_sections":
        # Pair deletions with same-kind additions → replace tab in place (new title/id).
        pending_replaces: list[tuple[str, StructureAddition]] = []
        remaining_deletions: list[StructureDeletion] = []
        add_queue = list(plan.additions[:6])
        used_adds: list[StructureAddition] = []
        for deletion in plan.deletions:
            sid = _resolve_deletion_id(
                draft.model_copy(update={"sections": sections}), deletion
            )
            target = next((s for s in sections if s.id == sid), None)
            if not target or not add_queue:
                if deletion not in remaining_deletions:
                    remaining_deletions.append(deletion)
                continue
            target_kind = _infer_addition_kind(target)
            match_i = next(
                (i for i, a in enumerate(add_queue) if a.kind == target_kind),
                None,
            )
            if match_i is None:
                remaining_deletions.append(deletion)
                continue
            addition = add_queue.pop(match_i)
            used_adds.append(addition)
            pending_replaces.append((target.id, addition))

        # Apply non-paired deletions first.
        if remaining_deletions:
            remove_ids = []
            for deletion in remaining_deletions:
                sid = _resolve_deletion_id(
                    draft.model_copy(update={"sections": sections}), deletion
                )
                if sid and sid not in {p[0] for p in pending_replaces}:
                    remove_ids.append(sid)
            if remove_ids:
                removed_titles = [
                    s.title for s in sections if s.id in set(remove_ids)
                ]
                sections = [s for s in sections if s.id not in set(remove_ids)]
                sections = renumber_dynamic_group_titles(sections)
                notes.append(
                    "Deleted: " + ", ".join(f"**{t}**" for t in removed_titles[:8])
                )

        draft_for_names = draft.model_copy(update={"sections": sections})
        paired_adds = await _resolve_bio_member_names(
            draft=draft_for_names,
            additions=used_adds + add_queue,
            rfp_client=rfp_client,
            rfp_sector=rfp_sector,
            rfp_context=rfp_context,
        )
        resolved_by_order = list(paired_adds)
        existing_ids = {s.id for s in sections}

        for old_id, _orig in pending_replaces:
            if not resolved_by_order:
                break
            addition = resolved_by_order.pop(0)
            old = next((s for s in sections if s.id == old_id), None)
            if not old:
                continue
            kind = addition.kind
            if kind == "bio":
                member = (addition.member_name or "").strip()
                if _is_placeholder_member_name(member):
                    raise ProposalError(
                        "Bio replace needs a real roster name.",
                        status_code=422,
                    )
                bios = _bio_sections(sections)
                index = (bios.index(old) + 1) if old in bios else len(bios) + 1
                new_sec = await _build_bio_section(
                    member_name=member,
                    index=index,
                    rfp_client=rfp_client,
                )
            elif kind == "case_study":
                cases = _case_study_sections(sections)
                index = (cases.index(old) + 1) if old in cases else len(cases) + 1
                name = (
                    addition.case_study_name or addition.title or "Case study"
                ).strip()
                name = re.sub(r"^3\.\d+\s*[—\-–:]\s*", "", name).strip() or name
                sec_id = f"section-3-work-{_slug(name)}"
                n = 2
                while sec_id in existing_ids and sec_id != old_id:
                    sec_id = f"section-3-work-{_slug(name)}-{n}"
                    n += 1
                new_sec = _build_stub_section(
                    section_id=sec_id,
                    title=f"3.{index} — {name}",
                    kind="case_study",
                    draft_hint=addition.draft_hint,
                )
            else:
                name = (addition.title or "New section").strip()
                name = _section_label(name) or name
                prefix = "rfp" if kind == "rfp" else "custom"
                sec_id = f"{prefix}-{_slug(name)}"
                n = 2
                while sec_id in existing_ids and sec_id != old_id:
                    sec_id = f"{prefix}-{_slug(name)}-{n}"
                    n += 1
                new_sec = _build_stub_section(
                    section_id=sec_id,
                    title=name,
                    kind=kind,
                    draft_hint=addition.draft_hint,
                )

            base_id = new_sec.id
            n = 2
            while new_sec.id in existing_ids and new_sec.id != old_id:
                new_sec = new_sec.model_copy(update={"id": f"{base_id}-{n}"})
                n += 1
            sections = _replace_section(
                sections, old_id=old_id, new_section=new_sec
            )
            existing_ids.discard(old_id)
            existing_ids.add(new_sec.id)
            focus = new_sec
            notes.append(f"Replaced section with **{new_sec.title}**")

        additions = resolved_by_order
        bio_count = len(_bio_sections(sections))
        case_count = len(_case_study_sections(sections))

        for addition in additions:
            kind = addition.kind
            if kind == "bio":
                member = (addition.member_name or "").strip()
                if _is_placeholder_member_name(member):
                    raise ProposalError(
                        "Bio add needs real roster names — none were available.",
                        status_code=422,
                    )
                # Prefer filling VERIFY stubs in place so "add 2 bios" doesn't
                # hide real people as 2.4/2.5 under leftover placeholders.
                verify_slot = next(
                    (s for s in _bio_sections(sections) if _is_verify_bio_section(s)),
                    None,
                )
                if verify_slot:
                    bios = _bio_sections(sections)
                    index = bios.index(verify_slot) + 1
                    new_sec = await _build_bio_section(
                        member_name=member,
                        index=index,
                        rfp_client=rfp_client,
                    )
                    base_id = new_sec.id
                    n = 2
                    while new_sec.id in existing_ids and new_sec.id != verify_slot.id:
                        new_sec = new_sec.model_copy(update={"id": f"{base_id}-{n}"})
                        n += 1
                    sections = _replace_section(
                        sections, old_id=verify_slot.id, new_section=new_sec
                    )
                    existing_ids.discard(verify_slot.id)
                    existing_ids.add(new_sec.id)
                    focus = new_sec
                    notes.append(f"Filled bio **{new_sec.title}**")
                    continue

                bio_count += 1
                new_sec = await _build_bio_section(
                    member_name=member,
                    index=bio_count,
                    rfp_client=rfp_client,
                )
                # Avoid id collisions
                base_id = new_sec.id
                n = 2
                while new_sec.id in existing_ids:
                    new_sec = new_sec.model_copy(update={"id": f"{base_id}-{n}"})
                    n += 1
                after = addition.insert_after_section_id
                if not after:
                    bios = _bio_sections(sections)
                    after = bios[-1].id if bios else None
                sections = _insert_after(sections, new_sec, after)
                existing_ids.add(new_sec.id)
                focus = new_sec
                notes.append(f"Added bio **{new_sec.title}**")
                continue

            if kind == "case_study":
                case_count += 1
                name = (addition.case_study_name or addition.title or f"Case study {case_count}").strip()
                name = re.sub(r"^3\.\d+\s*[—\-–:]\s*", "", name).strip() or name
                sec_id = f"section-3-work-{_slug(name)}"
                n = 2
                while sec_id in existing_ids:
                    sec_id = f"section-3-work-{_slug(name)}-{n}"
                    n += 1
                title = addition.title.strip() or f"3.{case_count} — {name}"
                new_sec = _build_stub_section(
                    section_id=sec_id,
                    title=title,
                    kind="case_study",
                    draft_hint=addition.draft_hint,
                )
                cases = _case_study_sections(sections)
                after = addition.insert_after_section_id or (
                    cases[-1].id if cases else None
                )
                sections = _insert_after(sections, new_sec, after)
                existing_ids.add(new_sec.id)
                focus = new_sec
                notes.append(f"Added case study **{new_sec.title}**")
                continue

            # custom / rfp
            title = (addition.title or "New section").strip()
            sec_id = f"{'rfp' if kind == 'rfp' else 'custom'}-{_slug(title)}"
            n = 2
            base = sec_id
            while sec_id in existing_ids:
                sec_id = f"{base}-{n}"
                n += 1
            new_sec = _build_stub_section(
                section_id=sec_id,
                title=title,
                kind=kind,
                draft_hint=addition.draft_hint,
            )
            sections = _insert_after(
                sections, new_sec, addition.insert_after_section_id
            )
            existing_ids.add(new_sec.id)
            focus = new_sec
            notes.append(f"Added section **{new_sec.title}**")

        sections = renumber_dynamic_group_titles(sections)
        # refresh focus after renumber
        if focus:
            focus = next((s for s in sections if s.id == focus.id), focus)

    if not sections:
        raise ProposalError("Structure change left no sections.", status_code=400)

    if focus is None:
        focus = sections[0]

    now = datetime.now(timezone.utc).isoformat()
    updated = draft.model_copy(
        update={"sections": sections, "updated_at": now}
    )
    header = (plan.assistant_note or "").strip()
    detail = "; ".join(notes) if notes else "Updated proposal structure."
    message = f"{header} {detail}".strip() if header else detail
    message += " New tabs are in the Sections sidebar with drafted content."
    return updated, focus, message
