import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request

from app.models.rfp import (
    ActivityItem,
    CurrentProposalItem,
    DashboardResponse,
    ManualRfpCreate,
    RfpRecord,
)
from app.services import supabase_db as sb
from app.services.go_no_go_service import GoNoGoError, analyze_rfp
from app.services.proposal_repository import list_proposal_draft_summaries
from app.services.rfp_repository import (
    TERMINAL_STATUSES,
    clear_go_no_go_analysis,
    compute_stats,
    delete_rfp,
    get_rfp,
    get_rfp_pdf_path,
    insert_manual_rfp,
    list_rfps,
    mark_rfp_go,
    save_go_no_go_analysis,
    save_manual_pdf,
    update_rfp_pdf_path,
    upsert_rfp,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/rfps", tags=["rfps"])


def _optional_form_int(value: object | None) -> int | None:
    if value is None or value == "":
        return None
    return int(str(value))


def _validate_manual_payload(payload: ManualRfpCreate) -> None:
    if len(payload.title.strip()) < 3:
        raise HTTPException(status_code=400, detail="Title must be at least 3 characters")
    if not payload.client.strip():
        raise HTTPException(status_code=400, detail="Client is required")
    if not payload.due_date.strip():
        raise HTTPException(status_code=400, detail="dueDate is required")


@router.get("", response_model=list[RfpRecord])
def get_rfps() -> list[RfpRecord]:
    return list_rfps()


def _rfp_lookup(all_rfps: list[RfpRecord]) -> dict[str, RfpRecord]:
    by_key: dict[str, RfpRecord] = {}
    for rfp in all_rfps:
        by_key[rfp.id] = rfp
        if rfp.external_id:
            by_key[rfp.external_id] = rfp
    return by_key


def _build_current_proposals(
    all_rfps: list[RfpRecord],
) -> tuple[list[CurrentProposalItem], CurrentProposalItem | None]:
    by_key = _rfp_lookup(all_rfps)
    items: list[CurrentProposalItem] = []
    try:
        summaries = list_proposal_draft_summaries()
    except Exception as exc:
        logger.warning("list_proposal_draft_summaries failed: %s", exc)
        summaries = []

    for summary in summaries:
        rfp = by_key.get(str(summary.get("rfp_id") or ""))
        if not rfp:
            continue
        if rfp.status in TERMINAL_STATUSES:
            continue
        filled = int(summary.get("filled_count") or 0)
        if filled <= 0:
            continue
        items.append(
            CurrentProposalItem(
                rfpId=rfp.id,
                rfpTitle=rfp.title,
                client=rfp.client,
                updatedAt=str(summary.get("updated_at") or rfp.last_activity or ""),
                filledCount=filled,
                sectionCount=int(summary.get("section_count") or 0),
                stage=rfp.stage,
                lastActivityNote=rfp.last_activity_note or "",
            )
        )
        if len(items) >= 8:
            break

    latest = items[0] if items else None
    return items, latest


def _short_activity_action(note: str, *, fallback: str) -> str:
    text = (note or "").strip()
    if not text:
        return fallback
    lower = text.casefold()
    if "go/no-go" in lower or "go / no-go" in lower:
        # Prefer the first clause before the essay summary starts.
        head = text.split(". ", 1)[0].strip()
        if len(head) > 90:
            head = head[:87].rstrip() + "…"
        return head
    if text.startswith("Key Personas selected"):
        count = text.count(",") + 1 if ":" in text else 0
        return f"Key personas selected{f' · {count}' if count else ''}"
    if "proposal draft updated" in lower:
        return text if len(text) <= 80 else text[:77].rstrip() + "…"
    if len(text) > 88:
        return text[:85].rstrip() + "…"
    return text


def _build_recent_activity(
    all_rfps: list[RfpRecord],
    current_proposals: list[CurrentProposalItem],
) -> list[ActivityItem]:
    items: list[ActivityItem] = []
    proposal_ids = {p.rfp_id for p in current_proposals}

    for proposal in current_proposals[:4]:
        action = (
            f"Draft updated · {proposal.filled_count}/"
            f"{proposal.section_count} sections"
        )
        items.append(
            ActivityItem(
                id=f"proposal-{proposal.rfp_id}-{proposal.updated_at}",
                rfpId=proposal.rfp_id,
                rfpTitle=proposal.rfp_title,
                action=action,
                actor="Proposal",
                timestamp=proposal.updated_at,
            )
        )

    for rfp in all_rfps:
        ts = (rfp.last_activity or "").strip()
        note = (rfp.last_activity_note or "").strip()
        if not ts or not note:
            continue
        # Draft updates already represented above — skip duplicate noise.
        if rfp.id in proposal_ids and "proposal draft updated" in note.casefold():
            continue
        actor = "System"
        lower_note = note.casefold()
        if "go/no-go" in lower_note or "marked as go" in lower_note:
            actor = "Go/No-Go"
        elif "persona" in lower_note or "draft" in lower_note:
            actor = "Proposal"
        items.append(
            ActivityItem(
                id=f"rfp-{rfp.id}-{ts}",
                rfpId=rfp.id,
                rfpTitle=rfp.title,
                action=_short_activity_action(note, fallback="Pipeline update"),
                actor=actor,
                timestamp=ts,
            )
        )

    def _ts_key(item: ActivityItem) -> float:
        try:
            return datetime.fromisoformat(
                item.timestamp.replace("Z", "+00:00")
            ).timestamp()
        except ValueError:
            return 0.0

    items.sort(key=_ts_key, reverse=True)
    seen: set[tuple[str, str]] = set()
    deduped: list[ActivityItem] = []
    for item in items:
        key = (item.rfp_id, item.action)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
        if len(deduped) >= 8:
            break
    return deduped


@router.get("/dashboard", response_model=DashboardResponse)
def get_dashboard() -> DashboardResponse:
    all_rfps = list_rfps()
    active = [r for r in all_rfps if r.status not in TERMINAL_STATUSES]
    current_proposals, latest_proposal = _build_current_proposals(all_rfps)
    recent_activity = _build_recent_activity(all_rfps, current_proposals)
    return DashboardResponse(
        rfps=active,
        allRfps=all_rfps,
        stats=compute_stats(all_rfps),
        recentActivity=recent_activity,
        currentProposals=current_proposals,
        latestProposal=latest_proposal,
    )


@router.put("/upsert")
def upsert_rfp_endpoint(record: RfpRecord) -> dict[str, bool]:
    """JustWin sync — upsert by external_id."""
    upsert_rfp(record)
    return {"ok": True}


@router.get("/{rfp_id}", response_model=RfpRecord)
def get_rfp_by_id(rfp_id: str) -> RfpRecord:
    rfp = get_rfp(rfp_id)
    if not rfp:
        raise HTTPException(status_code=404, detail="RFP not found")
    return rfp


@router.delete("/{rfp_id}")
async def delete_rfp_endpoint(rfp_id: str) -> dict[str, object]:
    rfp = delete_rfp(rfp_id)
    if not rfp:
        raise HTTPException(status_code=404, detail="RFP not found")

    logger.info("Deleted RFP %s (%r)", rfp.id, rfp.title)
    return {"ok": True, "deletedId": rfp.id}


@router.post("/extract-due-date")
async def extract_due_date_from_pdf(request: Request) -> dict[str, str | None]:
    """Parse an uploaded solicitation PDF and return a detected due date (ISO)."""
    content_type = request.headers.get("content-type", "")
    content: bytes | None = None

    if "multipart/form-data" in content_type:
        form = await request.form()
        pdf_file = form.get("pdf")
        if pdf_file and hasattr(pdf_file, "read"):
            content = await pdf_file.read()
    else:
        content = await request.body()

    if not content:
        raise HTTPException(status_code=400, detail="PDF file is required")

    if not content.startswith(b"%PDF"):
        raise HTTPException(status_code=400, detail="File must be a PDF")

    from app.services.rfp_due_date import extract_due_date_from_pdf_bytes

    due_date = extract_due_date_from_pdf_bytes(content)
    return {"dueDate": due_date}


@router.post("", response_model=RfpRecord, status_code=201)
async def create_manual_rfp(request: Request) -> RfpRecord:
    content_type = request.headers.get("content-type", "")

    if "multipart/form-data" in content_type:
        form = await request.form()
        payload = ManualRfpCreate(
            title=str(form.get("title", "")),
            client=str(form.get("client", "")),
            location=str(form.get("location", "")),
            sector=str(form.get("sector", "Public Sector")),
            dueDate=str(form.get("dueDate", "")),
            description=str(form.get("description", "")) or None,
            pageLimit=_optional_form_int(form.get("pageLimit")),
            estimatedValue=_optional_form_int(form.get("estimatedValue")),
            priority=str(form.get("priority", "medium")),  # type: ignore[arg-type]
        )
        pdf_file = form.get("pdf")
    else:
        try:
            body = await request.json()
        except Exception as exc:
            raise HTTPException(
                status_code=400,
                detail="Expected JSON or multipart form data for manual RFP create.",
            ) from exc
        payload = ManualRfpCreate.model_validate(body)
        pdf_file = None

    _validate_manual_payload(payload)
    record = insert_manual_rfp(payload)

    if pdf_file and hasattr(pdf_file, "read"):
        content = await pdf_file.read()
        try:
            pdf_path = save_manual_pdf(record.id, content)
            update_rfp_pdf_path(record.id, pdf_path)
            refreshed = get_rfp(record.id)
            if refreshed:
                record = refreshed
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return record


@router.post("/{rfp_id}/go")
def mark_go(rfp_id: str) -> dict[str, str]:
    if not mark_rfp_go(rfp_id):
        raise HTTPException(status_code=404, detail="RFP not found")
    return {"ok": "true", "goNoGo": "go"}


# Go/No-Go "can take a few minutes"; past this, a still-"running" signal is a
# zombie (dead run's persisted note or an orphaned job lock), not a live job.
_GO_NO_GO_STALE_RUNNING_SEC = 20 * 60


def _go_no_go_lock_key(rfp_id: str) -> str:
    # Independent of the proposal-pipeline lock (proposal_job_runner.py
    # defaults to lock_key=rfp_id for those 7 phases) — Go/No-Go and proposal
    # generation have always been able to run concurrently on the same RFP,
    # tracked in separate dicts before this migration; this key preserves
    # that instead of accidentally coupling them under one lock.
    return f"{rfp_id}:go-no-go"


@router.post("/{rfp_id}/analyze")
async def analyze_go_no_go(rfp_id: str) -> dict[str, object]:
    """Start Go/No-Go in the background and return immediately.

    Long LLM calls (60–120s+) used to hold the Next.js proxy open until the
    connection died ("Could not reach" / "Invalid JSON"). Clients poll
    GET /{id}/analyze/status instead.

    Dispatched through the same job tracker the proposal pipeline uses
    (proposal_job_runner.py) — Celery/Redis in production, in-process
    asyncio locally without Redis — instead of a second, separate in-memory
    dict that could get orphaned on a restart.
    """
    from app.services.proposal_job_runner import get_proposal_job, start_proposal_job

    rfp = get_rfp(rfp_id)
    if not rfp:
        raise HTTPException(status_code=404, detail="RFP not found")

    lock_key = _go_no_go_lock_key(rfp_id)
    existing = await get_proposal_job(rfp_id, lock_key=lock_key)
    if existing and existing.status == "running":
        return {
            "ok": True,
            "status": "running",
            "rfpId": rfp_id,
            "message": "Go/No-Go analysis already running",
        }

    # Drop stale Stage 1 results so re-runs never show the prior GO panel.
    clear_go_no_go_analysis(rfp_id)

    async def _run() -> None:
        import uuid

        from app.services.llm_call_context import llm_call_context

        try:
            current = get_rfp(rfp_id)
            if not current:
                raise GoNoGoError("RFP not found", status_code=404)
            with llm_call_context(
                rfp_id=rfp_id,
                run_id=str(uuid.uuid4()),
                node_name="go_no_go",
            ):
                analysis = await analyze_rfp(current)
            updated = save_go_no_go_analysis(rfp_id, analysis)
            if not updated:
                raise GoNoGoError("RFP not found after save", status_code=404)
            logger.info("Go/No-Go background job completed for %s", rfp_id)
        except GoNoGoError as exc:
            logger.error("Go/No-Go failed for %s: %s", rfp_id, exc)
            _mark_analyze_failed(rfp_id, str(exc))
            raise
        except Exception as exc:
            logger.exception("Go/No-Go unexpected failure for %s", rfp_id)
            _mark_analyze_failed(rfp_id, f"Go/No-Go analysis failed: {exc}")
            raise

    def _celery_dispatch() -> object:
        from app.celery_app import run_go_no_go_task

        return run_go_no_go_task.delay(rfp_id)

    await start_proposal_job(
        rfp_id,
        "go-no-go",
        _run,
        celery_dispatch=_celery_dispatch,
        lock_key=lock_key,
    )
    return {
        "ok": True,
        "status": "running",
        "rfpId": rfp_id,
        "message": "Go/No-Go analysis started",
    }


@router.get("/{rfp_id}/analyze/status")
async def analyze_go_no_go_status(rfp_id: str) -> dict[str, object]:
    from app.services.proposal_job_runner import get_proposal_job

    rfp = get_rfp(rfp_id)
    if not rfp:
        raise HTTPException(status_code=404, detail="RFP not found")

    job = await get_proposal_job(rfp_id, lock_key=_go_no_go_lock_key(rfp_id))
    if job and job.status == "running":
        # Guard against an orphaned job lock (e.g. a Redis lock that outlived its
        # dead worker across a redeploy) reporting "running" indefinitely.
        from app.services.proposal_job_runner import _iso_age_sec

        age = _iso_age_sec(job.started_at)
        if age is None or age < _GO_NO_GO_STALE_RUNNING_SEC:
            return {
                "status": "running",
                "rfpId": rfp_id,
                "startedAt": job.started_at,
            }
        # Stale lock — free it so the UI unsticks and a re-run is possible.
        try:
            from app.services.proposal_job_runner import cancel_proposal_job

            await cancel_proposal_job(rfp_id, lock_key=_go_no_go_lock_key(rfp_id))
        except Exception:  # noqa: BLE001 — best-effort; status below still unsticks
            logger.warning("Could not clear stale Go/No-Go job lock for %s", rfp_id)
    if job and job.status == "failed":
        return {
            "status": "failed",
            "rfpId": rfp_id,
            "error": job.error or "Go/No-Go analysis failed",
        }
    if rfp.go_no_go_analysis:
        return {
            "status": "completed",
            "rfpId": rfp_id,
            "recommendation": rfp.go_no_go,
        }
    if job and job.status == "completed":
        return {"status": "completed", "rfpId": rfp_id}

    note = (rfp.last_activity_note or "").lower()
    if "go/no-go analysis failed" in note:
        return {
            "status": "failed",
            "rfpId": rfp_id,
            "error": rfp.last_activity_note,
        }
    if "re-run in progress" in note or "analysis in progress" in note:
        # Only trust this persisted note while it is RECENT. It survives backend
        # restarts/redeploys, so a run that died without marking itself failed
        # (crash, OOM, worker killed, redeploy mid-run) would otherwise report
        # "running" forever. Past the stale window with no live job, the run is
        # gone — report idle so the UI unsticks and the user can re-run.
        from app.services.proposal_job_runner import _iso_age_sec

        age = _iso_age_sec(rfp.last_activity)
        if age is None or age < _GO_NO_GO_STALE_RUNNING_SEC:
            return {"status": "running", "rfpId": rfp_id}
        return {"status": "idle", "rfpId": rfp_id, "stale": True}

    return {"status": "idle", "rfpId": rfp_id}


def _mark_analyze_failed(rfp_id: str, error: str) -> None:
    """Persist a failure note so GET /analyze/status has a durable fallback
    signal even if job-tracker state itself is unavailable (matches the
    existing rfp.last_activity_note fallback path above)."""
    try:
        if sb.use_supabase_db():
            now = datetime.now(timezone.utc).isoformat()
            client = sb._get_client()  # noqa: SLF001 — shared Supabase client
            client.table("rfps").update(
                {
                    "last_activity": now,
                    "last_activity_note": error[:500],
                }
            ).or_(f"id.eq.{rfp_id},external_id.eq.{rfp_id}").execute()
    except Exception:  # noqa: BLE001 — status already recorded in memory
        logger.warning("Could not persist Go/No-Go failure note for %s", rfp_id)


@router.post("/{rfp_id}/pdf")
async def upload_rfp_pdf(rfp_id: str, request: Request) -> dict[str, str]:
    """Upload or replace RFP PDF (Supabase bucket when configured)."""
    rfp = get_rfp(rfp_id)
    if not rfp:
        raise HTTPException(status_code=404, detail="RFP not found")

    content_type = request.headers.get("content-type", "")
    content: bytes | None = None

    if "multipart/form-data" in content_type:
        form = await request.form()
        pdf_file = form.get("pdf")
        if pdf_file and hasattr(pdf_file, "read"):
            content = await pdf_file.read()
    else:
        content = await request.body()

    if not content:
        raise HTTPException(status_code=400, detail="PDF file is required")

    try:
        pdf_path = save_manual_pdf(rfp_id, content)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    update_rfp_pdf_path(rfp_id, pdf_path)
    return {"ok": "true", "pdfPath": pdf_path}


@router.api_route("/{rfp_id}/pdf", methods=["GET", "HEAD"])
def get_rfp_pdf(rfp_id: str, request: Request):
    from fastapi.responses import FileResponse, RedirectResponse, Response

    from app.services.rfp_content import resolve_rfp_pdf_path
    from app.services.rfp_storage import (
        is_supabase_path,
        load_rfp_pdf_bytes,
        resolve_pdf_view_url,
    )

    head_only = request.method == "HEAD"

    rfp = get_rfp(rfp_id)
    if not rfp:
        raise HTTPException(status_code=404, detail="RFP not found")

    pdf_path = rfp.pdf_path or get_rfp_pdf_path(rfp_id)

    if pdf_path and is_supabase_path(pdf_path):
        signed = resolve_pdf_view_url(rfp_id, pdf_path, sign=True)
        if signed and signed.startswith("http"):
            return RedirectResponse(url=signed, status_code=302)

    pdf_bytes = load_rfp_pdf_bytes(rfp_id, pdf_path)
    if pdf_bytes:
        headers = {
            "Content-Disposition": 'inline; filename="rfp.pdf"',
            "Content-Length": str(len(pdf_bytes)),
        }
        if head_only:
            return Response(status_code=200, media_type="application/pdf", headers=headers)
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers=headers,
        )

    path = resolve_rfp_pdf_path(rfp_id, pdf_path)
    if not path:
        raise HTTPException(status_code=404, detail="PDF file not found")

    if head_only:
        size = path.stat().st_size
        return Response(
            status_code=200,
            media_type="application/pdf",
            headers={
                "Content-Disposition": 'inline; filename="rfp.pdf"',
                "Content-Length": str(size),
            },
        )

    return FileResponse(
        path,
        media_type="application/pdf",
        filename="rfp.pdf",
        content_disposition_type="inline",
    )
