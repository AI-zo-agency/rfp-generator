import asyncio
import time

from app.services import proposal_improve_runs as runs


def _no_redis(monkeypatch):
    monkeypatch.setattr(runs.settings, "redis_url", "", raising=False)
    monkeypatch.setattr(type(runs.settings), "celery_enabled", property(lambda self: False))
    runs._memory_runs.clear()


def test_run_reports_running_then_completed_result(monkeypatch):
    _no_redis(monkeypatch)

    async def scenario():
        await runs.mark_improve_run_started("rfp-1", "run-a", "sec-9")
        running = await runs.get_improve_run("rfp-1", "run-a")
        assert running["status"] == "running"
        assert running["sectionId"] == "sec-9"

        await runs.finish_improve_run(
            "rfp-1", "run-a", "sec-9", result={"assistantMessage": "done"}
        )
        done = await runs.get_improve_run("rfp-1", "run-a")
        assert done["status"] == "completed"
        assert done["result"] == {"assistantMessage": "done"}

    asyncio.run(scenario())


def test_failed_run_keeps_detail(monkeypatch):
    _no_redis(monkeypatch)

    async def scenario():
        await runs.mark_improve_run_started("rfp-1", "run-b", "sec-1")
        await runs.finish_improve_run("rfp-1", "run-b", "sec-1", error="boom")
        failed = await runs.get_improve_run("rfp-1", "run-b")
        assert failed["status"] == "failed"
        assert failed["detail"] == "boom"

    asyncio.run(scenario())


def test_unknown_run_is_none_and_stale_running_reads_as_failed(monkeypatch):
    _no_redis(monkeypatch)

    async def scenario():
        assert await runs.get_improve_run("rfp-1", "missing") is None
        await runs.mark_improve_run_started("rfp-1", "run-c", "sec-2")
        runs._memory_runs[runs._key("rfp-1", "run-c")]["startedAt"] = (
            time.time() - runs._STALE_RUNNING_SEC - 5
        )
        stale = await runs.get_improve_run("rfp-1", "run-c")
        assert stale["status"] == "failed"

    asyncio.run(scenario())


def test_improve_endpoint_records_result_for_reload(monkeypatch):
    import pytest
    from fastapi import HTTPException

    from app.api.v1 import proposals
    from app.models.proposal import SectionImproveRequest

    _no_redis(monkeypatch)

    class _Response:
        def model_dump(self, **_kwargs):
            return {"assistantMessage": "tightened"}

    async def fake_improve(rfp_id, section_id, body):
        return _Response()

    async def failing_improve(rfp_id, section_id, body):
        raise HTTPException(status_code=422, detail="nope")

    async def scenario():
        monkeypatch.setattr(proposals, "_improve_section", fake_improve)
        body = SectionImproveRequest(message="improve", runId="run-ok")
        await proposals.improve_section_endpoint("rfp-1", "sec-9", body)
        record = await proposals.get_improve_run_endpoint("rfp-1", "run-ok")
        assert record["status"] == "completed"
        assert record["result"] == {"assistantMessage": "tightened"}

        monkeypatch.setattr(proposals, "_improve_section", failing_improve)
        with pytest.raises(HTTPException):
            await proposals.improve_section_endpoint(
                "rfp-1", "sec-9", SectionImproveRequest(message="x", runId="run-bad")
            )
        failed = await proposals.get_improve_run_endpoint("rfp-1", "run-bad")
        assert failed["status"] == "failed"
        assert failed["detail"] == "nope"

        with pytest.raises(HTTPException) as missing:
            await proposals.get_improve_run_endpoint("rfp-1", "never-started")
        assert missing.value.status_code == 404

    asyncio.run(scenario())
