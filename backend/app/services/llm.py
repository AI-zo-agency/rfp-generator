import asyncio
import json
import logging
import random
import re
import time
from email.utils import parsedate_to_datetime
from collections.abc import Sequence
from typing import Any, Literal

import httpx

from app.core.config import settings
from app.services.llm_call_context import (
    get_llm_node_name,
    get_llm_rfp_id,
    get_llm_run_id,
)
from app.services.llm_routing import resolve_fireworks_eligibility
from app.services.llm_pricing import (
    estimate_cost_usd,
    estimate_tokens_from_chars,
    split_cached_input_tokens,
)
from app.services.llm_prompt_cache import (
    apply_cache_control,
    inline_cache_prefix,
    message_char_count,
)

try:
    from langsmith import traceable as _langsmith_traceable
except ImportError:  # pragma: no cover

    def _langsmith_traceable(*_args: Any, **_kwargs: Any):  # type: ignore[misc]
        def _decorator(fn: Any) -> Any:
            return fn

        return _decorator

logger = logging.getLogger(__name__)

LlmTier = Literal["heavy", "light"]

# content, usage dict (prompt_tokens / completion_tokens / estimated)
_LlmPostResult = tuple[str, dict[str, Any]]

# Process-local: once Fireworks returns 412, never call it again this process.
_FIREWORKS_SUSPENDED = False


def _resolve_run_cost_cap_usd(node_name: str | None) -> float:
    """Return per-run hard cost cap for guarded pipeline phases; 0 = disabled."""
    try:
        from app.core.step_debug_logger import get_pipeline_phase

        phase = (get_pipeline_phase() or "").strip().lower()
    except Exception:  # noqa: BLE001
        phase = ""
    node = (node_name or get_llm_node_name() or "").strip().lower()

    # Complete Scan budget cap.
    if phase == "fulfill-scan" or "fulfill-scan" in node or "fulfill_scan" in node:
        return float(getattr(settings, "complete_scan_max_cost_usd", 0.0) or 0.0)

    # Generate proposal budget cap (pipeline + drafting phases).
    if (
        phase in {"pipeline", "sections-1-3", "phase-2", "phase-3", "phase-3-5", "phase-3-6"}
        or "phase-" in node
        or "generate" in node
        or "sections-1-3" in node
        or "proposal_generator" in node
    ):
        return float(getattr(settings, "generate_proposal_max_cost_usd", 0.0) or 0.0)
    return 0.0


def _enforce_run_cost_cap(node_name: str | None, run_id: str | None) -> None:
    """Hard stop when the current run already exceeded the configured budget."""
    cap = _resolve_run_cost_cap_usd(node_name)
    if cap <= 0:
        return
    resolved_run = (run_id if run_id is not None else get_llm_run_id()).strip()
    if not resolved_run:
        return
    try:
        from app.services.llm_call_log import get_run_total_cost_usd

        spent = float(get_run_total_cost_usd(resolved_run))
    except Exception:  # noqa: BLE001
        return
    if spent >= cap:
        raise LlmError(
            (
                f"LLM run budget exceeded: ${spent:.2f} spent (cap ${cap:.2f}). "
                "Stop this run and continue with targeted/manual edits."
            ),
            status_code=429,
        )


def _redact_langsmith_llm_inputs(inputs: dict[str, Any]) -> dict[str, Any]:
    """Never send API keys / bearer tokens to LangSmith."""
    redacted = dict(inputs)
    if "api_key" in redacted and redacted["api_key"]:
        redacted["api_key"] = "***"
    headers = redacted.get("extra_headers")
    if isinstance(headers, dict):
        safe = dict(headers)
        for key in list(safe):
            if "auth" in key.lower() or "key" in key.lower():
                safe[key] = "***"
        redacted["extra_headers"] = safe
    return redacted


