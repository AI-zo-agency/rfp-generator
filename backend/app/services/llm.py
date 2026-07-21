import asyncio
import json
import logging
import re
from typing import Any, Literal

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

LlmTier = Literal["heavy", "light"]

# Process-local: once Fireworks returns 412, never call it again this process.
_FIREWORKS_SUSPENDED = False


def resolve_llm_model(tier: LlmTier = "heavy") -> str:
    """OpenRouter model id for heavy (Sonnet) vs light (Haiku) tiers."""
    heavy = (settings.llm_heavy_model or settings.openrouter_model or "").strip()
    light = (settings.llm_light_model or "").strip()
    if tier == "light" and light:
        return light
    return heavy or settings.openrouter_model


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
    # Anthropic via OpenRouter often ignores response_format and emits ```json fences,
    # then stops mid-object. Prompt + salvage is more reliable than json_object mode.
    model_l = (model or "").lower()
    effective_json_mode = json_mode and not (
        "anthropic" in model_l or "claude" in model_l
    )
    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }
    if effective_json_mode:
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
            choice0 = data["choices"][0]
            content = choice0["message"]["content"]
            finish_reason = str(choice0.get("finish_reason") or "")
        except (KeyError, IndexError, TypeError) as exc:
            raise LlmError(f"{provider} returned an unexpected response shape") from exc

        if not isinstance(content, str) or not content.strip():
            raise LlmError(f"{provider} returned empty content")

        usage = data.get("usage") or {}
        logger.info(
            "LLM success: provider=%s model=%s response_chars=%d finish_reason=%s usage=%s",
            provider,
            model,
            len(content),
            finish_reason or "?",
            usage if usage else "{}",
        )
        if finish_reason in {"length", "max_tokens"}:
            logger.warning(
                "LLM hit output token limit: provider=%s model=%s finish_reason=%s chars=%d",
                provider,
                model,
                finish_reason,
                len(content),
            )

        # Check if response looks truncated (suspiciously short for a JSON response)
        if len(content) < 30 and '"content":' in content:
            logger.warning(
                "LLM response appears truncated: provider=%s model=%s chars=%d content=%s",
                provider,
                model,
                len(content),
                content[:200],
            )
            raise LlmError(f"{provider} returned truncated response (only {len(content)} chars)")

        return content.strip()

    if last_error:
        raise last_error
    raise LlmError(f"{provider} request failed after retries", status_code=429)


async def chat_json(
    messages: list[dict[str, str]],
    *,
    max_tokens: int | None = None,
    temperature: float = 0.2,
    tier: LlmTier = "heavy",
) -> tuple[dict[str, Any], str]:
    global _FIREWORKS_SUSPENDED
    errors: list[str] = []
    openrouter_model = resolve_llm_model(tier)
    skip_fireworks_fallback = False

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
                model=openrouter_model,
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
            # Invalid/truncated JSON already consumed tokens — do not re-run on Fireworks.
            msg = str(exc).lower()
            if "invalid json" in msg or "truncated" in msg:
                skip_fireworks_fallback = True
                logger.info(
                    "Skipping Fireworks fallback after OpenRouter JSON failure (avoid duplicate spend)"
                )

    fireworks_key = _fireworks_key()
    if fireworks_key and not _FIREWORKS_SUSPENDED and not skip_fireworks_fallback:
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
                _FIREWORKS_SUSPENDED = True
                logger.warning("Fireworks account suspended - skipping for future calls")
            else:
                errors.append(str(exc))
    elif _FIREWORKS_SUSPENDED:
        logger.debug("Fireworks skipped (account previously suspended)")

    if not errors:
        raise LlmError(
            "No LLM API key configured. Set OPENROUTER_API_KEY (primary) or FIREWORKS_API_KEY (fallback).",
            status_code=503,
        )

    raise LlmError(
        "All configured LLM providers failed: " + "; ".join(errors),
        status_code=502,
    )


async def chat_json_soft(
    messages: list[dict[str, str]],
    *,
    max_tokens: int | None = None,
    temperature: float = 0.2,
    tier: LlmTier = "heavy",
) -> tuple[dict[str, Any], str]:
    """One LLM JSON call. On failure return ({}, \"failed\") — never retry, never raise."""
    try:
        return await chat_json(
            messages,
            max_tokens=max_tokens,
            temperature=temperature,
            tier=tier,
        )
    except LlmError as exc:
        logger.warning("chat_json_soft: %s", str(exc)[:220])
        return {}, "failed"


