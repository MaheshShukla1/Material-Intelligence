"""pytest for item_room_qty.json's per-room quantity groups -- the real gap
this closes: compute() always assumed one uniform qty_per_room applies to
EVERY applicable room, so a corner room that genuinely needs more material
than a standard room had no way to be represented; the only "fix" available
was to fudge a single project-wide number.

The exact motivating example: 100 rooms each need 52 MTR of conduit, 9
different (corner) rooms each need 60 MTR of the same item. The real total
is (52*100) + (60*9) = 5740, not a single flat "52" that a quick read of
the Overall page could mistake for the whole item's total.

Every test here also re-confirms the OLD uniform path is byte-for-byte
unchanged when an item has no groups defined -- that backward-compatibility
guarantee is exactly as important as the new feature itself, given how many
other numbers in this app derive from compute().

Run: cd tests && python -m pytest test_room_qty_groups.py -v
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import pandas as pd
import pytest

import itemprog


def _rooms(n, prefix="r"):
    return [f"{prefix}{i}" for i in range(1, n + 1)]


def _items_df(code="2.1", unit="MTR", qty=0.0):
    return pd.DataFrame([{"item_code": code, "description": "conduit", "unit": unit, "qty": qty}])


# --------------------------------------------------------------------------
# the exact motivating example: 100 rooms @ 52 MTR, 9 rooms @ 60 MTR
# --------------------------------------------------------------------------
def test_the_exact_motivating_example_100_at_52_9_at_60():
    standard = _rooms(100, "std")
    corner = _rooms(9, "cor")
    all_rooms = standard + corner
    groups = {"2.1": [{"rooms": standard, "qty": 52.0}, {"rooms": corner, "qty": 60.0}]}
    used_df = itemprog.compute(_items_df(), {}, {}, all_rooms, {}, room_qty_groups=groups)
    row = used_df.iloc[0]
    assert row["planned_total"] == pytest.approx(52.0 * 100 + 60.0 * 9)   # 5740
    assert row["planned_total"] == pytest.approx(5740.0)
    assert row["used"] == pytest.approx(0.0)          # nothing tapped yet
    assert row["remaining"] == pytest.approx(5740.0)
    assert row["rooms"] == 109
    assert bool(row["has_room_groups"]) is True


def test_progress_tapped_only_in_standard_rooms_computes_correctly():
    standard = _rooms(100, "std")
    corner = _rooms(9, "cor")
    all_rooms = standard + corner
    groups = {"2.1": [{"rooms": standard, "qty": 52.0}, {"rooms": corner, "qty": 60.0}]}
    # half the standard rooms are fully done, nothing else touched
    prog = {"2.1": {r: 1.0 for r in standard[:50]}}
    used_df = itemprog.compute(_items_df(), prog, {}, all_rooms, {}, room_qty_groups=groups)
    row = used_df.iloc[0]
    assert row["planned_total"] == pytest.approx(5740.0)
    assert row["used"] == pytest.approx(52.0 * 50)      # 2600 -- only the standard group's own qty
    assert row["remaining"] == pytest.approx(5740.0 - 2600.0)
    # quantity-weighted %, not a room-count average (50/109 would be wrong here)
    assert row["progress_pct"] == pytest.approx(100.0 * 2600.0 / 5740.0, abs=0.05)


def test_progress_tapped_in_a_corner_room_weighs_correctly():
    standard = _rooms(100, "std")
    corner = _rooms(9, "cor")
    all_rooms = standard + corner
    groups = {"2.1": [{"rooms": standard, "qty": 52.0}, {"rooms": corner, "qty": 60.0}]}
    # ALL corner rooms done, no standard rooms touched
    prog = {"2.1": {r: 1.0 for r in corner}}
    used_df = itemprog.compute(_items_df(), prog, {}, all_rooms, {}, room_qty_groups=groups)
    row = used_df.iloc[0]
    assert row["used"] == pytest.approx(60.0 * 9)   # 540 -- corner group's own qty, not 52*9
    assert row["remaining"] == pytest.approx(5740.0 - 540.0)


def test_single_room_drilldown_uses_that_rooms_own_group_qty():
    standard = _rooms(2, "std")
    corner = _rooms(1, "cor")
    all_rooms = standard + corner
    groups = {"2.1": [{"rooms": standard, "qty": 52.0}, {"rooms": corner, "qty": 60.0}]}
    prog = {"2.1": {"cor1": 0.5}}
    row_corner = itemprog.compute(_items_df(), prog, {}, all_rooms, {}, room_qty_groups=groups, room="cor1").iloc[0]
    assert row_corner["planned_total"] == pytest.approx(60.0)
    assert row_corner["used"] == pytest.approx(30.0)
    row_std = itemprog.compute(_items_df(), prog, {}, all_rooms, {}, room_qty_groups=groups, room="std1").iloc[0]
    assert row_std["planned_total"] == pytest.approx(52.0)
    assert row_std["used"] == pytest.approx(0.0)   # std1 has no progress entry -> 0


# --------------------------------------------------------------------------
# precedence: room_qty_groups win over a flat planned_over, ALWAYS -- this
# is the actual bug the whole feature was originally built for and initially
# got backwards. A quick-added item's planned_over isn't a deliberate
# correction, it's just how quick items store their quantity by default --
# so if the flat override won, using the Rooms chip to build real per-room
# groups for a quick item (the exact intended workflow) would silently have
# no effect at all, forever, because every quick item always has a
# planned_over set.
# --------------------------------------------------------------------------
def test_groups_win_over_a_stale_flat_override_from_a_quick_item():
    all_rooms = _rooms(5)
    groups = {"QI4": [{"rooms": all_rooms, "qty": 52.0}]}
    # QI4 is a quick item -- planned_over is ALWAYS set for these, from
    # whatever number was typed in when it was first added (here: the exact
    # screenshot scenario, a stale flat "52" that predates any real groups)
    planned_over = {"QI4": 52.0}
    items_df = _items_df(code="QI4")
    row = itemprog.compute(items_df, {}, {}, all_rooms, planned_over, room_qty_groups=groups).iloc[0]
    assert row["planned_total"] == pytest.approx(260.0), "FAIL: groups (5 rooms x 52) must win over the stale flat 52"
    assert bool(row["has_room_groups"]) is True


def test_the_exact_reported_scenario_QI4_52_mtr_quick_item_gets_a_real_total():
    """The literal screenshot: QI4, '25MM MS conduit pipe black', a quick
    item showing '1 of 52 MTR done, 51 remaining' before any groups exist.
    Setting real per-room groups via the Rooms chip must make the Overall
    page's total change from 52 to the real summed figure -- not stay
    pinned at 52 forever because of the quick item's own flat number."""
    standard = _rooms(100, "std")
    corner = _rooms(9, "cor")
    all_rooms = standard + corner
    items_df = _items_df(code="QI4", unit="MTR", qty=0.0)   # quick items have qty_per_room=0
    planned_over = {"QI4": 52.0}   # the stale flat number from when it was added

    # before setting up any groups: matches the real screenshot exactly
    before = itemprog.compute(items_df, {}, {}, all_rooms, planned_over).iloc[0]
    assert before["planned_total"] == pytest.approx(52.0)

    # after using the Rooms chip to set real per-room groups (52 for 100
    # standard rooms, 60 for 9 corner rooms) -- the Overall total must now
    # reflect 5740, not the stale 52
    groups = {"QI4": [{"rooms": standard, "qty": 52.0}, {"rooms": corner, "qty": 60.0}]}
    after = itemprog.compute(items_df, {}, {}, all_rooms, planned_over, room_qty_groups=groups).iloc[0]
    assert after["planned_total"] == pytest.approx(5740.0), \
        "FAIL: this is the exact bug reported against real Hyatt data -- groups must override the quick item's stale flat total"


