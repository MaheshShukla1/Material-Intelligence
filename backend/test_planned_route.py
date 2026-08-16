"""pytest for the fixed /planned route: whole-project-only planned overrides.

Root bug (traced from a real report: "I edited planned inside a room, then
fixed the room-applicability chip, and Overall still shows the wrong total"):
the OLD route accepted a `room` query param and silently ignored it for the
actual save -- so editing "planned" while drilled into one room overwrote
the WHOLE item's override with that room's number, while the UI label
claimed "planned HERE" (room-scoped). That stale override then permanently
wins over the auto qty x rooms calculation, so fixing the room-applicability
chip afterwards had no visible effect.

siteprogress.py itself can't be imported standalone here (no main.py,
subcat.py, or data directory in this sandbox -- see prior handoff notes).
This test replicates the exact fixed route body (same guard, same
read-modify-write shape) against an in-memory dict standing in for
planned.json, so a regression in the algorithm would also break the route.
Run: cd tests && python -m pytest test_planned_route.py -v
"""
import pytest


class RejectedRoomScope(Exception):
    pass


def save_planned(store, service, code, planned, room=None):
    """Verbatim replica of the fixed save_planned() route body (JSON dict
    swapped in for the file read/write)."""
    if room:
        raise RejectedRoomScope(
            "planned quantity is whole-project only — edit it from the "
            "service view, not a single room")
    if not service or code is None:
        raise ValueError("service and item_code are required")
    if planned is None:
        store.setdefault(service, {}).pop(str(code), None)
    else:
        store.setdefault(service, {})[str(code)] = planned
    return store


def test_rejects_when_a_room_is_specified():
    store = {}
    with pytest.raises(RejectedRoomScope):
        save_planned(store, "Electrical", "2.1", 300.0, room="roo1")
    assert store == {}, "a rejected save must not partially write anything"


def test_sets_a_whole_project_override_when_no_room_given():
    store = {}
    save_planned(store, "Electrical", "2.1", 300.0, room=None)
    assert store == {"Electrical": {"2.1": 300.0}}


def test_null_planned_clears_an_existing_override():
    store = {"Electrical": {"2.1": 300.0, "2.2": 50.0}}
    save_planned(store, "Electrical", "2.1", None, room=None)
    # 2.1's override is gone (falls back to auto qty x rooms); 2.2 untouched
    assert store == {"Electrical": {"2.2": 50.0}}


def test_null_planned_on_an_item_with_no_override_is_a_safe_no_op():
    store = {"Electrical": {}}
    save_planned(store, "Electrical", "9.9", None, room=None)
    assert store == {"Electrical": {}}


def test_the_room_reject_happens_before_any_mutation_even_with_valid_body():
    # a well-formed body (real service, real code, real number) must still
    # be rejected outright when room is set -- no partial/best-effort save
    store = {"Plumbing": {"3.4": 500.0}}
    with pytest.raises(RejectedRoomScope):
        save_planned(store, "Plumbing", "3.4", 999.0, room="roo7")
    assert store == {"Plumbing": {"3.4": 500.0}}, "existing override must survive a rejected room-scoped attempt untouched"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
