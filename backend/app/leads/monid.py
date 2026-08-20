"""Server-side Monid company enrichment via People Data Labs."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

# Compact log of Wave-3-useful company fields. PDL also returns headcount time
# series and org-chart analytics we do not persist.
_RESULT_LOG_KEYS = (
    "name",
    "display_name",
    "industry",
    "industry_v2",
    "size",
    "employee_count",
    "founded",
    "type",
    "website",
    "linkedin_url",
    "inferred_revenue",
    "total_funding_raised",
    "headline",
    "summary",
    "tags",
    "likelihood",
    "full_name",
    "job_title",
    "job_title_role",
    "job_title_levels",
    "job_company_name",
)


class MonidError(RuntimeError):
    """A Monid request did not return a usable company match."""


def _money_usd(money: Any) -> float | None:
    if not isinstance(money, dict) or not isinstance(money.get("value"), (int, float)):
        return None
    value = float(money["value"])
    return value / 1_000_000 if money.get("unit") == "MICRO_DOLLAR" else value


def run_cost_usd(run: dict[str, Any]) -> float | None:
    """List price if present, else billed/reported wallet cost."""
    price = run.get("price") or {}
    if isinstance(price.get("amount"), (int, float)):
        return float(price["amount"])
    billing = run.get("billing") or {}
    return _money_usd(billing.get("actualCost")) or _money_usd(billing.get("reportedCost"))


def billed_usd(run: dict[str, Any]) -> float | None:
    billing = run.get("billing") or {}
    return _money_usd(billing.get("actualCost"))


def result_preview(output: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(output, dict):
        return None
    preview = {key: output[key] for key in _RESULT_LOG_KEYS if output.get(key) not in (None, "", [])}
    location = output.get("location") or {}
    if isinstance(location, dict):
        loc = {
            key: location[key]
            for key in ("locality", "region", "metro", "country")
            if location.get(key)
        }
        if loc:
            preview["location"] = loc
    extra = sorted(
        key for key in output if key not in _RESULT_LOG_KEYS and key not in {"location", "status"}
    )
    if extra:
        preview["other_keys"] = extra
    return preview


def log_monid_run(run: dict[str, Any], label: str) -> None:
    output = run.get("output") if isinstance(run.get("output"), dict) else None
    logger.info(
        "Monid enrich endpoint=%s label=%s run_id=%s status=%s provider_http=%s "
        "price_usd=%s billed_usd=%s result=%s",
        run.get("endpoint"),
        label,
        run.get("id") or run.get("runId"),
        run.get("status"),
        (run.get("providerResponse") or {}).get("httpStatus"),
        run_cost_usd(run),
        billed_usd(run),
        result_preview(output) if output is not None else run.get("output"),
    )


def extract_completed_output(run: dict[str, Any]) -> dict[str, Any]:
    """Return a successful synchronous Monid run's provider output."""
    provider_status = (run.get("providerResponse") or {}).get("httpStatus", 200)
    if provider_status >= 400:
        error = (run.get("providerResponse") or {}).get("error") or {}
        raise MonidError(error.get("message") or f"Provider returned HTTP {provider_status}")
    output = run.get("output")
    if not isinstance(output, dict):
        raise MonidError("Monid returned no match")
    return output


