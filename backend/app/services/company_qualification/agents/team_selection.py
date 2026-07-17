"""Team Selection Agent — extract RFP staffing needs, then map best-fit roster people."""

from __future__ import annotations

import logging
import re
from typing import Any

from app.services import llm
from app.services.company_qualification.schemas import (
    ProposalContext,
    RequiredTeamRole,
    TeamMemberSelection,
    TeamSelectionResult,
)

logger = logging.getLogger(__name__)

VERIFIED_NAME_CORRECTIONS = {
    "ron corner": "Ron Comer",
    "dyetola doyewunmi": "Oyetola Oyewunmi",
    "shawn dicrisio": "Shawn DiCriscio",
}

MIN_FIT_SCORE = 0.55
MAX_TEAM_SIZE = 5

# Agency principal — always considered for Principal-in-Charge on formal proposals.
AGENCY_OWNER_NAMES = ("Sonja Anderson",)
OWNER_TITLE_HINTS = (
    "founder",
    "ceo",
    "agency director",
    "owner",
    "principal",
    "president",
)
PIC_ROLE_TITLE = "Principal-in-Charge / Agency Owner"
PIC_ROLE_ALIASES = (
    "principal-in-charge",
    "principal in charge",
    "agency owner",
    "executive sponsor",
    "executive director",
    "pic",
    "agency director",
    "founder",
    "ceo",
)


def _canonicalize_verified_name(name: str) -> str:
    normalized = " ".join(name.strip().split())
    return VERIFIED_NAME_CORRECTIONS.get(normalized.casefold(), normalized)


def extract_roster_member_names(roster_text: str) -> list[str]:
    """Pull person names from Master Team Roster headings (## FIRST LAST)."""
    return [profile["name"] for profile in build_roster_profiles(roster_text)]


def build_roster_profiles(roster_text: str) -> list[dict[str, Any]]:
    """Compact per-person skill cards from Organizational Structure text."""
    found: list[dict[str, Any]] = []
    seen: set[str] = set()
    skip_last = {
        "team",
        "services",
        "structure",
        "accounts",
        "history",
        "experience",
        "department",
        "division",
        "agency",
        "marketing",
    }
    skip_first = {
        "your",
        "key",
        "organizational",
        "client",
        "creative",
        "seo",
        "ppc",
        "our",
        "the",
        "master",
    }
    date_in_line = re.compile(
        r"\b(?:19|20)\d{2}\s*-\s*(?:Present|(?:19|20)\d{2})\b",
        re.I,
    )
    header_re = re.compile(
        r"(?im)^(#{1,3})\s*([A-Z][A-Za-z'’-]+)\s+([A-Z][A-Za-z'’-]+)\s*$"
    )
    matches = list(header_re.finditer(roster_text))
    for idx, match in enumerate(matches):
        level = len(match.group(1))
        first, last = match.group(2), match.group(3)
        if first.casefold() in skip_first or last.casefold() in skip_last:
            continue
        if level >= 3:
            after = roster_text[match.end() : match.end() + 180]
            next_line = next((ln.strip() for ln in after.split("\n") if ln.strip()), "")
            if date_in_line.search(next_line):
                continue
        name = _canonicalize_verified_name(f"{first} {last}".title())
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)

        end = matches[idx + 1].start() if idx + 1 < len(matches) else min(len(roster_text), match.end() + 3500)
        block = roster_text[match.start() : end]
        # Prefer the richer block when the same person appears twice.
        title = ""
        for line in block.splitlines()[1:8]:
            stripped = line.strip().strip("*").strip()
            if not stripped or stripped.startswith("#") or stripped.startswith(">"):
                continue
            if len(stripped) < 80 and not stripped.lower().startswith(("ron ", "sonja ", "todd ")):
                title = stripped
                break
        expertise: list[dict[str, str]] = []
        for row in re.findall(
            r"\|\s*([^|]+?)\s*\|\s*(\d+\s*years?)\s*\|",
            block,
            flags=re.I,
        ):
            area = row[0].strip().strip("*")
            years = row[1].strip()
            if area and "---" not in area and "year" in years.lower():
                expertise.append({"area": area, "years": years})
        snippet = re.sub(r">\s*\*\*\[(?:photo|logo)\].*", "", block, flags=re.I)
        snippet = re.sub(r"\s+", " ", snippet).strip()[:900]
        found.append(
            {
                "name": name,
                "title": title,
                "expertise": expertise[:12],
                "snippet": snippet,
            }
        )
    return found


