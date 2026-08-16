"""pytest for _prune_deleted_rooms() -- the cleanup that runs whenever the
structure tree changes (a room deleted, or a full "Rebuild"). Real bug this
closes: deleting one room from a 109-room hotel left that room's id sitting
in item_room_qty.json's groups, item_rooms.json's applicability lists, and
item_progress.json's per-room overrides forever. compute() itself was
always safe (every room reference gets filtered against the CURRENT room
set before use) -- but anything that just DISPLAYS the raw stored data
(the Rooms modal's "current groups" summary, the 🏠 chip's room count)
wasn't filtered the same way, so it kept showing "109 of 108 rooms" after
deleting exactly one room -- one group still listing the room that no
longer exists.

siteprogress.py can't be imported standalone here (see prior handoff
notes) -- this replicates the route body verbatim against in-memory dicts
standing in for the three JSON stores.

Run: cd tests && python -m pytest test_prune_deleted_rooms.py -v
"""
import json


def prune_deleted_rooms(groups_store, rooms_store, prog_store, valid_room_ids):
    """Verbatim replica of _prune_deleted_rooms(), swapping direct dict
    mutation in for the file read/write (same logic, same shape)."""
    for svc, items in groups_store.items():
        for code, groups in list(items.items()):
            kept = []
            for g in groups:
                remaining = [r for r in g.get("rooms", []) if r in valid_room_ids]
                if remaining:
                    kept.append({"rooms": remaining, "qty": g.get("qty")})
            if kept:
                items[code] = kept
            else:
                items.pop(code, None)

    for svc, items in rooms_store.items():
        for code, ids in list(items.items()):
            kept = [r for r in ids if r in valid_room_ids]
            if kept:
                items[code] = kept
            else:
                items.pop(code, None)

    for svc, items in prog_store.items():
        for code, node in items.items():
            for r in [k for k in node.keys() if k != "*" and k not in valid_room_ids]:
                node.pop(r, None)

    return groups_store, rooms_store, prog_store


# --------------------------------------------------------------------------
# the exact reported scenario: 109 rooms, delete one, groups still list it
# --------------------------------------------------------------------------
def test_the_exact_reported_scenario_one_room_deleted_from_109():
    all_before = [f"r{i}" for i in range(1, 110)]     # 109 rooms
    all_after = all_before[:-1]                        # 108 rooms (last one deleted)
    groups = {"Electrical": {"QI5": [
        {"rooms": [all_before[-1]], "qty": 4.0},        # "1 room" group -- the deleted one
        {"rooms": all_before[:-1], "qty": 4.0},         # "108 rooms" group -- still valid
    ]}}
    rooms_store, prog_store = {}, {}
    prune_deleted_rooms(groups, rooms_store, prog_store, set(all_after))
    remaining_groups = groups["Electrical"]["QI5"]
    total_rooms_in_groups = sum(len(g["rooms"]) for g in remaining_groups)
    assert total_rooms_in_groups == 108, \
        f"FAIL: expected exactly 108 rooms across groups after pruning, got {total_rooms_in_groups} -- this is literally '109 of 108 rooms' if it fails"
    assert len(remaining_groups) == 1, "FAIL: the now-empty 'deleted room' group should be dropped entirely, not left as an empty group"
    assert remaining_groups[0]["qty"] == 4.0


def test_group_entirely_on_deleted_rooms_is_removed_completely():
    groups = {"Electrical": {"QI9": [{"rooms": ["ghost1", "ghost2"], "qty": 10.0}]}}
    prune_deleted_rooms(groups, {}, {}, {"real1", "real2"})
    assert "QI9" not in groups["Electrical"], "FAIL: an item whose ENTIRE group referenced only deleted rooms should have no entry left at all"


def test_partially_stale_group_keeps_only_the_valid_rooms():
    groups = {"Electrical": {"QI5": [{"rooms": ["r1", "r2", "ghost"], "qty": 4.0}]}}
    prune_deleted_rooms(groups, {}, {}, {"r1", "r2"})
    assert groups["Electrical"]["QI5"] == [{"rooms": ["r1", "r2"], "qty": 4.0}]


def test_item_rooms_applicability_lists_get_pruned_too():
    rooms_store = {"Electrical": {"5.5": ["r1", "r2", "ghost"]}}
    prune_deleted_rooms({}, rooms_store, {}, {"r1", "r2"})
    assert rooms_store["Electrical"]["5.5"] == ["r1", "r2"]


def test_item_rooms_entirely_stale_is_removed():
    rooms_store = {"Electrical": {"5.5": ["ghost1", "ghost2"]}}
    prune_deleted_rooms({}, rooms_store, {}, {"real1"})
    assert "5.5" not in rooms_store["Electrical"]


def test_item_progress_stale_per_room_overrides_get_pruned():
    prog_store = {"Electrical": {"2.1": {"*": 0.5, "r1": 1.0, "ghost": 0.8}}}
    prune_deleted_rooms({}, {}, prog_store, {"r1"})
    node = prog_store["Electrical"]["2.1"]
    assert node == {"*": 0.5, "r1": 1.0}, "FAIL: the overall '*' fraction must survive; only the stale per-room key should go"


def test_a_rebuild_that_replaces_every_room_id_prunes_everything_cleanly():
    """The 'Rebuild' action assigns a fresh id sequence -- essentially every
    old reference is stale at once. Must not crash, and must leave a clean,
    fully-empty (not half-broken) state."""
    old_rooms = [f"old{i}" for i in range(1, 20)]
    new_rooms = {f"new{i}" for i in range(1, 8)}   # genuinely disjoint id namespace, not just fewer of the same ids
    groups = {"Electrical": {"QI5": [{"rooms": old_rooms, "qty": 4.0}]}}
    rooms_store = {"Electrical": {"5.5": old_rooms}}
    prog_store = {"Electrical": {"2.1": {"*": 0.5, **{r: 1.0 for r in old_rooms}}}}
    prune_deleted_rooms(groups, rooms_store, prog_store, new_rooms)
    assert "QI5" not in groups["Electrical"]
    assert "5.5" not in rooms_store["Electrical"]
    assert prog_store["Electrical"]["2.1"] == {"*": 0.5}


def test_nothing_stale_is_a_true_no_op():
    groups = {"Electrical": {"QI5": [{"rooms": ["r1", "r2"], "qty": 4.0}]}}
    before = json.loads(json.dumps(groups))
    prune_deleted_rooms(groups, {}, {}, {"r1", "r2", "r3"})
    assert groups == before


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