def test_explicit_planned_override_wins_when_there_are_no_groups_at_all():
    # the OTHER half of the same rule: with no groups, planned_over still
    # works exactly as it always has -- nothing about a REGULAR (non-quick)
    # item's manual override changes when it has no groups to compete with
    all_rooms = _rooms(5)
    planned_over = {"2.1": 999.0}
    row = itemprog.compute(_items_df(), {}, {}, all_rooms, planned_over, room_qty_groups={}).iloc[0]
    assert row["planned_total"] == pytest.approx(999.0)
    assert bool(row["has_room_groups"]) is False


# --------------------------------------------------------------------------
# groups are EXCEPTIONS on top of normal applicability, not a full
# replacement -- a room outside every group still counts, at the item's own
# qty_per_room, exactly like the existing Rooms chip's own stated design
# ("all rooms typical by default; untick/override the exceptions"). Only
# grouping SOME rooms must never silently shrink the item down to just
# those rooms' worth of total.
# --------------------------------------------------------------------------
def test_rooms_outside_every_group_fall_back_to_the_items_own_qty_per_room():
    covered = _rooms(3, "cov")
    uncovered = _rooms(2, "unc")
    all_rooms = covered + uncovered
    groups = {"2.1": [{"rooms": covered, "qty": 10.0}]}
    # qty=4 here is the item's OWN BOQ per-room quantity -- what the 2
    # uncovered rooms should fall back to, not zero and not exclusion
    row = itemprog.compute(_items_df(qty=4.0), {}, {}, all_rooms, {}, room_qty_groups=groups).iloc[0]
    assert row["rooms"] == 5, "FAIL: uncovered rooms must still count as applicable"
    assert row["planned_total"] == pytest.approx(3 * 10.0 + 2 * 4.0), \
        "FAIL: expected 3 grouped rooms @ 10 PLUS 2 ungrouped rooms @ the item's own qty (4), got a different total"


