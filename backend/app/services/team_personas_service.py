"""Service for managing Key Personas (zö agency team members with KB resumes)."""

import re
import logging
from typing import Any

from app.services import supermemory

logger = logging.getLogger(__name__)

# Baseline set of zö team members always expected to have an approved
# 04_Bio_*.pdf on file — used as a sanity floor, not an allowlist.
VERIFIED_TEAM_PERSONAS: tuple[str, ...] = (
    "Sonja Anderson",
    "Rachael Rice",
    "Ella Lindau",
    "Sarah Eichhorn",
)


def _name_to_id(name: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return cleaned or "team-member"


def _extract_name_from_bio_filename(filename: str) -> str:
    """Extract clean display name from filenames containing BIO-, 04_Bio_, BIO_, etc.
    Examples:
      '04_BIO-John_Doe.pdf' -> 'John Doe'
      'BIO-Sonja_Anderson.pdf' -> 'Sonja Anderson'
      '04_Bio_SonjaAnderson.pdf' -> 'Sonja Anderson'
      '04_BIO_Ella_Lindau.pdf' -> 'Ella Lindau'
    """
    if not filename:
        return ""
    base = re.sub(r"\.[a-zA-Z0-9]+$", "", filename)
    base = base.split("/")[-1].split("\\")[-1]
    base = re.sub(r"^(?:04_|4_)?(?:BIO|Bio|bio)[_\-\s]*", "", base, flags=re.I)
    base = base.replace("_", " ").replace("-", " ")
    base = re.sub(r"([a-z])([A-Z])", r"\1 \2", base)
    cleaned = " ".join(base.split()).title()
    return cleaned


async def get_all_key_personas() -> list[dict[str, Any]]:
    """Fetch all members list of zö whose resume/bio is available in Knowledge Base (Supermemory)."""
    personas_map: dict[str, dict[str, Any]] = {}

    if supermemory.is_configured():
        try:
            import asyncio
            memories = await asyncio.wait_for(
                supermemory.list_container_memories(limit=1000), timeout=5.0
            )
            for mem in memories:
                meta = mem.get("metadata") if isinstance(mem.get("metadata"), dict) else {}
                file_name = str(
                    meta.get("fileName")
                    or mem.get("filepath")
                    or meta.get("title")
                    or mem.get("title")
                    or ""
                )
                category = str(meta.get("category") or "")
                title = str(meta.get("title") or mem.get("title") or file_name)

                fn_lower = file_name.lower()
                is_bio = (
                    "bio" in fn_lower
                    or "resume" in fn_lower
                    or category in {"team_bio", "04_"}
                )
                if not is_bio:
                    continue

                extracted_name = _extract_name_from_bio_filename(file_name) or title
                if not extracted_name or len(extracted_name.strip()) < 2:
                    continue
                if re.search(r"\b(?:master|template|org\s*structure|organization)\b", extracted_name, re.I):
                    continue

                person_id = _name_to_id(extracted_name)
                if person_id not in personas_map:
                    personas_map[person_id] = {
                        "id": person_id,
                        "name": extracted_name,
                        "title": str(meta.get("role") or meta.get("title") or "Team Specialist"),
                        "hasResume": True,
                        "sourceFile": file_name,
                        "bioSnippet": f"Approved team bio document: {title or file_name}.",
                    }
                else:
                    if "bio" in fn_lower:
                        personas_map[person_id]["sourceFile"] = file_name
        except Exception as exc:
            logger.warning("Could not query Supermemory memories for personas: %s", exc)

    from app.services.retired_staff_store import is_retired_id, retired_records

    for person in personas_map.values():
        person["retired"] = bool(is_retired_id(str(person.get("id") or "")))

    for row in retired_records():
        pid = row["id"]
        if pid in personas_map:
            personas_map[pid]["retired"] = True
            continue
        personas_map[pid] = {
            "id": pid,
            "name": row["name"],
            "title": "Retired — do not assign",
            "hasResume": False,
            "sourceFile": "",
            "bioSnippet": "Marked retired in Key Personas. Agents will not assign this person as current staff.",
            "retired": True,
        }

    return list(personas_map.values())
