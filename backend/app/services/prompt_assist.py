"""Plain-language help on the Prompts page: what a prompt does, and a rewrite from an instruction.

Neither result is saved here. The page shows the suggestion and the person decides.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from typing import Any

from app.services import llm, prompt_store
from app.services.prompt_store import MAX_BODY_CHARS

logger = logging.getLogger(__name__)

MAX_INSTRUCTION_CHARS = 1_000
_MAX_GROWTH = 3

_GUIDE_SYSTEM = """You explain an AI helper's instructions to an office worker who is not technical.
Read the instructions and answer in plain, friendly words. Never use these words: prompt, JSON, LLM, token,
model, schema, parse, regex. Say "the AI helper" and "the instructions".

Return JSON only: {"what": "...", "when": "..."}
- "what": one or two short sentences. What this helper does and where it shows up in the proposal work.
- "when": one or two short sentences. The kind of problem that is a good reason to change these instructions,
  for example "if proposals keep sounding too formal".
"""

_ASSIST_SYSTEM = """You change the written instructions that an AI helper follows. The person asking is not technical
and describes the change in plain words. Make exactly that change and nothing else.

Rules:
1. Keep every other line word for word. Do not tidy, reorder or reword what they did not ask about.
2. Never remove or rename the output format the instructions demand: JSON field names, "Return JSON" lines,
   [BRACKETED TAGS], and anything in {braces}. Other code reads the helper's reply and breaks without them.
3. If the request would break rule 2, contradicts the instructions' safety rules (such as never inventing facts),
   or is too unclear to apply, do not guess. Set "ok" to false and explain in one friendly sentence, and say what
   they could ask for instead.
4. Write the "summary" for a non-technical reader: one to four short bullets saying what you changed, no jargon.

Return JSON only: {"ok": true, "revised": "<the complete revised instructions>", "summary": ["..."], "note": ""}
or {"ok": false, "revised": "", "summary": [], "note": "<friendly explanation>"}
"""

_guides: dict[str, tuple[str, dict[str, str]]] = {}


class AssistError(Exception):
    """The helper could not produce a usable suggestion; the message is safe to show."""


def _clean_lines(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if str(v).strip()][:4]


async def guide(key: str, body: str) -> dict[str, str]:
    """What a prompt does and when to change it.

    Written by the AI once per version of the text and saved in Supabase, so opening the page
    never costs a call. A new note is written only after the prompt's text has changed.
    """
    body_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
    cached = _guides.get(key)
    if cached and cached[0] == body_hash:
        return cached[1]
    try:
        saved = await asyncio.to_thread(prompt_store.load_guide, key, body_hash)
    except Exception as exc:  # noqa: BLE001
        logger.info("prompt guide read failed for %s: %s", key, str(exc)[:200])
        saved = None
    if saved:
        _guides[key] = (body_hash, saved)
        return saved
    raw, _provider = await llm.chat_json(
        [
            {"role": "system", "content": _GUIDE_SYSTEM},
            {"role": "user", "content": body[:20_000]},
        ],
        max_tokens=400,
        temperature=0.2,
        tier="light",
        node_name="prompt_guide",
        include_corrections=False,
    )
    result = {"what": str(raw.get("what") or "").strip(), "when": str(raw.get("when") or "").strip()}
    if result["what"]:
        _guides[key] = (body_hash, result)
        try:
            await asyncio.to_thread(prompt_store.store_guide, key, body_hash, result["what"], result["when"])
        except Exception as exc:  # noqa: BLE001
            logger.info("prompt guide save failed for %s: %s", key, str(exc)[:200])
    return result


async def suggest_change(current_body: str, instruction: str) -> dict[str, Any]:
    """Apply a plain-language change request. Returns {"revised", "summary"} or raises AssistError."""
    instruction = instruction.strip()
    if not instruction:
        raise AssistError("Tell me what you would like changed.")
    raw, _provider = await llm.chat_json(
        [
            {"role": "system", "content": _ASSIST_SYSTEM},
            {
                "role": "user",
                "content": f"WHAT THEY WANT CHANGED:\n{instruction[:MAX_INSTRUCTION_CHARS]}\n\nCURRENT INSTRUCTIONS:\n{current_body}",
            },
        ],
        max_tokens=min(16_000, max(2_000, len(current_body) // 2)),
        temperature=0.1,
        tier="heavy",
        node_name="prompt_assist",
        include_corrections=False,
    )
    if not raw.get("ok"):
        raise AssistError(str(raw.get("note") or "I could not make that change safely. Try describing it differently.").strip())
    revised = str(raw.get("revised") or "").strip()
    if not revised or revised == current_body.strip():
        raise AssistError("That would not change anything. Try describing the change differently.")
    if len(revised) > min(MAX_BODY_CHARS, max(2_000, len(current_body) * _MAX_GROWTH)):
        raise AssistError("That change came out far too long. Try asking for something smaller.")
    if "json" in current_body.lower() and "json" not in revised.lower():
        raise AssistError("That change would remove the reply format other parts of the app rely on, so I did not apply it.")
    return {"revised": revised, "summary": _clean_lines(raw.get("summary"))}