def normalize_selected_members(raw_members: list[Any], *, max_members: int = MAX_TEAM_SIZE) -> list[str]:
    """Skill-based list only — dedupe by last name, cap at max_members."""
    ordered: list[str] = []
    seen_last_names: set[str] = set()
    for raw in raw_members:
        name = _canonicalize_verified_name(str(raw).strip())
        if not name:
            continue
        last_name = name.casefold().split()[-1] if name.split() else name.casefold()
        if last_name in seen_last_names:
            continue
        seen_last_names.add(last_name)
        ordered.append(name)
        if len(ordered) >= max_members:
            break
    return ordered


def _filter_to_roster(
    members: list[TeamMemberSelection],
    roster_names: list[str],
) -> list[TeamMemberSelection]:
    """Keep only people who appear on the Master Team Roster (by last-name match)."""
    if not roster_names:
        return members
    by_last: dict[str, str] = {}
    by_full: dict[str, str] = {}
    for name in roster_names:
        by_full[name.casefold()] = name
        last = name.casefold().split()[-1] if name.split() else name.casefold()
        by_last.setdefault(last, name)

    filtered: list[TeamMemberSelection] = []
    seen: set[str] = set()
    for member in members:
        raw = _canonicalize_verified_name(member.name)
        canon = by_full.get(raw.casefold())
        if not canon:
            last = raw.casefold().split()[-1] if raw.split() else raw.casefold()
            canon = by_last.get(last)
        if not canon:
            logger.warning("Team Selection dropped non-roster name: %s", member.name)
            continue
        if canon.casefold() in seen:
            continue
        seen.add(canon.casefold())
        filtered.append(
            TeamMemberSelection(
                name=canon,
                role=member.role,
                rationale=member.rationale,
                fitScore=member.fit_score,
                matchedSkills=list(member.matched_skills),
            )
        )
    return filtered


def _is_pic_role(role: str) -> bool:
    key = role.casefold()
    return any(alias in key for alias in PIC_ROLE_ALIASES)


