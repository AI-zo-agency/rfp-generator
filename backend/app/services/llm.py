import asyncio
import json
import logging
import re
from typing import Any

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_PLACEHOLDER_KEY_MARKERS = (
    "your_openrouter_key",
    "changeme",
    "replace_me",
    "xxx",
)


class LlmError(Exception):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def is_configured() -> bool:
    gemini_key = settings.gemini_api_key.strip()
    if gemini_key and not _is_placeholder_key(gemini_key):
        return True
    return bool(_openrouter_key() or _fireworks_key())


def _openrouter_key() -> str:
    key = settings.openrouter_api_key.strip()
    if not key or _is_placeholder_key(key):
        return ""
    return key


def _fireworks_key() -> str:
    return settings.fireworks_api_key.strip()


def _is_placeholder_key(key: str) -> bool:
    lowered = key.strip().lower()
    if lowered in _PLACEHOLDER_KEY_MARKERS:
        return True
    return lowered.startswith("your_") or lowered.startswith("paste_")


async def _post_gemini_chat(
    *,
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
    max_tokens: int | None = None,
    temperature: float = 0.2,
    json_mode: bool = True,
) -> str:
    """Call Gemini API directly."""
    # Use v1beta for JSON mode support, but don't prefix model name with "models/"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    
    # Convert messages to Gemini format
    contents = []
    for msg in messages:
        role = "user" if msg["role"] in ("user", "system") else "model"
        contents.append({
            "role": role,
            "parts": [{"text": msg["content"]}]
        })
    
    generation_config: dict[str, Any] = {"temperature": temperature}
    if json_mode:
        generation_config["response_mime_type"] = "application/json"
    body: dict[str, Any] = {
        "contents": contents,
        "generationConfig": generation_config,
    }
    if max_tokens:
        body["generationConfig"]["maxOutputTokens"] = max_tokens
    
    logger.info("LLM request: provider=Gemini model=%s messages=%d", model, len(messages))

    from app.services.proposal_generation_cancel import run_with_generation_cancel

    async def _post() -> httpx.Response:
        async with httpx.AsyncClient(timeout=180.0) as client:
            return await client.post(url, json=body)

    response = await run_with_generation_cancel(_post)
    
    if response.status_code >= 400:
        detail = response.text.strip() or response.reason_phrase
        logger.warning("LLM error: provider=Gemini model=%s status=%s detail=%s", model, response.status_code, detail[:300])
        raise LlmError(f"Gemini API error ({response.status_code}): {detail}", status_code=response.status_code)
    
    data = response.json()
    finish_reason = ""
    try:
        finish_reason = str(data["candidates"][0].get("finishReason") or "")
    except (KeyError, IndexError, TypeError):
        pass
    if finish_reason == "MAX_TOKENS":
        logger.warning(
            "Gemini hit MAX_TOKENS model=%s — response may be truncated JSON",
            model,
        )
    try:
        content = data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LlmError(f"Gemini returned an unexpected response shape") from exc
    
    if not isinstance(content, str) or not content.strip():
        raise LlmError(f"Gemini returned empty content")
    
    logger.info("LLM success: provider=Gemini model=%s response_chars=%d", model, len(content))
    return content.strip()


