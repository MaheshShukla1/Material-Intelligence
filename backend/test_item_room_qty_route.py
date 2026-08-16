"""pytest for the /item-room-qty route body (real per-room quantity groups
save endpoint). siteprogress.py can't be imported standalone in this sandbox
(no main.py/subcat.py/data dir -- see prior handoff notes), so this
replicates the route body verbatim against an in-memory dict standing in
for item_room_qty.json, calling the real itemprog.set_room_qty_group()
underneath -- a regression in either would break this test.

Run: cd tests && python -m pytest test_item_room_qty_route.py -v
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import pytest

import itemprog


def set_item_room_qty(store, service, code, rooms, qty):
    """Verbatim replica of the /item-room-qty route body (HTTP plumbing
    stripped, everything else identical)."""
    if not service or code is None:
        raise ValueError("service and item_code are required")
    itemprog.set_room_qty_group(store, service, code, rooms or [], qty)
    return store


def test_first_group_saves_correctly():
    store = {}
    set_item_room_qty(store, "Electrical", "2.1", ["r1", "r2", "r3"], 52.0)
    assert store["Electrical"]["2.1"] == [{"rooms": ["r1", "r2", "r3"], "qty": 52.0}]


def test_second_call_with_different_rooms_adds_a_second_group():
    store = {}
    set_item_room_qty(store, "Electrical", "2.1", [f"r{i}" for i in range(1, 101)], 52.0)
    set_item_room_qty(store, "Electrical", "2.1", ["c1", "c2"], 60.0)
    groups = store["Electrical"]["2.1"]
    assert len(groups) == 2
    total_rooms = sum(len(g["rooms"]) for g in groups)
    assert total_rooms == 102
    assert {g["qty"] for g in groups} == {52.0, 60.0}


def test_the_motivating_example_end_to_end_through_the_route():
    store = {}
    standard = [f"std{i}" for i in range(1, 101)]
    corner = [f"cor{i}" for i in range(1, 10)]
    set_item_room_qty(store, "Electrical", "2.1", standard, 52.0)
    set_item_room_qty(store, "Electrical", "2.1", corner, 60.0)
    import pandas as pd
    items_df = pd.DataFrame([{"item_code": "2.1", "description": "conduit", "unit": "MTR", "qty": 0.0}])
    row = itemprog.compute(items_df, {}, {}, standard + corner, {},
                           room_qty_groups=store["Electrical"]).iloc[0]
    assert row["planned_total"] == pytest.approx(5740.0)


def test_missing_service_raises():
    store = {}
    with pytest.raises(ValueError):
        set_item_room_qty(store, None, "2.1", ["r1"], 52.0)


def test_missing_item_code_raises():
    store = {}
    with pytest.raises(ValueError):
        set_item_room_qty(store, "Electrical", None, ["r1"], 52.0)


def test_null_qty_clears_rooms_without_error():
    store = {}
    set_item_room_qty(store, "Electrical", "2.1", ["r1", "r2"], 52.0)
    set_item_room_qty(store, "Electrical", "2.1", ["r1"], None)
    groups = store["Electrical"]["2.1"]
    all_rooms = [r for g in groups for r in g["rooms"]]
    assert all_rooms == ["r2"]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
