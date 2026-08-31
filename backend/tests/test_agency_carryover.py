from app.financial.agency_carryover import apply_carryover_state


def _item(item_id: str) -> dict:
    return {
        "id": item_id,
        "kind": "delivery",
        "title": item_id,
        "amount": 100.0,
        "go_to": "jobs",
    }


def test_carryover_increments_weeks_open():
    prior = [{**_item("delivery:1"), "first_seen_week": "2026-08-25", "weeks_open": 2}]
    current = [_item("delivery:1"), _item("delivery:2")]

    open_items, carryover, resolved, new_items = apply_carryover_state(
        current,
        prior,
        week_start="2026-09-01",
    )

    assert len(carryover) == 1
    assert carryover[0]["weeks_open"] == 3
    assert carryover[0]["first_seen_week"] == "2026-08-25"
    assert len(new_items) == 1
    assert new_items[0]["id"] == "delivery:2"
    assert new_items[0]["weeks_open"] == 1
    assert resolved == []


def test_resolved_items_appear_when_absent_from_current():
    prior = [_item("delivery:1"), _item("delivery:2")]
    current = [_item("delivery:1")]

    _, _, resolved, _ = apply_carryover_state(current, prior, week_start="2026-09-01")

    assert len(resolved) == 1
    assert resolved[0]["id"] == "delivery:2"


def test_empty_prior_treats_everything_as_new():
    current = [_item("delivery:1")]

    _, carryover, _, new_items = apply_carryover_state(current, None, week_start="2026-09-01")

    assert carryover == []
    assert len(new_items) == 1
    assert new_items[0]["first_seen_week"] == "2026-09-01"