async def chat_text(
    messages: list[dict[str, str]],
    *,
    max_tokens: int | None = None,
    temperature: float = 0.2,
    tier: LlmTier = "heavy",
) -> tuple[str, str]:
    """Plain-text chat completion (no JSON response format)."""
    global _FIREWORKS_SUSPENDED
    errors: list[str] = []
    openrouter_model = resolve_llm_model(tier)

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
                model=openrouter_model,
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
    if fireworks_key and not _FIREWORKS_SUSPENDED:
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
            if exc.status_code == 412:
                _FIREWORKS_SUSPENDED = True
                logger.warning("Fireworks account suspended - skipping for future calls")
            elif exc.status_code != 412:
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
    # Always drop a trailing fence — common after slicing from the first `{`.
    stripped = re.sub(r"\s*```(?:\w*)?\s*$", "", stripped)
    return stripped.strip()


def _extract_json_from_text(text: str) -> str:
    """Extract JSON from text that may have explanatory prefixes or markdown formatting."""
    # Strip fences FIRST. If we slice from `{` before stripping, a trailing ``` remains
    # and json.loads fails on otherwise-valid Claude responses.
    text = _strip_code_fence(text.strip())

    brace_start = text.find("{")
    bracket_start = text.find("[")

    if brace_start >= 0 and (bracket_start < 0 or brace_start < bracket_start):
        text = text[brace_start:]
    elif bracket_start >= 0:
        text = text[bracket_start:]

    return _strip_code_fence(text)


def _close_truncated_json(text: str) -> str:
    """Close truncated JSON by finishing open strings and LIFO-closing braces/brackets."""
    s = text.strip()
    in_string = False
    escape = False
    stack: list[str] = []
    for ch in s:
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch in "{[":
            stack.append(ch)
        elif ch == "}" and stack and stack[-1] == "{":
            stack.pop()
        elif ch == "]" and stack and stack[-1] == "[":
            stack.pop()
    if in_string:
        s += '"'
    s = s.rstrip().rstrip(",")
    # Drop trailing incomplete `"key":` with no value — common Claude stop mid-object.
    # Do NOT strip complete `"key": 123` / null / true / false (those are valid).
    while True:
        cleaned = re.sub(r',?\s*"[^"\\]+"\s*:\s*$', "", s)
        if cleaned == s:
            break
        s = cleaned.rstrip().rstrip(",")

    # Re-scan stack after incomplete-key stripping
    in_string = False
    escape = False
    stack = []
    for ch in s:
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch in "{[":
            stack.append(ch)
        elif ch == "}" and stack and stack[-1] == "{":
            stack.pop()
        elif ch == "]" and stack and stack[-1] == "[":
            stack.pop()
    if in_string:
        s += '"'
    s = s.rstrip().rstrip(",")
    for opener in reversed(stack):
        s += "}" if opener == "{" else "]"
    return s


def _salvage_manuscript_locks_payload(text: str) -> dict[str, Any] | None:
    """Recover primaryContact* fields when locks JSON truncates mid-string."""
    if "primaryContactName" not in text:
        return None
    payload: dict[str, Any] = {}

    def _str_field(key: str) -> str | None:
        m = re.search(rf'"{key}"\s*:\s*"((?:\\.|[^"\\])*)"', text)
        if m:
            try:
                return json.loads(f'"{m.group(1)}"')
            except json.JSONDecodeError:
                return m.group(1)
        # Truncated open string
        m = re.search(rf'"{key}"\s*:\s*"([^"]*)$', text, re.M)
        if m:
            return m.group(1).rstrip()
        return None

    for key in (
        "primaryContactName",
        "primaryContactTitle",
        "primaryContactRole",
        "executiveSponsorName",
        "decisionRationale",
    ):
        val = _str_field(key)
        if val is not None:
            payload[key] = val

    bool_m = re.search(r'"needsHumanConfirm"\s*:\s*(true|false)', text, re.I)
    if bool_m:
        payload["needsHumanConfirm"] = bool_m.group(1).lower() == "true"

    kpis: list[str] = []
    for m in re.finditer(r'"requiredKpis"\s*:\s*\[(.*?)(?:\]|$)', text, re.S):
        for item in re.finditer(r'"((?:\\.|[^"\\])*)"', m.group(1)):
            try:
                kpis.append(json.loads(f'"{item.group(1)}"'))
            except json.JSONDecodeError:
                kpis.append(item.group(1))
    if kpis:
        payload["requiredKpis"] = kpis

    if payload.get("primaryContactName"):
        return payload
    return None