def _find_agency_owner(roster_profiles: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Prefer known owner name, else roster title that looks like founder/CEO."""
    by_name = {str(p.get("name") or "").casefold(): p for p in roster_profiles}
    for owner in AGENCY_OWNER_NAMES:
        if owner.casefold() in by_name:
            return by_name[owner.casefold()]
    for profile in roster_profiles:
        blob = f"{profile.get('title') or ''} {profile.get('snippet') or ''}".casefold()
        if any(hint in blob for hint in OWNER_TITLE_HINTS):
            return profile
    return None


def _ensure_principal_role(roles: list[RequiredTeamRole]) -> list[RequiredTeamRole]:
    """Formal proposals always need the agency owner as Principal-in-Charge."""
    if any(_is_pic_role(r.role) or r.is_leadership for r in roles):
        # Normalize leadership role title so Pass 2 maps owner correctly.
        out: list[RequiredTeamRole] = []
        for role in roles:
            if _is_pic_role(role.role) or role.is_leadership:
                out.append(
                    RequiredTeamRole(
                        role=PIC_ROLE_TITLE,
                        mustHaveSkills=[
                            "agency leadership",
                            "client ownership",
                            "executive accountability",
                            *list(role.must_have_skills),
                        ],
                        niceToHaveSkills=list(role.nice_to_have_skills),
                        whyNeeded=role.why_needed
                        or "Client needs a named principal accountable for delivery and quality.",
                        seniority="executive",
                        isLeadership=True,
                    )
                )
            else:
                out.append(role)
        # PIC first.
        out.sort(key=lambda r: (0 if _is_pic_role(r.role) else 1))
        return out[:MAX_TEAM_SIZE]

    pic = RequiredTeamRole(
        role=PIC_ROLE_TITLE,
        mustHaveSkills=[
            "agency leadership",
            "client ownership",
            "executive accountability",
            "proposal sponsorship",
        ],
        niceToHaveSkills=["public sector", "women-owned business leadership"],
        whyNeeded=(
            "Every formal proposal needs the agency owner / Principal-in-Charge "
            "accountable for quality, strategy, and client relationship."
        ),
        seniority="executive",
        isLeadership=True,
    )
    return [pic, *roles][:MAX_TEAM_SIZE]


def _ensure_owner_as_principal(
    members: list[TeamMemberSelection],
    roster_profiles: list[dict[str, Any]],
) -> list[TeamMemberSelection]:
    """Force agency owner into Principal-in-Charge — never a niche demo/pitch-only seat."""
    owner = _find_agency_owner(roster_profiles)
    if not owner:
        return members

    owner_name = str(owner["name"])
    pic_member = TeamMemberSelection(
        name=owner_name,
        role=PIC_ROLE_TITLE,
        rationale=(
            f"{owner_name} is zö agency Founder/CEO / Agency Director — "
            "Principal-in-Charge and executive owner for this engagement."
        ),
        fitScore=0.99,
        matchedSkills=[
            "agency leadership",
            "client ownership",
            "executive accountability",
        ],
    )

    others = [
        m
        for m in members
        if m.name.casefold() != owner_name.casefold() and not _is_pic_role(m.role)
    ]
    # Owner first, then remaining strong fits (cap at MAX_TEAM_SIZE).
    return [pic_member, *others][:MAX_TEAM_SIZE]


def _parse_required_roles(raw: dict[str, Any]) -> list[RequiredTeamRole]:
    roles_raw = raw.get("requiredRoles") or raw.get("required_roles") or raw.get("roles") or []
    if not isinstance(roles_raw, list):
        return []
    parsed: list[RequiredTeamRole] = []
    for entry in roles_raw:
        if isinstance(entry, str) and entry.strip():
            parsed.append(RequiredTeamRole(role=entry.strip()))
            continue
        if not isinstance(entry, dict):
            continue
        role_name = str(entry.get("role") or entry.get("title") or "").strip()
        if not role_name:
            continue
        try:
            parsed.append(RequiredTeamRole.model_validate(entry))
        except Exception:
            parsed.append(
                RequiredTeamRole(
                    role=role_name,
                    mustHaveSkills=[
                        str(s).strip()
                        for s in (entry.get("mustHaveSkills") or entry.get("must_have_skills") or [])
                        if str(s).strip()
                    ],
                    whyNeeded=str(entry.get("whyNeeded") or entry.get("why_needed") or "").strip(),
                )
            )
    # Cap staffing needs — proposals rarely need more than 5 named bios.
    return parsed[:MAX_TEAM_SIZE]


async def _extract_rfp_staffing_needs(
    *,
    proposal_context: ProposalContext,
    rfp_context: str,
) -> tuple[list[RequiredTeamRole], str]:
    """Pass 1 — extract what the RFP needs. No person names allowed."""
    raw, provider = await llm.chat_json(
        [
            {
                "role": "system",
                "content": (
                    "You are the RFP Staffing Needs Analyst for zö agency.\n"
                    "Extract ONLY the roles this solicitation needs for a winning proposal team.\n"
                    "Do NOT name any people. Do NOT invent roles the RFP does not imply.\n\n"
                    "Rules:\n"
                    "- Derive roles from scope, evaluation criteria, deliverables, and required expertise.\n"
                    "- ALWAYS include Principal-in-Charge / Agency Owner as the FIRST role "
                    "(isLeadership=true) for formal client RFPs — the agency owner must be named.\n"
                    "- Then add 2–4 specialist roles the RFP actually needs.\n"
                    "- For each role, list must-have skills (required) and nice-to-have skills.\n"
                    "- Be specific (e.g. 'Paid Media / Media Buying Lead', not vague 'Marketer').\n\n"
                    "Return JSON:\n"
                    "{\n"
                    '  "requiredRoles": [\n'
                    "    {\n"
                    '      "role": "Media Buying Lead",\n'
                    '      "mustHaveSkills": ["media buying", "broadcast", "digital media"],\n'
                    '      "niceToHaveSkills": ["public sector"],\n'
                    '      "whyNeeded": "RFP requires paid media planning and buying",\n'
                    '      "seniority": "senior",\n'
                    '      "isLeadership": false\n'
                    "    }\n"
                    "  ]\n"
                    "}"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Proposal context:\n{proposal_context.model_dump_json()}\n\n"
                    f"RFP context:\n{rfp_context[:18000]}"
                ),
            },
        ],
        temperature=0.0,
    )
    roles = _parse_required_roles(raw if isinstance(raw, dict) else {})
    roles = _ensure_principal_role(roles)
    logger.info(
        "Team Selection Pass 1 (RFP needs): %s",
        [r.model_dump(by_alias=True) for r in roles],
    )
    return roles, provider


async def _map_roles_to_best_people(
    *,
    role_requirements: list[RequiredTeamRole],
    roster_profiles: list[dict[str, Any]],
    proposal_context: ProposalContext,
) -> tuple[list[TeamMemberSelection], str]:
    """Pass 2 — map each RFP role to the single best-fit roster person."""
    if not role_requirements or not roster_profiles:
        return [], "none"

    raw, provider = await llm.chat_json(
        [
            {
                "role": "system",
                "content": (
                    "You are the Team Fit Mapper for zö agency Section 2.\n"
                    "For EACH required RFP role, choose the ONE strongest person from ZO_ROSTER_PROFILES.\n\n"
                    "Rules:\n"
                    "- Choose only superior fits (fitScore >= 0.70 preferred; never below 0.55).\n"
                    "- Match must-have skills to the person's title + expertise years + snippet.\n"
                    "- Prefer deeper relevant years of experience for that role's core skill.\n"
                    "- One person may fill only ONE role. No duplicate names.\n"
                    "- For Principal-in-Charge / Agency Owner / Executive Sponsor: choose the "
                    "Founder/CEO/Agency Director (Sonja Anderson when present on the roster). "
                    "Do NOT assign the owner to niche demo/pitch-only roles.\n"
                    "- If no roster person is a strong fit for a specialist role, OMIT that role "
                    "(do not force a weak pick). Always fill Principal-in-Charge when the owner is on roster.\n"
                    "- Never invent names. Names must match ZO_ROSTER_PROFILES exactly.\n"
                    "- Rationale must cite concrete roster evidence (title/skills/years).\n"
                    f"- Return at most {MAX_TEAM_SIZE} members, ordered by importance to the RFP.\n\n"
                    "Return JSON:\n"
                    "{\n"
                    '  "members": [\n'
                    "    {\n"
                    '      "name": "Full Name",\n'
                    '      "role": "RFP role being filled",\n'
                    '      "fitScore": 0.92,\n'
                    '      "matchedSkills": ["media buying", "account management"],\n'
                    '      "rationale": "38 yrs account management + traditional/digital media expertise"\n'
                    "    }\n"
                    "  ]\n"
                    "}"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Proposal context:\n{proposal_context.model_dump_json()}\n\n"
                    f"RFP_REQUIRED_ROLES:\n{[r.model_dump(by_alias=True) for r in role_requirements]}\n\n"
                    f"ZO_ROSTER_PROFILES:\n{roster_profiles}"
                ),
            },
        ],
        temperature=0.0,
        max_tokens=2048,
    )

    members_raw = raw.get("members") or [] if isinstance(raw, dict) else []
    members: list[TeamMemberSelection] = []
    for entry in members_raw:
        if not isinstance(entry, dict) or not entry.get("name"):
            continue
        try:
            member = TeamMemberSelection.model_validate(entry)
        except Exception:
            member = TeamMemberSelection(
                name=str(entry.get("name")),
                role=str(entry.get("role") or ""),
                rationale=str(entry.get("rationale") or ""),
                fitScore=float(entry.get("fitScore") or entry.get("fit_score") or 0),
                matchedSkills=[
                    str(s).strip()
                    for s in (entry.get("matchedSkills") or entry.get("matched_skills") or [])
                    if str(s).strip()
                ],
            )
        if member.fit_score and member.fit_score < MIN_FIT_SCORE:
            logger.warning(
                "Team Selection dropped weak fit %s for %s (score=%.2f)",
                member.name,
                member.role,
                member.fit_score,
            )
            continue
        members.append(member)
    return members, provider


async def run_team_selection_agent(
    *,
    proposal_context: ProposalContext,
    rfp_context: str,
    roster_text: str,
    roster_doc_label: str,
) -> tuple[TeamSelectionResult, str]:
    """Two-pass selection: extract RFP needs → map superior roster fits only."""
    del roster_doc_label  # kept for call-site compatibility / logging upstream
    roster_profiles = build_roster_profiles(roster_text)
    roster_names = [p["name"] for p in roster_profiles]

    role_requirements, provider_1 = await _extract_rfp_staffing_needs(
        proposal_context=proposal_context,
        rfp_context=rfp_context,
    )
    mapped, provider_2 = await _map_roles_to_best_people(
        role_requirements=role_requirements,
        roster_profiles=roster_profiles,
        proposal_context=proposal_context,
    )

    filtered = _filter_to_roster(mapped, roster_names)
    filtered = _ensure_owner_as_principal(filtered, roster_profiles)
    names = normalize_selected_members([m.name for m in filtered])
    by_name = {m.name.casefold(): m for m in filtered}
    ordered: list[TeamMemberSelection] = []
    for name in names:
        existing = by_name.get(name.casefold())
        ordered.append(existing or TeamMemberSelection(name=name))

    # Principal-in-Charge / agency owner first, then remaining by RFP role order.
    role_order = {r.role.casefold(): i for i, r in enumerate(role_requirements)}
    ordered.sort(
        key=lambda m: (
            0 if _is_pic_role(m.role) or m.name.casefold() in {n.casefold() for n in AGENCY_OWNER_NAMES} else 1,
            role_order.get(m.role.casefold(), 999),
            -m.fit_score,
        )
    )

    result = TeamSelectionResult(
        requiredRoles=[r.role for r in role_requirements],
        roleRequirements=role_requirements,
        members=ordered,
    )
    provider = provider_2 or provider_1
    logger.info(
        "Team Selection Pass 2 (best fits): requiredRoles=%s members=%s",
        result.model_dump(by_alias=True).get("requiredRoles"),
        [
            {
                "name": member.name,
                "proposalRole": member.role,
                "fitScore": member.fit_score,
                "matchedSkills": member.matched_skills,
                "rationale": member.rationale,
            }
            for member in ordered
        ],
    )
    return result, provider