def test_partial_grouping_never_shrinks_below_the_old_uniform_total():
    """A stronger version of the same guarantee: grouping even ONE room
    (leaving everything else on the default) must never produce a SMALLER
    total than the plain, groups-free uniform calculation would have --
    that would be a real regression in planned ₹ value, not just a display
    quirk."""
    all_rooms = _rooms(20)
    uniform = itemprog.compute(_items_df(qty=5.0), {}, {}, all_rooms, {}).iloc[0]
    grouped = itemprog.compute(_items_df(qty=5.0), {}, {}, all_rooms, {},
                               room_qty_groups={"2.1": [{"rooms": ["r1"], "qty": 5.0}]}).iloc[0]
    assert grouped["planned_total"] == pytest.approx(uniform["planned_total"]), \
        "FAIL: grouping one room at the SAME qty as the uniform default must not change the total at all"
    assert grouped["rooms"] == uniform["rooms"] == 20


def test_a_group_can_add_a_room_beyond_the_old_applicability_too():
    """The union direction that matters the other way: if item_rooms.json
    only listed 3 rooms as applicable, but a group references a 4th room
    (e.g. a genuinely new exception the engineer is adding), that room
    becomes applicable too -- groups can widen coverage, not just narrow it."""
    listed = _rooms(3, "listed")
    extra = "extra1"
    all_rooms = listed + [extra]
    rooms_svc = {"2.1": listed}   # old applicability: only the 3 listed rooms
    groups = {"2.1": [{"rooms": [extra], "qty": 99.0}]}
    row = itemprog.compute(_items_df(qty=4.0), {}, rooms_svc, all_rooms, {}, room_qty_groups=groups).iloc[0]
    assert row["rooms"] == 4
    assert row["planned_total"] == pytest.approx(3 * 4.0 + 99.0)


# --------------------------------------------------------------------------
# a room re-ticked into a new group MOVES, never duplicates -- both at the
# raw-store level (set_room_qty_group) and reflected in compute()
# --------------------------------------------------------------------------
def test_set_room_qty_group_moves_a_room_not_duplicates_it():
    store = {}
    itemprog.set_room_qty_group(store, "Electrical", "2.1", ["r1", "r2", "r3"], 52.0)
    itemprog.set_room_qty_group(store, "Electrical", "2.1", ["r2"], 60.0)   # r2 moves to a new group
    groups = store["Electrical"]["2.1"]
    all_rooms_in_groups = [r for g in groups for r in g["rooms"]]
    assert sorted(all_rooms_in_groups) == ["r1", "r2", "r3"], "FAIL: r2 must not appear in two groups at once"
    r2_group = next(g for g in groups if "r2" in g["rooms"])
    assert r2_group["qty"] == pytest.approx(60.0)
    r1_group = next(g for g in groups if "r1" in g["rooms"])
    assert r1_group["qty"] == pytest.approx(52.0)


def test_set_room_qty_group_with_no_qty_removes_rooms_cleanly():
    store = {}
    itemprog.set_room_qty_group(store, "Electrical", "2.1", ["r1", "r2"], 52.0)
    itemprog.set_room_qty_group(store, "Electrical", "2.1", ["r1"], None)   # remove r1
    groups = store["Electrical"]["2.1"]
    all_rooms_in_groups = [r for g in groups for r in g["rooms"]]
    assert all_rooms_in_groups == ["r2"]