async def _post_chat(
    *,
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
    provider: str,
    extra_headers: dict[str, str] | None = None,
    max_tokens: int | None = None,
    temperature: float = 0.2,
    json_mode: bool = True,
) -> str:
    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        **(extra_headers or {}),
    }
    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    if max_tokens is not None:
        body["max_tokens"] = max_tokens

    logger.info(
        "LLM request: provider=%s model=%s messages=%d",
        provider,
        model,
        len(messages),
    )

    last_error: LlmError | None = None
    from app.services.proposal_generation_cancel import run_with_generation_cancel

    for attempt in range(4):

        async def _post() -> httpx.Response:
            async with httpx.AsyncClient(timeout=180.0) as client:
                return await client.post(url, headers=headers, json=body)

        response = await run_with_generation_cancel(_post)

        if response.status_code == 429 and attempt < 3:
            wait_s = 2 ** (attempt + 1)
            logger.warning(
                "LLM rate limited (%s), retrying in %ds (attempt %d/3)",
                provider,
                wait_s,
                attempt + 1,
            )
            await asyncio.sleep(wait_s)
            continue

        if response.status_code >= 400:
            detail = response.text.strip() or response.reason_phrase
            logger.warning(
                "LLM error: provider=%s model=%s status=%s detail=%s",
                provider,
                model,
                response.status_code,
                detail[:300],
            )
            last_error = LlmError(
                f"{provider} API error ({response.status_code}): {detail}",
                status_code=response.status_code,
            )
            # Payment/credit errors won't succeed on retry — fail fast to fallback.
            if response.status_code in (402, 403):
                break
            break

        data = response.json()
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LlmError(f"{provider} returned an unexpected response shape") from exc

        if not isinstance(content, str) or not content.strip():
            raise LlmError(f"{provider} returned empty content")

        # Check if response looks truncated (suspiciously short for a JSON response)
        if len(content) < 30 and '"content":' in content:
            logger.warning(
                "LLM response appears truncated: provider=%s model=%s chars=%d content=%s",
                provider,
                model,
                len(content),
                content[:200],
            )
            # Check if this was due to token limits in the response
            usage = data.get("usage", {})
            if usage:
                logger.info(f"Token usage: {usage}")
            raise LlmError(f"{provider} returned truncated response (only {len(content)} chars)")

        logger.info(
            "LLM success: provider=%s model=%s response_chars=%d",
            provider,
            model,
            len(content),
        )
        return content.strip()

    if last_error:
        raise last_error
    raise LlmError(f"{provider} request failed after retries", status_code=429)


async def chat_json(
    messages: list[dict[str, str]],
    *,
    max_tokens: int | None = None,
    temperature: float = 0.2,
) -> tuple[dict[str, Any], str]:
    errors: list[str] = []

    # Try Gemini first if API key is configured and not skipped by preferences
    gemini_key = settings.gemini_api_key.strip()
    skip_gemini = settings.llm_prefer_openrouter or settings.llm_prefer_fireworks
    if gemini_key and not _is_placeholder_key(gemini_key) and not skip_gemini:
        try:
            raw = await _post_gemini_chat(
                api_key=gemini_key,
                model=settings.gemini_model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            return _parse_json_response(raw), "gemini"
        except LlmError as exc:
            errors.append(str(exc))
            logger.info("Gemini failed: %s", str(exc)[:200])

    openrouter_key = _openrouter_key()
    skip_openrouter = settings.llm_prefer_fireworks
    if openrouter_key and not skip_openrouter:
        try:
            raw = await _post_chat(
                base_url=settings.openrouter_base_url,
                api_key=openrouter_key,
                model=settings.openrouter_model,
                messages=messages,
                provider="OpenRouter",
                extra_headers={
                    "HTTP-Referer": settings.app_url,
                    "X-Title": settings.app_name,
                },
                max_tokens=max_tokens,
                temperature=temperature,
            )
            return _parse_json_response(raw), "openrouter"
        except LlmError as exc:
            errors.append(str(exc))
            logger.info("OpenRouter failed: %s", str(exc)[:200])
            # If OpenRouter response was truncated, likely due to model limits
            # Don't bother trying Fireworks fallback as it will likely hit same issue
            if "truncated" in str(exc).lower() or len(str(exc)) < 100:
                logger.info("OpenRouter returned truncated response, retrying without Fireworks fallback")

    fireworks_key = _fireworks_key()
    if fireworks_key:
        try:
            # Scale with caller request — hard cap 8192 (pricing budgets need room).
            requested = max_tokens or 4096
            fireworks_tokens = min(requested, 8192)
            raw = await _post_chat(
                base_url=settings.fireworks_base_url,
                api_key=fireworks_key,
                model=settings.fireworks_model,
                messages=messages,
                provider="Fireworks",
                max_tokens=fireworks_tokens,
                temperature=temperature,
            )
            return _parse_json_response(raw), "fireworks"
        except LlmError as exc:
            # If Fireworks account is suspended (412), don't count it as a provider failure
            if exc.status_code == 412:
                logger.warning("Fireworks account suspended - skipping for future calls")
            else:
                errors.append(str(exc))

    if not errors:
        raise LlmError(
            "No LLM API key configured. Set OPENROUTER_API_KEY (primary) or FIREWORKS_API_KEY (fallback).",
            status_code=503,
        )

    raise LlmError(
        "All configured LLM providers failed: " + "; ".join(errors),
        status_code=502,
    )


async def chat_text(
    messages: list[dict[str, str]],
    *,
    max_tokens: int | None = None,
    temperature: float = 0.2,
) -> tuple[str, str]:
    """Plain-text chat completion (no JSON response format)."""
    errors: list[str] = []

    gemini_key = settings.gemini_api_key.strip()
    skip_gemini = settings.llm_prefer_openrouter or settings.llm_prefer_fireworks
    if gemini_key and not _is_placeholder_key(gemini_key) and not skip_gemini:
        try:
            raw = await _post_gemini_chat(
                api_key=gemini_key,
                model=settings.gemini_model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                json_mode=False,
            )
            return raw, "gemini"
        except LlmError as exc:
            errors.append(str(exc))

    openrouter_key = _openrouter_key()
    skip_openrouter = settings.llm_prefer_fireworks
    if openrouter_key and not skip_openrouter:
        try:
            raw = await _post_chat(
                base_url=settings.openrouter_base_url,
                api_key=openrouter_key,
                model=settings.openrouter_model,
                messages=messages,
                provider="OpenRouter",
                extra_headers={
                    "HTTP-Referer": settings.app_url,
                    "X-Title": settings.app_name,
                },
                max_tokens=max_tokens,
                temperature=temperature,
                json_mode=False,
            )
            return raw, "openrouter"
        except LlmError as exc:
            errors.append(str(exc))

    fireworks_key = _fireworks_key()
    if fireworks_key:
        try:
            requested = max_tokens or 4096
            fireworks_tokens = min(requested, 8192)
            raw = await _post_chat(
                base_url=settings.fireworks_base_url,
                api_key=fireworks_key,
                model=settings.fireworks_model,
                messages=messages,
                provider="Fireworks",
                max_tokens=fireworks_tokens,
                temperature=temperature,
                json_mode=False,
            )
            return raw, "fireworks"
        except LlmError as exc:
            if exc.status_code != 412:
                errors.append(str(exc))

    if not errors:
        raise LlmError(
            "No LLM API key configured. Set GEMINI_API_KEY, OPENROUTER_API_KEY, or FIREWORKS_API_KEY.",
            status_code=503,
        )

    raise LlmError(
        "All configured LLM providers failed: " + "; ".join(errors),
        status_code=502,
    )


def _strip_code_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.IGNORECASE)
        stripped = re.sub(r"\s*```$", "", stripped)
    return stripped.strip()