def _salvage_classification_payload(text: str) -> dict[str, Any] | None:
    """Recover ProposalContext-style fields when classification JSON truncates."""
    if '"industry"' not in text and '"servicesRequested"' not in text:
        return None
    payload: dict[str, Any] = {}

    def _str_field(key: str) -> str | None:
        m = re.search(rf'"{key}"\s*:\s*"((?:\\.|[^"\\])*)"', text)
        if m:
            try:
                return json.loads(f'"{m.group(1)}"')
            except json.JSONDecodeError:
                return m.group(1)
        m = re.search(rf'"{key}"\s*:\s*"([^"]*)$', text, re.M)
        if m:
            return m.group(1).rstrip()
        return None

    for key in ("industry", "buyerType", "projectComplexity", "proposalType", "summary"):
        val = _str_field(key)
        if val is not None:
            payload[key] = val

    for key in ("servicesRequested", "evaluationPriorities"):
        m = re.search(rf'"{key}"\s*:\s*\[(.*?)(?:\]|$)', text, re.S)
        if not m:
            continue
        items: list[str] = []
        for item in re.finditer(r'"((?:\\.|[^"\\])*)"', m.group(1)):
            try:
                items.append(json.loads(f'"{item.group(1)}"'))
            except json.JSONDecodeError:
                items.append(item.group(1))
        # Truncated last string without closing quote
        tail = re.search(r',\s*"([^"]+)$', m.group(1))
        if not items and tail:
            items.append(tail.group(1).rstrip())
        elif tail and (not m.group(1).rstrip().endswith('"')):
            items.append(tail.group(1).rstrip())
        if items:
            payload[key] = items

    if payload.get("industry") or payload.get("servicesRequested"):
        return payload
    return None


def _salvage_capability_tiers_payload(text: str) -> dict[str, Any] | None:
    """Recover primary/secondary/omit capability tiers from truncated ranking JSON."""
    if '"primary"' not in text and '"secondary"' not in text and '"omit"' not in text:
        return None

    def _tier_items(key: str) -> list[dict[str, str]]:
        m = re.search(rf'"{key}"\s*:\s*\[(.*?)(?:\]\s*,|\]\s*}}|$)', text, re.S)
        if not m:
            return []
        chunk = m.group(1)
        items: list[dict[str, str]] = []
        for obj in re.finditer(
            r'\{\s*"capability"\s*:\s*"((?:\\.|[^"\\])*)"\s*,\s*"rationale"\s*:\s*"((?:\\.|[^"\\])*)"',
            chunk,
        ):
            try:
                cap = json.loads(f'"{obj.group(1)}"')
                rat = json.loads(f'"{obj.group(2)}"')
            except json.JSONDecodeError:
                cap, rat = obj.group(1), obj.group(2)
            items.append({"capability": cap, "rationale": rat})
        # Truncated last object with open rationale string
        tail = re.search(
            r'\{\s*"capability"\s*:\s*"((?:\\.|[^"\\])*)"\s*,\s*"rationale"\s*:\s*"([^"]*)$',
            chunk,
            re.M,
        )
        if tail:
            try:
                cap = json.loads(f'"{tail.group(1)}"')
            except json.JSONDecodeError:
                cap = tail.group(1)
            items.append({"capability": cap, "rationale": tail.group(2).rstrip()[:80]})
        return items

    payload = {
        "primary": _tier_items("primary"),
        "secondary": _tier_items("secondary"),
        "omit": _tier_items("omit"),
    }
    if payload["primary"] or payload["secondary"] or payload["omit"]:
        return payload
    return None


