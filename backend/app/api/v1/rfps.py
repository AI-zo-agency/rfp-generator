import logging

from fastapi import APIRouter, HTTPException, Request

from app.models.rfp import DashboardResponse, ManualRfpCreate, RfpRecord
from app.services.go_no_go_service import GoNoGoError, analyze_rfp
from app.services.rfp_repository import (
    TERMINAL_STATUSES,
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


@router.get("/dashboard", response_model=DashboardResponse)
def get_dashboard() -> DashboardResponse:
    all_rfps = list_rfps()
    active = [r for r in all_rfps if r.status not in TERMINAL_STATUSES]
    return DashboardResponse(
        rfps=active,
        allRfps=all_rfps,
        stats=compute_stats(all_rfps),
    )


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
        body = await request.json()
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


@router.post("/{rfp_id}/analyze")
async def analyze_go_no_go(rfp_id: str) -> dict[str, object]:
    rfp = get_rfp(rfp_id)
    if not rfp:
        raise HTTPException(status_code=404, detail="RFP not found")

    try:
        analysis = await analyze_rfp(rfp)
    except GoNoGoError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Go/No-Go analysis failed: {exc}",
        ) from exc

    updated = save_go_no_go_analysis(rfp_id, analysis)
    if not updated:
        raise HTTPException(status_code=404, detail="RFP not found")

    return {
        "ok": True,
        "rfp": updated.model_dump(by_alias=True),
        "analysis": analysis.model_dump(by_alias=True),
    }


@router.get("/{rfp_id}/pdf")
def get_rfp_pdf(rfp_id: str):
    from fastapi.responses import FileResponse

    from app.services.rfp_content import resolve_rfp_pdf_path

    rfp = get_rfp(rfp_id)
    if not rfp:
        raise HTTPException(status_code=404, detail="RFP not found")

    path = resolve_rfp_pdf_path(rfp_id, rfp.pdf_path or get_rfp_pdf_path(rfp_id))
    if not path:
        raise HTTPException(status_code=404, detail="PDF file missing on disk")

    return FileResponse(
        path,
        media_type="application/pdf",
        filename="rfp.pdf",
        content_disposition_type="inline",
    )