def _error_message(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    message = payload.get("message")
    if isinstance(message, dict):
        message = message.get("message")
    if isinstance(message, str) and message.strip():
        return message.strip()
    return None


def is_payment_error(error: object) -> bool:
    text = str(error).casefold()
    return "payment required" in text or "insufficient wallet" in text


def completed_output_from_run_http(status_code: int, payload: Any) -> dict[str, Any] | None:
    """Parse POST /v1/run. None means HTTP 202 — the caller should poll GET /v1/runs/:runId."""
    if status_code == 202:
        return None
    if status_code == 402:
        raise MonidError(f"Monid payment required: {_error_message(payload) or 'insufficient wallet balance'}")
    if status_code == 200:
        if not isinstance(payload, dict):
            raise MonidError("Monid returned no match")
        if payload.get("status") == "FAILED":
            raise MonidError("Monid could not complete this enrichment run")
        return extract_completed_output(payload)
    # PDL no-match is often HTTP 404 with a COMPLETED run body — not a dead gateway.
    if isinstance(payload, dict) and payload.get("status") == "COMPLETED":
        return extract_completed_output(payload)
    raise MonidError(f"Monid API HTTP {status_code}: {_error_message(payload) or 'request failed'}")


def available() -> bool:
    return bool(settings.monid_api_key.strip())


def _employee_band(count: Any) -> str | None:
    if not isinstance(count, int) or count < 1:
        return None
    if count <= 10:
        return "1-10"
    if count <= 50:
        return "11-50"
    if count <= 200:
        return "51-200"
    if count <= 1000:
        return "201-1000"
    return "1000+"


def _size_band(data: dict[str, Any]) -> str | None:
    size = data.get("size")
    if isinstance(size, str) and size.strip():
        return size.strip()
    return _employee_band(data.get("employee_count"))


def _confidence(likelihood: Any) -> str:
    if isinstance(likelihood, int) and likelihood >= 7:
        return "high"
    if isinstance(likelihood, int) and likelihood >= 6:
        return "medium"
    return "low"


def _tags(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(tag).strip() for tag in value if str(tag).strip()][:5]


_NAME_STOP = frozenset({"inc", "llc", "ltd", "co", "corp", "company", "the", "and", "of", "group"})


def _compact(value: str) -> str:
    return "".join(ch for ch in value.casefold() if ch.isalnum())


def _name_tokens(name: str) -> set[str]:
    parts = "".join(ch.lower() if ch.isalnum() else " " for ch in name).split()
    return {part for part in parts if len(part) >= 4 and part not in _NAME_STOP}


def _host(domain: str) -> str:
    return domain.split("@")[-1].split(":")[0].removeprefix("www.").split(".")[0].casefold()


def _name_matches_domain(name: str, domain: str) -> bool:
    host = _host(domain)
    compact = _compact(name)
    if len(host) >= 4 and (host in compact or compact in host):
        return True
    return any(token in host or host in token for token in _name_tokens(name))


def _names_agree(left: str, right: str) -> bool:
    a, b = _name_tokens(left), _name_tokens(right)
    if a and b:
        return bool(a & b)
    cl, cr = _compact(left), _compact(right)
    return bool(cl) and bool(cr) and (cl in cr or cr in cl)


def _best_company_name(data: dict[str, Any], domain: str, known_name: str | None) -> str | None:
    candidates: list[str] = []
    if known_name and known_name.strip():
        candidates.append(known_name.strip())
    for value in (data.get("display_name"), data.get("name"), *(data.get("alternative_names") or [])):
        if isinstance(value, str) and value.strip():
            candidates.append(value.strip())
    slug = data.get("linkedin_slug")
    if isinstance(slug, str) and slug.strip():
        candidates.append(slug.replace("-", " ").replace("_", " ").strip())
    for name in candidates:
        if _name_matches_domain(name, domain):
            return name
    return None


def normalize_company_enrichment(
    data: dict[str, Any],
    domain: str,
    known_name: str | None = None,
) -> dict[str, Any]:
    """Map the Wave 3-useful PDL fields into the lead-enrichment response."""
    location = data.get("location") or {}
    likelihood = data.get("likelihood")
    pdl_name = data.get("display_name") or data.get("name")
    if isinstance(pdl_name, str):
        pdl_name = pdl_name.strip() or None
    else:
        pdl_name = None
    chosen = _best_company_name(data, domain, known_name)
    conflict = bool(pdl_name and chosen and not _names_agree(pdl_name, chosen))
    linkedin = data.get("linkedin_url") if _name_matches_domain(str(data.get("linkedin_url") or ""), domain) else None
    website = data.get("website") if isinstance(data.get("website"), str) else None
    if conflict:
        logger.warning(
            "Monid company name conflict domain=%s pdl_name=%s using=%s",
            domain,
            pdl_name,
            chosen,
        )
        return {
            "company_name": chosen,
            "industry": None,
            "company_type": None,
            "city": None,
            "state": None,
            "employee_count": None,
            "employee_band": None,
            "founded": None,
            "inferred_revenue": None,
            "linkedin_url": linkedin,
            "website": website or domain,
            "what_they_do": None,
            "tags": [],
            "confidence": "low",
            "basis": (
                f"Monid matched {domain} to '{pdl_name}', which does not corroborate the domain. "
                "Parent/affiliate firmographics were dropped."
            ),
            "name_conflict": pdl_name,
            "source": "monid-pdl",
            "domain": domain,
        }
    return {
        "company_name": chosen or pdl_name,
        "industry": data.get("industry_v2") or data.get("industry"),
        "company_type": data.get("type"),
        "city": location.get("locality") or location.get("city"),
        "state": location.get("region") or location.get("state"),
        "employee_count": data.get("employee_count") if isinstance(data.get("employee_count"), int) else None,
        "employee_band": _size_band(data),
        "founded": data.get("founded"),
        "inferred_revenue": data.get("inferred_revenue"),
        "linkedin_url": linkedin or data.get("linkedin_url"),
        "website": website or domain,
        "what_they_do": data.get("headline") or data.get("summary") or data.get("description"),
        "tags": _tags(data.get("tags")),
        "confidence": _confidence(likelihood),
        "basis": f"Monid / People Data Labs match for {domain} (likelihood {likelihood}/10).",
        "source": "monid-pdl",
        "domain": domain,
    }


def unwrap_pdl_output(output: dict[str, Any]) -> dict[str, Any]:
    """Person enrich nests the profile in `data`; company enrich is flat."""
    nested = output.get("data")
    if not isinstance(nested, dict):
        return output
    if "likelihood" not in nested and output.get("likelihood") is not None:
        return {**nested, "likelihood": output["likelihood"]}
    return nested


def _phone(data: dict[str, Any]) -> str | None:
    mobile = data.get("mobile_phone")
    if isinstance(mobile, str) and mobile.strip():
        return mobile.strip()
    for raw in data.get("phone_numbers") or data.get("phones") or []:
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
        if isinstance(raw, dict):
            number = raw.get("number") or raw.get("phone")
            if isinstance(number, str) and number.strip():
                return number.strip()
    return None


def _person_linkedin(data: dict[str, Any]) -> str | None:
    url = data.get("linkedin_url")
    if isinstance(url, str) and url.strip():
        return url.strip()
    for profile in data.get("profiles") or []:
        if isinstance(profile, str) and "linkedin.com" in profile.casefold():
            return profile
    return None


def _join(value: Any) -> str | None:
    if isinstance(value, list):
        parts = [str(item).strip() for item in value if str(item).strip()]
        return ", ".join(parts) or None
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def normalize_person_enrichment(data: dict[str, Any], domain: str) -> dict[str, Any]:
    likelihood = data.get("likelihood")
    return {
        "full_name": data.get("full_name"),
        "job_title": data.get("job_title"),
        "job_title_role": data.get("job_title_role"),
        "job_title_levels": _join(data.get("job_title_levels")),
        "job_company_name": data.get("job_company_name"),
        "phone": _phone(data),
        "linkedin_url": _person_linkedin(data),
        "confidence": _confidence(likelihood),
        "basis": f"Monid / People Data Labs person match for {domain} (likelihood {likelihood}/10).",
        "source": "monid-pdl",
    }


async def _run(endpoint: str, payload: dict[str, Any], label: str) -> dict[str, Any]:
    if not available():
        raise MonidError("Monid is not configured")

    headers = {"Authorization": f"Bearer {settings.monid_api_key}", "Content-Type": "application/json"}
    body = {"provider": "pdl", "endpoint": endpoint, "input": payload}
    async with httpx.AsyncClient(base_url=settings.monid_base_url, headers=headers, timeout=20) as client:
        response = await client.post("/v1/run", json=body)
        try:
            run: Any = response.json()
        except ValueError as exc:
            raise MonidError(f"Monid API HTTP {response.status_code}: non-JSON response") from exc
        if response.status_code != 200 and not (
            isinstance(run, dict) and run.get("status") == "COMPLETED"
        ):
            logger.warning(
                "Monid run HTTP %s endpoint=%s label=%s message=%s",
                response.status_code,
                endpoint,
                label,
                _error_message(run) or (str(run)[:200] if run else ""),
            )
        elif isinstance(run, dict):
            log_monid_run(run, label)
        completed = completed_output_from_run_http(response.status_code, run)
        if completed is not None:
            return completed
        run_id = run.get("runId") or run.get("id") if isinstance(run, dict) else None
        if not run_id:
            raise MonidError("Monid returned no completed match")
        for _ in range(20):
            await asyncio.sleep(1)
            poll = await client.get(f"/v1/runs/{run_id}")
            poll.raise_for_status()
            run = poll.json()
            if run.get("status") == "COMPLETED":
                log_monid_run(run, label)
                return extract_completed_output(run)
            if run.get("status") == "FAILED":
                log_monid_run(run, label)
                raise MonidError("Monid could not complete this enrichment run")
    raise MonidError("Monid returned no completed match")


async def enrich_company(domain: str, known_name: str | None = None) -> dict[str, Any]:
    # Monid gateway: POST /v1/run. PDL catalog path is /v5/company/enrich (not /company/enrich).
    output = await _run(
        "/v5/company/enrich",
        {"website": domain, "min_likelihood": 6},
        domain,
    )
    return normalize_company_enrichment(unwrap_pdl_output(output), domain, known_name=known_name)


async def enrich_person(email: str) -> dict[str, Any]:
    domain = email.rsplit("@", 1)[-1].strip().lower() if "@" in email else email
    output = await _run(
        "/v5/person/enrich",
        {"email": email, "min_likelihood": 6},
        f"person:{domain}",
    )
    return normalize_person_enrichment(unwrap_pdl_output(output), domain)


async def enrich_contact(
    domain: str,
    email: str,
    *,
    skip_company: bool = False,
    known_company: str | None = None,
) -> dict[str, Any]:
    """Company + person in parallel. A 404 on one side does not kill the other."""

    async def _company() -> dict[str, Any] | MonidError | None:
        if skip_company:
            return None
        try:
            return await enrich_company(domain, known_name=known_company)
        except MonidError as exc:
            logger.warning("Monid company enrich failed domain=%s: %s", domain, exc)
            return exc
        except Exception as exc:
            logger.warning("Monid company enrich failed domain=%s: %s", domain, exc, exc_info=True)
            return MonidError(str(exc))

    async def _person() -> dict[str, Any] | MonidError:
        try:
            return await enrich_person(email)
        except MonidError as exc:
            logger.warning("Monid person enrich failed domain=%s: %s", domain, exc)
            return exc
        except Exception as exc:
            logger.warning("Monid person enrich failed domain=%s: %s", domain, exc, exc_info=True)
            return MonidError(str(exc))

    company_res, person_res = await asyncio.gather(_company(), _person())
    payload: dict[str, Any] = {}
    if skip_company:
        payload["company_skipped"] = "hubspot"
    elif isinstance(company_res, dict):
        payload.update(company_res)
    else:
        payload["company_error"] = str(company_res)

    if isinstance(person_res, dict):
        payload["person"] = person_res
    else:
        payload["person"] = None
        payload["person_error"] = str(person_res)
    return payload