def _extract_json_from_text(text: str) -> str:
    """Extract JSON from text that may have explanatory prefixes or markdown formatting."""
    text = text.strip()
    
    # Try to find JSON object starting with {
    brace_start = text.find('{')
    if brace_start > 0:
        # There's text before the JSON, extract from the first {
        text = text[brace_start:]
    
    # Also try to find JSON array starting with [
    bracket_start = text.find('[')
    if bracket_start >= 0 and (brace_start < 0 or bracket_start < brace_start):
        text = text[bracket_start:]
    
    return _strip_code_fence(text)


def _close_truncated_json(text: str) -> str:
    """Close an unterminated string and trailing brackets/braces."""
    s = text.strip()
    in_string = False
    escape = False
    for ch in s:
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
    if in_string:
        s += '"'
    s = s.rstrip().rstrip(",")
    open_brackets = s.count("[") - s.count("]")
    open_braces = s.count("{") - s.count("}")
    if open_brackets > 0:
        s += "]" * open_brackets
    if open_braces > 0:
        s += "}" * open_braces
    return s


def _try_parse_json_object(text: str) -> dict[str, Any] | None:
    for candidate in (text, _close_truncated_json(text)):
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _unwrap_nested_json(parsed: dict[str, Any]) -> dict[str, Any]:
    for key in ("output", "response", "result", "data"):
        value = parsed.get(key)
        if isinstance(value, str) and value.strip():
            inner = _try_parse_json_object(_strip_code_fence(value.strip()))
            if inner is not None:
                return inner
        if isinstance(value, dict):
            return value
    return parsed