def _provider_routing(
    *,
    max_tokens: int | None,
    node_name: str | None,
) -> tuple[str, bool, bool, bool]:
    """Return (resolved_node, skip_gemini_for_prefer, skip_openrouter_for_prefer, allow_fireworks).

    Raises LlmError when the request exceeds Fireworks' cap and no alternative exists.
    """
    resolved_node = node_name if node_name is not None else get_llm_node_name()
    openrouter_available = bool(_openrouter_key())
    gemini_key = settings.gemini_api_key.strip()
    gemini_available = bool(gemini_key and not _is_placeholder_key(gemini_key))
    decision = resolve_fireworks_eligibility(
        requested_max_tokens=max_tokens,
        prefer_fireworks=settings.llm_prefer_fireworks,
        node_name=resolved_node,
        openrouter_available=openrouter_available,
        gemini_available=gemini_available,
        disable_fireworks=settings.llm_disable_fireworks,
    )
    if decision.must_raise:
        raise LlmError(
            decision.block_reason
            or (
                "Requested max_tokens exceeds Fireworks output cap and no "
                "alternative provider is configured"
            ),
            status_code=503,
        )
    if decision.block_reason:
        logger.info("LLM routing: %s (node=%s)", decision.block_reason, resolved_node or "unknown")

    # Prefer-Fireworks only when eligibility does not force skip (quality-critical / over-cap).
    prefer_fw = settings.llm_prefer_fireworks and not decision.skip_prefer_fireworks
    skip_gemini = settings.llm_prefer_openrouter or prefer_fw
    skip_openrouter = prefer_fw
    return resolved_node, skip_gemini, skip_openrouter, decision.allow_fireworks


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
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SECONDS) as client:
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
    
    usage_meta = data.get("usageMetadata") or data.get("usage_metadata") or {}
    prompt_tokens = int(
        usage_meta.get("promptTokenCount")
        or usage_meta.get("prompt_token_count")
        or 0
    )
    completion_tokens = int(
        usage_meta.get("candidatesTokenCount")
        or usage_meta.get("candidates_token_count")
        or 0
    )
    estimated = False
    if prompt_tokens <= 0 and completion_tokens <= 0:
        # Estimate when Gemini omits usageMetadata.
        msg_chars = sum(len(m.get("content") or "") for m in messages)
        prompt_tokens = estimate_tokens_from_chars(msg_chars)
        completion_tokens = estimate_tokens_from_chars(len(content))
        estimated = True
    logger.info(
        "LLM success: provider=Gemini model=%s response_chars=%d usage=%s estimated=%s",
        model,
        len(content),
        {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
        estimated,
    )
    return content.strip(), {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "estimated": estimated,
    }


# 429 retry: honor Retry-After when present; else exponential backoff + jitter.
_RATE_LIMIT_MAX_WAIT_S = 60.0
_RATE_LIMIT_JITTER_FRAC = 0.3


def _parse_retry_after_seconds(response: httpx.Response) -> float | None:
    """Extract wait seconds from Retry-After header or JSON body, if present.

    Fireworks does not document Retry-After as guaranteed; honor it when sent.
    Accepts delay-seconds or HTTP-date header values, plus common JSON fields.
    """
    header = (response.headers.get("Retry-After") or "").strip()
    if header:
        try:
            return max(0.0, float(header))
        except ValueError:
            try:
                dt = parsedate_to_datetime(header)
                if dt is not None:
                    return max(0.0, dt.timestamp() - time.time())
            except (TypeError, ValueError, OverflowError, OSError):
                pass

    try:
        payload = response.json()
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None

    candidates: list[Any] = [
        payload.get("retry_after"),
        payload.get("retryAfter"),
    ]
    err = payload.get("error")
    if isinstance(err, dict):
        candidates.extend([err.get("retry_after"), err.get("retryAfter")])

    for raw in candidates:
        if raw is None:
            continue
        try:
            return max(0.0, float(raw))
        except (TypeError, ValueError):
            continue
    return None


def _rate_limit_wait_seconds(attempt: int, response: httpx.Response) -> tuple[float, str]:
    """Return (wait_seconds, source) for a 429 retry.

    attempt is 0-based within the retry loop (0 = first retry after initial fail).
    Base schedule without Retry-After: 2s, 4s, 8s + up to 30% jitter, capped at 60s.
    """
    retry_after = _parse_retry_after_seconds(response)
    if retry_after is not None:
        # Tiny jitter so parallel callers don't align even when the server
        # returns the same Retry-After.
        wait = min(_RATE_LIMIT_MAX_WAIT_S, retry_after + random.uniform(0.0, 0.5))
        return wait, "retry_after"

    base = float(2 ** (attempt + 1))
    jitter = random.uniform(0.0, base * _RATE_LIMIT_JITTER_FRAC)
    wait = min(_RATE_LIMIT_MAX_WAIT_S, base + jitter)
    return wait, "exponential_backoff"


#: Read timeout for a single LLM HTTP call.
_HTTP_TIMEOUT_SECONDS = 180.0

#: Long-output calls need real headroom: the Stage 3 budget pass asks for 8192
#: tokens against a ~28k-char prompt on a heavy model, and was timing out at 180s
#: purely because generation had not finished. That is slow, not broken.
_HTTP_TIMEOUT_LONG_SECONDS = 300.0
_LONG_OUTPUT_TOKEN_THRESHOLD = 4096


def _http_timeout_for(max_tokens: int | None) -> float:
    """Read timeout scaled to how much output the call asks for."""
    if max_tokens is not None and max_tokens >= _LONG_OUTPUT_TOKEN_THRESHOLD:
        return _HTTP_TIMEOUT_LONG_SECONDS
    return _HTTP_TIMEOUT_SECONDS

#: Retries for network-level failures. Kept low because each attempt can already
#: cost the full read timeout — the point is to reach the provider fallback chain
#: quickly, not to keep hammering one slow endpoint.
_MAX_TRANSPORT_RETRIES = 1
_TRANSPORT_RETRY_BACKOFF_SECONDS = 2.0


@_langsmith_traceable(
    name="llm.post_chat",
    run_type="llm",
    process_inputs=_redact_langsmith_llm_inputs,
)
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
    cache_prefix: str | Sequence[str] | None = None,
    ttl_1h: bool | None = None,
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
    cached_messages = apply_cache_control(
        messages,
        model=model,
        cache_prefix=cache_prefix,
        ttl_1h=settings.llm_cache_ttl_1h if ttl_1h is None else ttl_1h,
        enabled=not settings.llm_disable_prompt_cache,
    )
    body: dict[str, Any] = {
        "model": model,
        "messages": cached_messages,
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

    transport_failures = 0
    for attempt in range(4):

        timeout_s = _http_timeout_for(max_tokens)

        async def _post() -> httpx.Response:
            async with httpx.AsyncClient(timeout=timeout_s) as client:
                return await client.post(url, headers=headers, json=body)

        try:
            response = await run_with_generation_cancel(_post)
        except httpx.HTTPError as exc:
            # Network-level failures (read timeout, connect error, protocol error)
            # used to propagate raw out of this function. Every layer of resilience
            # in this codebase catches LlmError only, so a single slow response
            # bypassed ALL of them at once: this retry loop, the
            # Gemini -> OpenRouter -> Fireworks fallback chain in chat_json, and each
            # caller's own recovery (e.g. the budget's compact-output retry). One
            # timeout killed a whole pipeline phase.
            #
            # ProposalGenerationCancelled is a separate class, so pressing Stop is
            # still honoured rather than being retried.
            transport_failures += 1
            last_error = LlmError(
                f"{provider} request failed: {exc.__class__.__name__}: {str(exc)[:200]}"
            )
            if transport_failures <= _MAX_TRANSPORT_RETRIES and attempt < 3:
                wait_s = _TRANSPORT_RETRY_BACKOFF_SECONDS * transport_failures
                logger.warning(
                    "LLM transport error (%s): %s — retrying in %.1fs (attempt %d)",
                    provider,
                    exc.__class__.__name__,
                    wait_s,
                    transport_failures,
                )
                await asyncio.sleep(wait_s)
                continue
            logger.warning(
                "LLM transport error (%s) exhausted retries: %s — falling back",
                provider,
                exc.__class__.__name__,
            )
            break

        if response.status_code == 429 and attempt < 3:
            wait_s, wait_source = _rate_limit_wait_seconds(attempt, response)
            logger.warning(
                "LLM rate limited (%s), retrying in %.1fs (attempt %d/3, source=%s)",
                provider,
                wait_s,
                attempt + 1,
                wait_source,
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
        prompt_tokens, cache_write_tokens, cache_read_tokens = (
            split_cached_input_tokens(usage)
        )
        completion_tokens = int(
            usage.get("completion_tokens") or usage.get("output_tokens") or 0
        )
        estimated = False
        if (
            prompt_tokens <= 0
            and completion_tokens <= 0
            and cache_write_tokens <= 0
            and cache_read_tokens <= 0
        ):
            msg_chars = sum(message_char_count(m) for m in cached_messages)
            prompt_tokens = estimate_tokens_from_chars(msg_chars)
            completion_tokens = estimate_tokens_from_chars(len(content))
            estimated = True
        logger.info(
            "LLM success: provider=%s model=%s response_chars=%d finish_reason=%s usage=%s estimated=%s",
            provider,
            model,
            len(content),
            finish_reason or "?",
            usage if usage else "{}",
            estimated,
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

        return content.strip(), {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "cache_creation_input_tokens": cache_write_tokens,
            "cache_read_input_tokens": cache_read_tokens,
            "estimated": estimated,
        }

    if last_error:
        raise last_error
    raise LlmError(f"{provider} request failed after retries", status_code=429)


def _record_successful_call(
    *,
    model: str,
    tier: LlmTier,
    provider: str,
    usage: dict[str, Any],
    latency_ms: int,
    node_name: str | None,
    rfp_id: str | None,
    run_id: str | None,
) -> None:
    """Persist cost/token row — never raises."""
    try:
        from app.core.step_debug_logger import (
            get_pipeline_rfp_id,
            get_pipeline_run_id,
            resolve_pipeline_node_name,
        )
        from app.services.llm_call_log import record_llm_call
        from app.services.proposal_generation_cancel import get_active_rfp_id

        resolved_node = resolve_pipeline_node_name(
            node_name if node_name is not None else get_llm_node_name()
        )
        resolved_rfp = (
            (rfp_id if rfp_id is not None else get_llm_rfp_id())
            or get_pipeline_rfp_id()
            or get_active_rfp_id()
            or ""
        )
        resolved_run = (
            (run_id if run_id is not None else get_llm_run_id())
            or get_pipeline_run_id()
            or "unknown"
        )

        inp = int(usage.get("prompt_tokens") or 0)
        out = int(usage.get("completion_tokens") or 0)
        cache_write = int(usage.get("cache_creation_input_tokens") or 0)
        cache_read = int(usage.get("cache_read_input_tokens") or 0)
        estimated = bool(usage.get("estimated"))
        cost = estimate_cost_usd(
            model=model,
            input_tokens=inp,
            output_tokens=out,
            cache_creation_input_tokens=cache_write,
            cache_read_input_tokens=cache_read,
            cache_ttl_1h=settings.llm_cache_ttl_1h,
        )
        record_llm_call(
            run_id=resolved_run,
            rfp_id=resolved_rfp,
            node_name=resolved_node,
            model=model,
            tier=tier,
            provider=provider,
            input_tokens=inp,
            output_tokens=out,
            cost_usd=cost,
            latency_ms=latency_ms,
            tokens_estimated=estimated,
            cache_creation_input_tokens=cache_write,
            cache_read_input_tokens=cache_read,
        )
        logger.info(
            "LLM cost: node=%s model=%s tier=%s in=%d out=%d cache_w=%d cache_r=%d "
            "cost_usd=%.6f latency_ms=%d estimated=%s",
            node_name if node_name is not None else get_llm_node_name() or resolved_node,
            model,
            tier,
            inp,
            out,
            cache_write,
            cache_read,
            cost,
            latency_ms,
            estimated,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("LLM cost record failed (non-fatal): %s", str(exc)[:200])


#: Utility calls that emit queries or JSON structure, never prose about the
#: agency — corrections have nothing to correct there, so they are skipped.
CORRECTIONS_EXEMPT_NODES: frozenset[str] = frozenset({
    "go_no_go_opportunity_classifier",
    "go_no_go_evidence_query_plan",
    "chat_manuscript_intent",
    "manual_fill_triage",
    "query_planner",
    "ledger_add_query_planner",
    "retrieval_query_planner_batch",
})

_CORRECTIONS_MARKER = "## STANDING CORRECTIONS"


async def apply_standing_corrections(
    messages: list[dict[str, str]],
    *,
    node_name: str | None,
    include_corrections: bool,
) -> list[dict[str, str]]:
    """Append the standing-corrections block to `messages`, without mutating the input.

    This is the single choke point for corrections: every model call in the
    backend passes through `chat_json`/`chat_text`, which call this first, so
    no knowledge-base path can answer from a superseded fact.
    """
    if not include_corrections:
        return messages
    if node_name in CORRECTIONS_EXEMPT_NODES:
        return messages
    if any(_CORRECTIONS_MARKER in (m.get("content") or "") for m in messages):
        return messages

    import app.services.kb_corrections as kb_corrections

    block = await kb_corrections.corrections_prompt_block()
    if not block:
        return messages

    new_messages = [dict(m) for m in messages]
    for m in new_messages:
        if m.get("role") == "system":
            m["content"] = f"{m.get('content') or ''}\n\n{block}"
            return new_messages

    new_messages.insert(0, {"role": "system", "content": block})
    return new_messages


@_langsmith_traceable(name="llm.chat_json", run_type="chain")
async def chat_json(
    messages: list[dict[str, str]],
    *,
    max_tokens: int | None = None,
    temperature: float = 0.2,
    tier: LlmTier = "heavy",
    node_name: str | None = None,
    rfp_id: str | None = None,
    run_id: str | None = None,
    cache_prefix: str | Sequence[str] | None = None,
    include_corrections: bool = True,
) -> tuple[dict[str, Any], str]:
    messages = await apply_standing_corrections(
        messages, node_name=node_name, include_corrections=include_corrections
    )
    global _FIREWORKS_SUSPENDED
    errors: list[str] = []
    openrouter_model = resolve_llm_model(tier)
    skip_fireworks_fallback = False
    started = time.perf_counter()

    _resolved_node, skip_gemini, skip_openrouter, allow_fireworks = _provider_routing(
        max_tokens=max_tokens,
        node_name=node_name,
    )
    if node_name is None:
        node_name = _resolved_node or None
    _enforce_run_cost_cap(node_name, run_id)

    # Try Gemini first if API key is configured and not skipped by preferences
    gemini_key = settings.gemini_api_key.strip()
    if gemini_key and not _is_placeholder_key(gemini_key) and not skip_gemini:
        try:
            raw, usage = await _post_gemini_chat(
                api_key=gemini_key,
                model=settings.gemini_model,
                messages=inline_cache_prefix(messages, cache_prefix),
                max_tokens=max_tokens,
                temperature=temperature,
            )
            _record_successful_call(
                model=settings.gemini_model,
                tier=tier,
                provider="gemini",
                usage=usage,
                latency_ms=int((time.perf_counter() - started) * 1000),
                node_name=node_name,
                rfp_id=rfp_id,
                run_id=run_id,
            )
            return _parse_json_response(raw), "gemini"
        except LlmError as exc:
            errors.append(str(exc))
            logger.info("Gemini failed: %s", str(exc)[:200])
            started = time.perf_counter()

    openrouter_key = _openrouter_key()
    if openrouter_key and not skip_openrouter:
        try:
            raw, usage = await _post_chat(
                base_url=settings.openrouter_base_url,
                api_key=openrouter_key,
                model=openrouter_model,
                messages=messages,
                cache_prefix=cache_prefix,
                provider="OpenRouter",
                extra_headers={
                    "HTTP-Referer": settings.app_url,
                    "X-Title": settings.app_name,
                },
                max_tokens=max_tokens,
                temperature=temperature,
            )
            # Parse before recording success — HTTP 200 can still be invalid JSON.
            parsed = _parse_json_response(raw)
            _record_successful_call(
                model=openrouter_model,
                tier=tier,
                provider="openrouter",
                usage=usage,
                latency_ms=int((time.perf_counter() - started) * 1000),
                node_name=node_name,
                rfp_id=rfp_id,
                run_id=run_id,
            )
            return parsed, "openrouter"
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
            started = time.perf_counter()

    fireworks_key = _fireworks_key()
    fireworks_failed = False
    if (
        fireworks_key
        and not _FIREWORKS_SUSPENDED
        and not skip_fireworks_fallback
        and allow_fireworks
    ):
        try:
            # Only reached when requested ≤ Fireworks cap (T3.1 — no silent under-serve).
            requested = max_tokens or 4096
            fireworks_tokens = min(requested, 8192)
            raw, usage = await _post_chat(
                base_url=settings.fireworks_base_url,
                api_key=fireworks_key,
                model=settings.fireworks_model,
                messages=messages,
                cache_prefix=cache_prefix,
                provider="Fireworks",
                max_tokens=fireworks_tokens,
                temperature=temperature,
            )
            _record_successful_call(
                model=settings.fireworks_model,
                tier=tier,
                provider="fireworks",
                usage=usage,
                latency_ms=int((time.perf_counter() - started) * 1000),
                node_name=node_name,
                rfp_id=rfp_id,
                run_id=run_id,
            )
            return _parse_json_response(raw), "fireworks"
        except LlmError as exc:
            fireworks_failed = True
            # If Fireworks account is suspended (412), don't count it as a provider failure
            if exc.status_code == 412:
                _FIREWORKS_SUSPENDED = True
                logger.warning("Fireworks account suspended - skipping for future calls")
            else:
                errors.append(str(exc))
    elif _FIREWORKS_SUSPENDED:
        fireworks_failed = True
        logger.debug("Fireworks skipped (account previously suspended)")

    # Prefer-Fireworks skipped OpenRouter/Gemini above — if Fireworks is down, fall back.
    if settings.llm_prefer_fireworks and (fireworks_failed or _FIREWORKS_SUSPENDED):
        if openrouter_key:
            try:
                started = time.perf_counter()
                logger.info("Falling back to OpenRouter after Fireworks unavailable")
                raw, usage = await _post_chat(
                    base_url=settings.openrouter_base_url,
                    api_key=openrouter_key,
                    model=openrouter_model,
                    messages=messages,
                    cache_prefix=cache_prefix,
                    provider="OpenRouter",
                    extra_headers={
                        "HTTP-Referer": settings.app_url,
                        "X-Title": settings.app_name,
                    },
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
                _record_successful_call(
                    model=openrouter_model,
                    tier=tier,
                    provider="openrouter",
                    usage=usage,
                    latency_ms=int((time.perf_counter() - started) * 1000),
                    node_name=node_name,
                    rfp_id=rfp_id,
                    run_id=run_id,
                )
                return _parse_json_response(raw), "openrouter"
            except LlmError as exc:
                errors.append(str(exc))
                logger.info("OpenRouter fallback failed: %s", str(exc)[:200])
        if gemini_key and not _is_placeholder_key(gemini_key):
            try:
                started = time.perf_counter()
                logger.info("Falling back to Gemini after Fireworks unavailable")
                raw, usage = await _post_gemini_chat(
                    api_key=gemini_key,
                    model=settings.gemini_model,
                    messages=inline_cache_prefix(messages, cache_prefix),
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
                _record_successful_call(
                    model=settings.gemini_model,
                    tier=tier,
                    provider="gemini",
                    usage=usage,
                    latency_ms=int((time.perf_counter() - started) * 1000),
                    node_name=node_name,
                    rfp_id=rfp_id,
                    run_id=run_id,
                )
                return _parse_json_response(raw), "gemini"
            except LlmError as exc:
                errors.append(str(exc))
                logger.info("Gemini fallback failed: %s", str(exc)[:200])

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
    node_name: str | None = None,
    rfp_id: str | None = None,
    run_id: str | None = None,
    cache_prefix: str | Sequence[str] | None = None,
    include_corrections: bool = True,
) -> tuple[dict[str, Any], str]:
    """One LLM JSON call. On failure return ({}, \"failed\") — never retry, never raise."""
    try:
        return await chat_json(
            messages,
            max_tokens=max_tokens,
            temperature=temperature,
            tier=tier,
            node_name=node_name,
            rfp_id=rfp_id,
            run_id=run_id,
            cache_prefix=cache_prefix,
            include_corrections=include_corrections,
        )
    except LlmError as exc:
        logger.warning("chat_json_soft: %s", str(exc)[:220])
        return {}, "failed"


@_langsmith_traceable(name="llm.chat_text", run_type="chain")
async def chat_text(
    messages: list[dict[str, str]],
    *,
    max_tokens: int | None = None,
    temperature: float = 0.2,
    tier: LlmTier = "heavy",
    node_name: str | None = None,
    rfp_id: str | None = None,
    run_id: str | None = None,
    cache_prefix: str | Sequence[str] | None = None,
    include_corrections: bool = True,
) -> tuple[str, str]:
    """Plain-text chat completion (no JSON response format)."""
    messages = await apply_standing_corrections(
        messages, node_name=node_name, include_corrections=include_corrections
    )
    global _FIREWORKS_SUSPENDED
    errors: list[str] = []
    openrouter_model = resolve_llm_model(tier)
    started = time.perf_counter()

    _resolved_node, skip_gemini, skip_openrouter, allow_fireworks = _provider_routing(
        max_tokens=max_tokens,
        node_name=node_name,
    )
    if node_name is None:
        node_name = _resolved_node or None
    _enforce_run_cost_cap(node_name, run_id)

    gemini_key = settings.gemini_api_key.strip()
    if gemini_key and not _is_placeholder_key(gemini_key) and not skip_gemini:
        try:
            raw, usage = await _post_gemini_chat(
                api_key=gemini_key,
                model=settings.gemini_model,
                messages=inline_cache_prefix(messages, cache_prefix),
                max_tokens=max_tokens,
                temperature=temperature,
                json_mode=False,
            )
            _record_successful_call(
                model=settings.gemini_model,
                tier=tier,
                provider="gemini",
                usage=usage,
                latency_ms=int((time.perf_counter() - started) * 1000),
                node_name=node_name,
                rfp_id=rfp_id,
                run_id=run_id,
            )
            return raw, "gemini"
        except LlmError as exc:
            errors.append(str(exc))
            started = time.perf_counter()

    openrouter_key = _openrouter_key()
    if openrouter_key and not skip_openrouter:
        try:
            raw, usage = await _post_chat(
                base_url=settings.openrouter_base_url,
                api_key=openrouter_key,
                model=openrouter_model,
                messages=messages,
                cache_prefix=cache_prefix,
                provider="OpenRouter",
                extra_headers={
                    "HTTP-Referer": settings.app_url,
                    "X-Title": settings.app_name,
                },
                max_tokens=max_tokens,
                temperature=temperature,
                json_mode=False,
            )
            _record_successful_call(
                model=openrouter_model,
                tier=tier,
                provider="openrouter",
                usage=usage,
                latency_ms=int((time.perf_counter() - started) * 1000),
                node_name=node_name,
                rfp_id=rfp_id,
                run_id=run_id,
            )
            return raw, "openrouter"
        except LlmError as exc:
            errors.append(str(exc))
            started = time.perf_counter()

    fireworks_key = _fireworks_key()
    fireworks_failed = False
    if fireworks_key and not _FIREWORKS_SUSPENDED and allow_fireworks:
        try:
            requested = max_tokens or 4096
            fireworks_tokens = min(requested, 8192)
            raw, usage = await _post_chat(
                base_url=settings.fireworks_base_url,
                api_key=fireworks_key,
                model=settings.fireworks_model,
                messages=messages,
                cache_prefix=cache_prefix,
                provider="Fireworks",
                max_tokens=fireworks_tokens,
                temperature=temperature,
                json_mode=False,
            )
            _record_successful_call(
                model=settings.fireworks_model,
                tier=tier,
                provider="fireworks",
                usage=usage,
                latency_ms=int((time.perf_counter() - started) * 1000),
                node_name=node_name,
                rfp_id=rfp_id,
                run_id=run_id,
            )
            return raw, "fireworks"
        except LlmError as exc:
            fireworks_failed = True
            if exc.status_code == 412:
                _FIREWORKS_SUSPENDED = True
                logger.warning("Fireworks account suspended - skipping for future calls")
            elif exc.status_code != 412:
                errors.append(str(exc))
    elif _FIREWORKS_SUSPENDED:
        fireworks_failed = True

    if settings.llm_prefer_fireworks and (fireworks_failed or _FIREWORKS_SUSPENDED):
        if openrouter_key:
            try:
                started = time.perf_counter()
                logger.info("Falling back to OpenRouter after Fireworks unavailable")
                raw, usage = await _post_chat(
                    base_url=settings.openrouter_base_url,
                    api_key=openrouter_key,
                    model=openrouter_model,
                    messages=messages,
                    cache_prefix=cache_prefix,
                    provider="OpenRouter",
                    extra_headers={
                        "HTTP-Referer": settings.app_url,
                        "X-Title": settings.app_name,
                    },
                    max_tokens=max_tokens,
                    temperature=temperature,
                    json_mode=False,
                )
                _record_successful_call(
                    model=openrouter_model,
                    tier=tier,
                    provider="openrouter",
                    usage=usage,
                    latency_ms=int((time.perf_counter() - started) * 1000),
                    node_name=node_name,
                    rfp_id=rfp_id,
                    run_id=run_id,
                )
                return raw, "openrouter"
            except LlmError as exc:
                errors.append(str(exc))
        if gemini_key and not _is_placeholder_key(gemini_key):
            try:
                started = time.perf_counter()
                logger.info("Falling back to Gemini after Fireworks unavailable")
                raw, usage = await _post_gemini_chat(
                    api_key=gemini_key,
                    model=settings.gemini_model,
                    messages=inline_cache_prefix(messages, cache_prefix),
                    max_tokens=max_tokens,
                    temperature=temperature,
                    json_mode=False,
                )
                _record_successful_call(
                    model=settings.gemini_model,
                    tier=tier,
                    provider="gemini",
                    usage=usage,
                    latency_ms=int((time.perf_counter() - started) * 1000),
                    node_name=node_name,
                    rfp_id=rfp_id,
                    run_id=run_id,
                )
                return raw, "gemini"
            except LlmError as exc:
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


def _find_balanced_json_end(text: str, start: int) -> int | None:
    """Index just past the closing brace/bracket matching ``text[start]``.

    None when the opening delimiter never closes within the string (the
    truncated-mid-generation case — leave that to ``_close_truncated_json``).
    """
    in_string = False
    escape = False
    stack: list[str] = []
    for i in range(start, len(text)):
        ch = text[i]
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
        elif ch in "}]":
            if not stack:
                return None
            stack.pop()
            if not stack:
                return i + 1
    return None


def _extract_json_from_text(text: str) -> str:
    """Extract JSON from text that may have explanatory prefixes or markdown formatting."""
    # Strip fences FIRST. If we slice from `{` before stripping, a trailing ``` remains
    # and json.loads fails on otherwise-valid Claude responses.
    text = _strip_code_fence(text.strip())

    brace_start = text.find("{")
    bracket_start = text.find("[")

    if brace_start >= 0 and (bracket_start < 0 or brace_start < bracket_start):
        start = brace_start
    elif bracket_start >= 0:
        start = bracket_start
    else:
        return _strip_code_fence(text)

    # A complete, well-formed JSON value can be followed by trailing prose —
    # a model that answers the schema AND then keeps talking (e.g. adding an
    # unsolicited clarifying question after the JSON). json.loads rejects
    # "Extra data" after a valid value, so isolate just the balanced span
    # when one closes; otherwise keep the old to-end-of-string slice so the
    # truncated-mid-generation repair path still gets a chance.
    end = _find_balanced_json_end(text, start)
    text = text[start:end] if end is not None else text[start:]

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


def _escape_raw_controls_in_json_strings(text: str) -> str:
    """Turn raw newlines/tabs inside JSON strings into escapes so json.loads can run.

    Claude often emits ```json fences and then puts real line breaks inside
    \"summary\" / \"replacement\" values. That is invalid JSON even when the
    object is otherwise complete.
    """
    out: list[str] = []
    in_string = False
    escape = False
    for ch in text:
        if escape:
            out.append(ch)
            escape = False
            continue
        if ch == "\\" and in_string:
            out.append(ch)
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            out.append(ch)
            continue
        if in_string:
            if ch == "\n":
                out.append("\\n")
                continue
            if ch == "\r":
                continue
            if ch == "\t":
                out.append("\\t")
                continue
            if ord(ch) < 32:
                out.append(f"\\u{ord(ch):04x}")
                continue
        out.append(ch)
    return "".join(out)


def _try_parse_json_object(text: str) -> dict[str, Any] | None:
    escaped = _escape_raw_controls_in_json_strings(text)
    for candidate in (
        text,
        escaped,
        _close_truncated_json(text),
        _close_truncated_json(escaped),
    ):
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
    """Recover content/replacement from a single-object payload if LLM JSON is invalid.

    Excerpt edits return {"replacement": "..."} and models often put raw markdown
    table newlines inside the string, which json.loads rejects.
    """
    for key in ("replacement", "content"):
        match = re.search(rf'"{key}"\s*:\s*"', text)
        if not match:
            continue
        content_start = match.end()
        chunk = text[content_start:]
        content = _extract_json_string_value(chunk, allow_partial=True)
        if not content.strip():
            continue
        clean_content = content.rstrip('}" \t\n\r')
        payload: dict[str, Any] = {key: clean_content}
        if key == "replacement":
            payload["content"] = clean_content
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


def _salvage_object_array_payload(text: str, key: str) -> list[dict[str, Any]] | None:
    """Recover complete objects from a truncated JSON array field."""
    needle = f'"{key}"'
    start = text.find(needle)
    if start == -1:
        return None
    bracket = text.find("[", start)
    if bracket == -1:
        return None

    items: list[dict[str, Any]] = []
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
            # Last object was cut off — try to close it.
            snippet = _escape_raw_controls_in_json_strings(text[obj_start:])
            parsed = _try_parse_json_object(snippet)
            if isinstance(parsed, dict) and parsed:
                items.append(parsed)
            break
        blob = _escape_raw_controls_in_json_strings(text[obj_start:j])
        try:
            obj = json.loads(blob)
        except json.JSONDecodeError:
            obj = _try_parse_json_object(blob)
        if isinstance(obj, dict):
            items.append(obj)
        i = j
    return items


def _salvage_issues_payload(text: str) -> dict[str, Any] | None:
    """Recover forms-audit {issues: [...]} when Claude fences or truncates JSON."""
    if not re.search(r'"issues"\s*:', text):
        return None
    looks_like_audit = bool(
        re.search(r'\{\s*"issues"\s*:', text)
        or '"verbatimQuote"' in text
        or '"verbatim_quote"' in text
        or '"fixAction"' in text
    )
    if not looks_like_audit:
        return None
    items = _salvage_object_array_payload(text, "issues")
    if items is None:
        return None
    return {"issues": items}


def _salvage_recommendations_payload(text: str) -> dict[str, Any] | None:
    """Recover complete recommendation objects from a truncated editorial-review JSON."""
    items = _salvage_object_array_payload(text, "recommendations")
    if not items:
        return None
    return {"recommendations": items}


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
            (_salvage_issues_payload, "forms-audit issue(s)"),
            (_salvage_recommendations_payload, "recommendation(s)"),
            (_salvage_simple_content_payload, "simple content"),
            (_salvage_budget_payload, "budget field(s)"),
        ):
            salvaged = salvager(text)
            if salvaged:
                count = len(
                    salvaged.get("sections")
                    or salvaged.get("recommendations")
                    or salvaged.get("issues")
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