def _salvage_company_truth_payload(text: str) -> dict[str, Any] | None:
    """Recover CompanyTruth top-level fields when extraction JSON truncates."""
    if '"legalName"' not in text and '"dba"' not in text:
        return None
    payload: dict[str, Any] = {}

    def _str_field(key: str) -> str | None:
        m = re.search(rf'"{key}"\s*:\s*"((?:\\.|[^"\\])*)"', text)
        if m:
            try:
                return json.loads(f'"{m.group(1)}"')
            except json.JSONDecodeError:
                return m.group(1)
        m = re.search(rf'"{key}"\s*:\s*"([^"]*)$', text, re.M)
        if m:
            return m.group(1).rstrip()
        return None

    for key in (
        "legalName",
        "dba",
        "founded",
        "ownership",
        "employeeCount",
    ):
        val = _str_field(key)
        if val is not None:
            payload[key] = val

    years = re.search(r'"yearsInOperation"\s*:\s*(\d+)', text)
    if years:
        payload["yearsInOperation"] = int(years.group(1))

    # Nested simple objects: locations / contact — take closed string fields only
    for obj_key in ("locations", "contact"):
        m = re.search(rf'"{obj_key}"\s*:\s*\{{([^}}]*)\}}', text, re.S)
        if not m:
            continue
        nested: dict[str, Any] = {}
        for fm in re.finditer(r'"(\w+)"\s*:\s*(?:"((?:\\.|[^"\\])*)"|null)', m.group(1)):
            nested[fm.group(1)] = None if fm.group(2) is None and "null" in fm.group(0) else fm.group(2)
        if nested:
            payload[obj_key] = nested

    caps: list[str] = []
    cm = re.search(r'"capabilities"\s*:\s*\[(.*?)(?:\]|$)', text, re.S)
    if cm:
        for item in re.finditer(r'"((?:\\.|[^"\\])*)"', cm.group(1)):
            try:
                caps.append(json.loads(f'"{item.group(1)}"'))
            except json.JSONDecodeError:
                caps.append(item.group(1))
        if caps:
            payload["capabilities"] = caps

    if payload.get("legalName") or payload.get("dba") or payload.get("founded"):
        return payload
    return None


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
        payload: dict[str, Any] = {"content": clean_content}
        id_m = re.search(r'"id"\s*:\s*"((?:\\.|[^"\\])*)"', text)
        title_m = re.search(r'"title"\s*:\s*"((?:\\.|[^"\\])*)"', text)
        if id_m:
            payload["id"] = _unescape_json_string(id_m.group(1))
        if title_m:
            payload["title"] = _unescape_json_string(title_m.group(1))
        return payload
    return None


def _salvage_section1_budgets_payload(text: str) -> dict[str, Any] | None:
    """Recover Section 1 content-budget objects from truncated planning JSON."""
    if '"budgets"' not in text:
        return None
    items: list[dict[str, Any]] = []
    for m in re.finditer(
        r'\{\s*"sectionId"\s*:\s*"((?:\\.|[^"\\])*)"\s*,\s*"title"\s*:\s*"((?:\\.|[^"\\])*)"'
        r'\s*,\s*"format"\s*:\s*"((?:\\.|[^"\\])*)"',
        text,
    ):
        entry: dict[str, Any] = {
            "sectionId": _unescape_json_string(m.group(1)),
            "title": _unescape_json_string(m.group(2)),
            "format": _unescape_json_string(m.group(3)),
        }
        # Pull nearby wordMin/wordMax after this object start if present before next `{`
        window = text[m.start() : m.start() + 400]
        wmin = re.search(r'"wordMin"\s*:\s*(\d+|null)', window)
        wmax = re.search(r'"wordMax"\s*:\s*(\d+|null)', window)
        if wmin:
            entry["wordMin"] = None if wmin.group(1) == "null" else int(wmin.group(1))
        if wmax:
            entry["wordMax"] = None if wmax.group(1) == "null" else int(wmax.group(1))
        items.append(entry)
    if items:
        return {"budgets": items}
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
    # Normalize fancy quotes that break json.loads
    text = (
        text.replace("\u201c", '"')
        .replace("\u201d", '"')
        .replace("\u2018", "'")
        .replace("\u2019", "'")
    )
    parsed = _try_parse_json_object(text)
    if parsed is None:
        for salvager, label in (
            (_salvage_manuscript_locks_payload, "manuscript lock field(s)"),
            (_salvage_classification_payload, "classification field(s)"),
            (_salvage_capability_tiers_payload, "capability tier(s)"),
            (_salvage_section1_budgets_payload, "section-1 budget(s)"),
            (_salvage_company_truth_payload, "company truth field(s)"),
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
                    or salvaged.get("budgets")
                    or salvaged.get("primary")
                    or salvaged.get("capabilities")
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