def _salvage_line_items(text: str) -> list[dict[str, Any]]:
    """Recover complete budget line-item objects from truncated JSON."""
    items: list[dict[str, Any]] = []
    pattern = re.compile(
        r'\{\s*"id"\s*:\s*"([^"]+)"\s*,\s*"category"\s*:\s*"([^"]*)"\s*,'
        r'\s*"description"\s*:\s*"((?:\\.|[^"\\])*)"\s*,'
        r'(?:(?:"namedPerson"\s*:\s*(?:"((?:\\.|[^"\\])*)"|null)\s*,\s*)?)?'
        r'(?:(?:"roleTitle"\s*:\s*(?:"((?:\\.|[^"\\])*)"|null)\s*,\s*)?)?'
        r'\s*"unit"\s*:\s*"([^"]*)"\s*,\s*"quantity"\s*:\s*(\d+(?:\.\d+)?)\s*,'
        r'\s*"rate"\s*:\s*(\d+(?:\.\d+)?)\s*,\s*"extended"\s*:\s*(\d+(?:\.\d+)?)',
        re.DOTALL,
    )
    for match in pattern.finditer(text):
        try:
            description = json.loads(f'"{match.group(3)}"')
        except json.JSONDecodeError:
            description = match.group(3).replace("\\n", "\n").replace('\\"', '"')
        items.append(
            {
                "id": match.group(1),
                "category": match.group(2) or "labor",
                "description": description,
                "namedPerson": match.group(4),
                "roleTitle": match.group(5),
                "unit": match.group(6) or "flat",
                "quantity": float(match.group(7)),
                "rate": float(match.group(8)),
                "extended": float(match.group(9)),
            }
        )
    return items


def _salvage_budget_payload(text: str) -> dict[str, Any] | None:
    """Recover budget fields from truncated Stage 3 JSON."""
    payload: dict[str, Any] = {}

    cap_match = re.search(r'"rfpBudgetCap"\s*:\s*(null|\d+(?:\.\d+)?)', text)
    if cap_match:
        cap_val = cap_match.group(1)
        payload["rfpBudgetCap"] = None if cap_val == "null" else float(cap_val)

    for key, pattern in (
        ("pricingTier", r'"pricingTier"\s*:\s*"(Low|Average|High)"'),
        ("budgetFormat", r'"budgetFormat"\s*:\s*"(phased|personnel_loading|service_menu)"'),
        ("feeStructure", r'"feeStructure"\s*:\s*"((?:\\.|[^"\\])*)"'),
        ("scopeSummary", r'"scopeSummary"\s*:\s*"((?:\\.|[^"\\])*)"'),
    ):
        match = re.search(pattern, text)
        if not match:
            continue
        value = match.group(1)
        if key != "pricingTier" and key != "budgetFormat":
            try:
                value = json.loads(f'"{value}"')
            except json.JSONDecodeError:
                value = value.replace("\\n", "\n").replace('\\"', '"')
        payload[key] = value

    notes_match = re.search(r'"rfpBudgetNotes"\s*:\s*"((?:\\.|[^"\\])*)"', text)
    if notes_match:
        try:
            payload["rfpBudgetNotes"] = json.loads(f'"{notes_match.group(1)}"')
        except json.JSONDecodeError:
            payload["rfpBudgetNotes"] = notes_match.group(1)

    line_items = _salvage_line_items(text)
    if line_items:
        payload["lineItems"] = line_items

    flags_match = re.search(r'"pricingFlags"\s*:\s*\[(.*?)\]', text, re.DOTALL)
    if flags_match:
        flags = re.findall(r'"((?:\\.|[^"\\])*)"', flags_match.group(1))
        if flags:
            payload["pricingFlags"] = [
                f.replace("\\n", "\n").replace('\\"', '"') for f in flags
            ]

    conf_match = re.search(r'"confidence"\s*:\s*(\d+)', text)
    if conf_match:
        payload["confidence"] = int(conf_match.group(1))

    if line_items or payload.get("pricingTier") or payload.get("budgetFormat"):
        return payload
    return None


def _unescape_json_string(raw: str) -> str:
    try:
        return json.loads(f'"{raw}"')
    except json.JSONDecodeError:
        return (
            raw.replace("\\n", "\n")
            .replace("\\t", "\t")
            .replace('\\"', '"')
            .replace("\\\\", "\\")
        )


def _extract_json_string_value(chunk: str, *, allow_partial: bool) -> str:
    """Read a JSON string value from chunk (starts after opening quote)."""
    buf: list[str] = []
    i = 0
    while i < len(chunk):
        ch = chunk[i]
        if ch == "\\" and i + 1 < len(chunk):
            buf.append(chunk[i : i + 2])
            i += 2
            continue
        if ch == '"':
            raw = "".join(buf)
            return _unescape_json_string(raw)
        buf.append(ch)
        i += 1
    if not allow_partial:
        return ""
    raw = "".join(buf)
    return _unescape_json_string(raw) if raw else ""


