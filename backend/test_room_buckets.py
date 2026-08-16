"""pytest for the new room-bucket aggregates (itemprog.room_buckets,
itemprog.project_room_status) and their wiring into realtime.combine_item /
sentence. Pure-function tests -- no FastAPI TestClient, no data directory --
because everything touched here is plain dict/DataFrame math with no I/O.
Run: cd tests && python -m pytest test_room_buckets.py -v
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import pandas as pd
import pytest

import itemprog
import realtime


ALL_ROOMS = ["r1", "r2", "r3", "r4"]


# --------------------------------------------------------------------------
# itemprog.room_buckets
# --------------------------------------------------------------------------
def test_room_buckets_basic_mix():
    prog_svc = {"2.1": {"r1": 1.0, "r2": 0.5, "r3": 0.0, "r4": 1.0}}
    rooms_svc = {}  # applies to every room
    b = itemprog.room_buckets("2.1", prog_svc, rooms_svc, ALL_ROOMS)
    assert b == {"done": 2, "in_progress": 1, "not_started": 1, "total": 4}


def test_room_buckets_default_frac_is_star():
    # r1/r2 override "*", r3/r4 fall back to "*"=0.5 (in progress)
    prog_svc = {"2.1": {"*": 0.5, "r1": 1.0}}
    b = itemprog.room_buckets("2.1", prog_svc, {}, ALL_ROOMS)
    assert b["done"] == 1          # r1
    assert b["in_progress"] == 3   # r2, r3, r4 all inherit "*"=0.5
    assert b["not_started"] == 0
    assert b["total"] == 4


def test_room_buckets_respects_applicability():
    # item only applies to r1, r2 -- r3/r4 must not be counted at all
    rooms_svc = {"2.1": ["r1", "r2"]}
    prog_svc = {"2.1": {"r1": 1.0, "r2": 1.0}}
    b = itemprog.room_buckets("2.1", prog_svc, rooms_svc, ALL_ROOMS)
    assert b["total"] == 2
    assert b["done"] == 2


def test_room_buckets_no_progress_at_all_is_not_started():
    b = itemprog.room_buckets("9.9", {}, {}, ALL_ROOMS)
    assert b == {"done": 0, "in_progress": 0, "not_started": 4, "total": 4}


def test_room_buckets_tolerance_near_one_counts_as_done():
    # a frac that lands at 0.9999999 due to float division should still be "done"
    prog_svc = {"2.1": {"r1": 0.999999999}}
    b = itemprog.room_buckets("2.1", prog_svc, {"2.1": ["r1"]}, ALL_ROOMS)
    assert b["done"] == 1
    assert b["total"] == 1


def test_room_buckets_matches_compute_used_remaining_identity():
    """The core claim from the design discussion: summing remaining qty over
    only the not-fully-done rooms must equal the item's whole `remaining`
    from itemprog.compute() -- because a fully-done room already contributes
    zero to `remaining`. Verify this holds for a real compute() call."""
    items_df = pd.DataFrame([{"item_code": "2.1", "description": "wire",
                              "unit": "MTR", "qty": 10.0}])
    prog_svc = {"2.1": {"r1": 1.0, "r2": 0.5, "r3": 0.0, "r4": 1.0}}
    used_df = itemprog.compute(items_df, prog_svc, {}, ALL_ROOMS, {})
    row = used_df.iloc[0]
    whole_remaining = row["remaining"]

    # manual per-room remaining, uniform per-room qty = planned/n (matches
    # compute()'s own used = planned*(frac_sum/n) formula)
    planned = row["planned_total"]
    per_room_qty = planned / len(ALL_ROOMS)
    buckets = itemprog.room_buckets("2.1", prog_svc, {}, ALL_ROOMS)
    outstanding_remaining = 0.0
    for r in ALL_ROOMS:
        fr = itemprog.frac_for(prog_svc["2.1"], r)
        if fr < 1.0 - 1e-9:
            outstanding_remaining += per_room_qty * (1 - fr)
    assert outstanding_remaining == pytest.approx(whole_remaining, abs=1e-6)
    assert buckets["done"] == 2 and buckets["in_progress"] == 1 and buckets["not_started"] == 1


# --------------------------------------------------------------------------
# itemprog.project_room_status
# --------------------------------------------------------------------------
def _svc(items, prog, rooms, mapped):
    return (pd.DataFrame(items), prog, rooms, set(mapped))


def test_project_room_status_single_service_all_mapped():
    items = [{"item_code": "2.1"}, {"item_code": "2.2"}]
    prog = {"2.1": {"r1": 1.0, "r2": 1.0, "r3": 0.0, "r4": 0.0},
            "2.2": {"r1": 1.0, "r2": 0.5, "r3": 0.0, "r4": 1.0}}
    data = [_svc(items, prog, {}, ["2.1", "2.2"])]
    out = itemprog.project_room_status(data, ALL_ROOMS)
    # r1: both items 1.0 -> done
    # r2: 2.1=1.0, 2.2=0.5 -> in_progress (not every item full)
    # r3: both 0.0 -> not_started
    # r4: 2.1=0.0, 2.2=1.0 -> in_progress (some progress, not all done)
    assert out == {"done": 1, "in_progress": 2, "not_started": 1, "total": 4}


def test_project_room_status_unmapped_items_excluded():
    items = [{"item_code": "2.1"}]
    prog = {"2.1": {"r1": 1.0, "r2": 1.0, "r3": 1.0, "r4": 1.0}}
    # 2.1 is NOT in mapped_codes -> must not count toward "done"
    data = [_svc(items, prog, {}, [])]
    out = itemprog.project_room_status(data, ALL_ROOMS)
    assert out == {"done": 0, "in_progress": 0, "not_started": 4, "total": 4}


def test_project_room_status_multiple_services_room_needs_all_done():
    # room r1 is fully done in Electrical but only half-done in Plumbing ->
    # the ROOM is in_progress project-wide, not done
    elec = _svc([{"item_code": "2.1"}], {"2.1": {"r1": 1.0}}, {}, ["2.1"])
    plumb = _svc([{"item_code": "3.1"}], {"3.1": {"r1": 0.5}}, {}, ["3.1"])
    out = itemprog.project_room_status([elec, plumb], ["r1"])
    assert out == {"done": 0, "in_progress": 1, "not_started": 0, "total": 1}


def test_project_room_status_room_specific_applicability_across_services():
    # item only applies to r2 in this service -- r1 must fall through to
    # "not_started" from this service's contribution (still gated by other
    # services if any; here there are none)
    data = [_svc([{"item_code": "5.1"}], {"5.1": {"r2": 1.0}},
                 {"5.1": ["r2"]}, ["5.1"])]
    out = itemprog.project_room_status(data, ["r1", "r2"])
    assert out["done"] == 1       # r2
    assert out["not_started"] == 1  # r1: nothing mapped applies to it


def test_project_room_status_matches_real_screenshot_shape():
    """Reproduces the shape of the real Hyatt screenshot: 5 services, most
    barely started, ONE item somewhere set at an overall '*' fraction (the
    common way an engineer records "roughly X% done" without going room by
    room) -- which, by definition, touches every applicable room at once.
    That is why "0 done, every room in_progress" is a mathematically honest
    reading of real data, not a bug: a single '*' entry anywhere is enough
    to flip every room it applies to out of not_started, while getting a
    room to `done` still requires EVERY mapped item to be fully finished
    there -- a much higher bar that legitimately nobody has cleared yet."""
    rooms = [f"r{i}" for i in range(1, 11)]   # 10 rooms, standing in for 108
    # Electrical: one item at "*"=0.28 (an engineer's rough "28% done" entry)
    elec = _svc([{"item_code": "E.1"}], {"E.1": {"*": 0.28}}, {}, ["E.1"])
    # Plumbing: one item at "*"=0.09, nothing else
    plumb = _svc([{"item_code": "P.1"}], {"P.1": {"*": 0.09}}, {}, ["P.1"])
    # FAPA/Fire: mapped but genuinely untouched (0 everywhere)
    fapa = _svc([{"item_code": "F.1"}], {"F.1": {"*": 0.0}}, {}, ["F.1"])
    out = itemprog.project_room_status([elec, plumb, fapa], rooms)
    assert out["total"] == 10
    assert out["done"] == 0            # no room has EVERY mapped item at 1.0 -- correct, nobody's finished
    assert out["in_progress"] == 10    # Electrical's/Plumbing's "*" touches every room -- correct, not a bug
    assert out["not_started"] == 0


# --------------------------------------------------------------------------
# realtime.combine_item / sentence -- rooms wiring changes wording only
# --------------------------------------------------------------------------
def _base_item(remaining=6.1, unit="MTR"):
    return {"item_code": "QI1", "unit": unit, "planned_total": 6.6,
            "used": 0.5, "remaining": remaining, "progress_pct": 7.6}


def test_sentence_enough_without_rooms_is_unchanged():
    stock = [{"material": "25MM PIPE", "stock": 1190, "rate_per_day": 45,
              "days_left": 26, "status": "GREEN", "unit": "MTR",
              "total_consumed": 1308}]
    res = realtime.combine_item(_base_item(), stock)
    msg = realtime.sentence(res)
    assert res["rooms"] is None
    assert "of work remains; stock on hand is enough" in msg


def test_sentence_enough_with_rooms_mentions_room_count():
    stock = [{"material": "25MM PIPE", "stock": 1190, "rate_per_day": 45,
              "days_left": 26, "status": "GREEN", "unit": "MTR",
              "total_consumed": 1308}]
    rooms = {"done": 12, "in_progress": 3, "not_started": 93, "total": 108}
    res = realtime.combine_item(_base_item(), stock, rooms=rooms)
    msg = realtime.sentence(res)
    assert res["rooms"] == rooms
    assert "the remaining 96 rooms" in msg     # 3 + 93 outstanding
    assert "6 MTR" in msg or "6.1 MTR"[:1] in msg  # remaining still ~6.1, formatted %.0f
    assert msg.startswith("6 ")  # %.0f formatting of 6.1 -> "6"


def test_sentence_shortage_with_rooms():
    stock = [{"material": "25MM PIPE", "stock": 1, "rate_per_day": 0.1,
              "days_left": 5, "status": "RED", "unit": "MTR",
              "total_consumed": 100}]
    rooms = {"done": 0, "in_progress": 1, "not_started": 4, "total": 5}
    res = realtime.combine_item(_base_item(remaining=10), stock, rooms=rooms)
    assert res["verdict"] == "SHORTAGE"
    msg = realtime.sentence(res)
    assert "the remaining 5 rooms" in msg
    assert "order about" in msg


def test_sentence_rooms_with_zero_total_falls_back():
    # no structure yet (all_room_ids empty) -> room_buckets total=0 ->
    # sentence must fall back to the original generic wording, not "0 rooms"
    rooms = {"done": 0, "in_progress": 0, "not_started": 0, "total": 0}
    res = realtime.combine_item(_base_item(), [], rooms=rooms)
    msg = realtime.sentence(res)
    assert "rooms" not in msg


def test_remaining_equals_outstanding_room_sum_end_to_end():
    """The identity this whole feature leans on, exercised through the actual
    itemprog.compute() -> realtime.combine_item() path with a quick-added
    item (qty_per_room=0, all quantity in the `planned` override) -- the
    trickier case flagged in itemprog.py's own comments."""
    items_df = pd.DataFrame([{"item_code": "QI1", "description": "pipe",
                              "unit": "MTR", "qty": 0.0}])
    prog_svc = {"QI1": {"r1": 1.0, "r2": 0.0, "r3": 0.0}}
    planned_over = {"QI1": 6.6}
    used_df = itemprog.compute(items_df, prog_svc, {}, ["r1", "r2", "r3"], planned_over)
    row = used_df.iloc[0]
    buckets = itemprog.room_buckets("QI1", prog_svc, {}, ["r1", "r2", "r3"])
    assert buckets == {"done": 1, "in_progress": 0, "not_started": 2, "total": 3}
    item = {"item_code": "QI1", "unit": "MTR", "planned_total": row.planned_total,
            "used": row.used, "remaining": row.remaining, "progress_pct": row.progress_pct}
    res = realtime.combine_item(item, [], rooms=buckets)
    # remaining should be exactly 2/3 of planned (2 of 3 rooms untouched)
    assert res["remaining"] == pytest.approx(6.6 * 2 / 3, abs=1e-6)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