def test_set_room_qty_group_removing_every_room_deletes_the_item_entry():
    store = {}
    itemprog.set_room_qty_group(store, "Electrical", "2.1", ["r1"], 52.0)
    itemprog.set_room_qty_group(store, "Electrical", "2.1", ["r1"], None)
    assert "2.1" not in store.get("Electrical", {})


def test_end_to_end_two_saves_then_compute_matches_the_motivating_example():
    """The actual workflow end to end: save 100 rooms @ 52, then save 9
    (different) rooms @ 60, then compute() sees the same 5740 total."""
    store = {}
    standard = _rooms(100, "std")
    corner = _rooms(9, "cor")
    itemprog.set_room_qty_group(store, "Electrical", "2.1", standard, 52.0)
    itemprog.set_room_qty_group(store, "Electrical", "2.1", corner, 60.0)
    row = itemprog.compute(_items_df(), {}, {}, standard + corner, {},
                           room_qty_groups=store["Electrical"]).iloc[0]
    assert row["planned_total"] == pytest.approx(5740.0)


# --------------------------------------------------------------------------
# room_buckets() and project_room_status() must use a grouped item's real
# (group-union) applicability, not item_rooms.json
# --------------------------------------------------------------------------
def test_room_buckets_uses_group_union_not_item_rooms_json():
    standard = _rooms(3, "std")
    corner = _rooms(2, "cor")
    all_rooms = standard + corner
    groups = {"2.1": [{"rooms": standard, "qty": 52.0}, {"rooms": corner, "qty": 60.0}]}
    prog = {"2.1": {r: 1.0 for r in standard}}   # standard done, corner not started
    b = itemprog.room_buckets("2.1", prog, {}, all_rooms, room_qty_groups=groups)
    assert b == {"done": 3, "in_progress": 0, "not_started": 2, "total": 5}


def test_project_room_status_5tuple_form_respects_groups():
    standard = _rooms(2, "std")
    corner = _rooms(1, "cor")
    all_rooms = standard + corner
    groups = {"2.1": [{"rooms": standard, "qty": 52.0}, {"rooms": corner, "qty": 60.0}]}
    prog = {"2.1": {r: 1.0 for r in standard + corner}}   # everything done
    items_df = _items_df()
    entry = (items_df, prog, {}, {"2.1"}, groups)
    out = itemprog.project_room_status([entry], all_rooms)
    assert out == {"done": 3, "in_progress": 0, "not_started": 0, "total": 3}


def test_project_room_status_4tuple_form_still_works_unchanged():
    # the OLD call shape (no groups) must still work exactly as before
    all_rooms = _rooms(3)
    items_df = _items_df()
    prog = {"2.1": {"r1": 1.0}}
    entry = (items_df, prog, {}, {"2.1"})   # 4-tuple, no groups
    out = itemprog.project_room_status([entry], all_rooms)
    assert out["total"] == 3
    assert out["done"] == 1


# --------------------------------------------------------------------------
# backward compatibility: an item with NO groups behaves byte-identically
# to the pre-existing uniform path, at every call site
# --------------------------------------------------------------------------
def test_no_groups_defined_is_byte_identical_to_the_old_uniform_path():
    all_rooms = _rooms(10)
    items_df = _items_df(qty=5.0)   # 5 MTR per room, uniform
    prog = {"2.1": {"*": 0.4}}
    old = itemprog.compute(items_df, prog, {}, all_rooms, {})
    new = itemprog.compute(items_df, prog, {}, all_rooms, {}, room_qty_groups={})
    pd.testing.assert_frame_equal(old, new)
    new2 = itemprog.compute(items_df, prog, {}, all_rooms, {}, room_qty_groups=None)
    pd.testing.assert_frame_equal(old, new2)
    assert bool(old.iloc[0]["has_room_groups"]) is False


def test_room_buckets_no_groups_matches_old_behaviour():
    all_rooms = _rooms(10)
    prog = {"2.1": {"*": 1.0}}
    rooms_svc = {"2.1": all_rooms[:5]}
    old = itemprog.room_buckets("2.1", prog, rooms_svc, all_rooms)
    new = itemprog.room_buckets("2.1", prog, rooms_svc, all_rooms, room_qty_groups={})
    assert old == new


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