_SECTION_HEADER_RE = re.compile(
    r'\{\s*"sectionId"\s*:\s*"([^"]+)"\s*,\s*'
    r'(?:"title"\s*:\s*"((?:\\.|[^"\\])*)"\s*,\s*)?'
    r'"content"\s*:\s*"',
    re.DOTALL,
)


def _salvage_sections_payload(text: str) -> dict[str, Any] | None:
    """Recover section objects from truncated Phase 3 JSON (title field + partial content)."""
    matches = list(_SECTION_HEADER_RE.finditer(text))
    if not matches:
        return None

    sections: list[dict[str, Any]] = []
    for idx, match in enumerate(matches):
        section_id = match.group(1)
        content_start = match.end()
        next_start = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        chunk = text[content_start:next_start]
        is_last = idx == len(matches) - 1
        content = _extract_json_string_value(chunk, allow_partial=is_last)
        if section_id and content.strip() and len(content.strip()) > 40:
            entry: dict[str, Any] = {"sectionId": section_id, "content": content}
            title = match.group(2)
            if title:
                entry["title"] = _unescape_json_string(title)
            sections.append(entry)

    if sections:
        return {"sections": sections}
    return None


def _salvage_simple_content_payload(text: str) -> dict[str, Any] | None:
    """Recover content field from a single-section payload if LLM JSON is truncated/invalid."""
    match = re.search(r'"content"\s*:\s*"', text)
    if not match:
        return None
    content_start = match.end()
    chunk = text[content_start:]
    content = _extract_json_string_value(chunk, allow_partial=True)
    if content.strip():
        # Clean up any trailing closed quotes, braces, brackets
        clean_content = content.rstrip('}" \t\n\r')
        return {"content": clean_content}
    return None


def _salvage_recommendations_payload(text: str) -> dict[str, Any] | None:
    """Recover complete recommendation objects from a truncated editorial-review JSON."""
    start = text.find('"recommendations"')
    if start == -1:
        return None
    bracket = text.find("[", start)
    if bracket == -1:
        return None

    recs: list[dict[str, Any]] = []
    i = bracket + 1
    n = len(text)
    while i < n:
        while i < n and text[i] in " \t\r\n,":
            i += 1
        if i >= n or text[i] == "]":
            break
        if text[i] != "{":
            i += 1
            continue
        depth = 0
        in_str = False
        escape = False
        obj_start = i
        j = i
        closed = False
        while j < n:
            ch = text[j]
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = not in_str
            elif not in_str:
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        closed = True
                        j += 1
                        break
            j += 1
        if not closed:
            break
        try:
            recs.append(json.loads(text[obj_start:j]))
        except json.JSONDecodeError:
            pass
        i = j

    if recs:
        return {"recommendations": recs}
    return None


def _parse_json_response(raw: str) -> dict[str, Any]:
    # First try to extract JSON from any surrounding text
    text = _extract_json_from_text(raw)
    parsed = _try_parse_json_object(text)
    if parsed is None:
        for salvager, label in (
            (_salvage_sections_payload, "section(s)"),
            (_salvage_recommendations_payload, "recommendation(s)"),
            (_salvage_simple_content_payload, "simple content"),
            (_salvage_budget_payload, "budget field(s)"),
        ):
            salvaged = salvager(text)
            if salvaged:
                count = len(
                    salvaged.get("sections")
                    or salvaged.get("recommendations")
                    or salvaged.get("lineItems")
                    or [1]
                )
                logger.warning(
                    "Salvaged %d %s from truncated LLM JSON",
                    count,
                    label,
                )
                return salvaged
        raise LlmError(f"LLM returned invalid JSON: {raw[:200]}")

    parsed = _unwrap_nested_json(parsed)
    if "sections" not in parsed and "lineItems" not in parsed:
        for salvager, label in (
            (_salvage_sections_payload, "section(s)"),
            (_salvage_budget_payload, "budget field(s)"),
        ):
            salvaged = salvager(text)
            if salvaged:
                count = len(salvaged.get("sections") or salvaged.get("lineItems") or [1])
                logger.warning(
                    "Salvaged %d %s after unwrap — missing expected keys",
                    count,
                    label,
                )
                return salvaged

    return parsed
