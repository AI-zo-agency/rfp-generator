from app.api.v1.financials import (
    get_iworker_timesheets,
    get_checklist,
    get_sources_status,
    get_audit_queue,
    generate_ai_financial_insights,
)

def test_iworker_timesheets_data():
    res = get_iworker_timesheets()
    assert res["contractor"] == "iWorker Contractor"
    assert "Connected" in res["status"]
    assert len(res["timesheets"]) > 0
    assert res["summary"]["total_logged_hours"] > 0

def test_checklist_data():
    res = get_checklist()
    assert res["total_features"] == 19
    assert len(res["checklist"]) == 19
    assert len(res["phases"]) == 5

def test_sources_status():
    res = get_sources_status()
    sources = res["sources"]
    assert len(sources) == 5
    iworker = next(s for s in sources if s["name"] == "iWorker Timesheets")
    assert iworker["active_data"] is True
    qb = next(s for s in sources if s["name"] == "QuickBooks API")
    assert qb["active_data"] is False

def test_ai_insights():
    res = generate_ai_financial_insights()
    assert res["status"] == "success"
    assert len(res["summary"]["top_3_risks"]) == 3
    assert len(res["summary"]["top_3_wins"]) == 3
