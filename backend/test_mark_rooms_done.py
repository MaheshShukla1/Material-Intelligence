"""Real-logic pytest suite for /mark-rooms-done: the companion action to
/item-room-qty in the Rooms modal -- tick rooms, mark them done, without
touching the item's overall '*' slider (which would wipe other rooms'
per-room overrides -- the actual reported accident this route prevents).
"""
import json
import shutil
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backend import siteprogress as sp   # noqa: E402
from backend import structure as struct_mod   # noqa: E402


SLUG = "hyatt-hotel"


def _clear_all_caches():
    sp._read_forecast_parquet_cached.cache_clear()
    sp._full_run_rows_cached.cache_clear()
    sp._forecast_pool_cached.cache_clear()
    sp._linkage_match_cached.cache_clear()


@pytest.fixture(autouse=True)
def clean_project():
    _clear_all_caches()
    d = sp.PROJECTS / SLUG
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    if sp.RUNS.exists():
        shutil.rmtree(sp.RUNS)
    sp.RUNS.mkdir(parents=True)
    yield d
    _clear_all_caches()


def _write_json(d, name, obj):
    (d / name).write_text(json.dumps(obj, ensure_ascii=False))


def _boq_df(rows):
    recs = [{**r, "item_code_raw": r["item_code"], "section": "Main",
            "subcategory": "Other"} for r in rows]
    return pd.DataFrame(recs, columns=["service", "item_code", "item_code_raw",
                                       "description", "unit", "qty",
                                       "section", "subcategory"])


def _setup(d, n_rooms=6):
    s = struct_mod.hotel("Test Hotel", floors=["F1"],
                         room_labels=[f"Room {i}" for i in range(1, n_rooms + 1)])
    (d / "structure.json").write_text(s.to_json())
    room_ids = [r["id"] for r in s.rooms()]
    _boq_df([{"service": "HVAC", "item_code": "QI1", "description": "32MM PIPE",
             "unit": "MTR", "qty": 2.5}]).to_parquet(d / "boq.parquet")
    _write_json(d, "activities.json", {"HVAC": ["CHW Piping"]})
    _write_json(d, "mapping.json", {"HVAC": {"CHW Piping": ["QI1"]}})
    return room_ids


class TestMarkRoomsDone:
    def test_marks_only_the_ticked_rooms(self, clean_project):
        d = clean_project
        rooms = _setup(d)
        sp.mark_rooms_done(SLUG, {"service": "HVAC", "item_code": "QI1", "rooms": rooms[:2]})
        raw = sp._item_prog(d)["HVAC"]["QI1"]
        assert raw[rooms[0]] == 1.0
        assert raw[rooms[1]] == 1.0
        assert rooms[2] not in raw   # untouched, not silently set to anything

    def test_reflected_in_room_buckets(self, clean_project):
        d = clean_project
        rooms = _setup(d)
        sp.mark_rooms_done(SLUG, {"service": "HVAC", "item_code": "QI1", "rooms": rooms[:3]})
        view = sp._service_view(d, "HVAC")
        row = next(r for r in view["items"] if r["code"] == "QI1")
        assert row["room_done"] == 3
        assert row["room_pending"] == 3   # the other 3 rooms, untouched -> not_started

    def test_does_not_wipe_other_rooms_progress(self, clean_project):
        """The actual accident this route exists to prevent: marking a NEW
        set of rooms done must never disturb rooms that were already at
        some other fraction (partial or done) via earlier per-room edits."""
        d = clean_project
        rooms = _setup(d)
        # room 0 already at 40% from an earlier per-room edit
        sp.set_item_progress(SLUG, {"service": "HVAC", "item_code": "QI1",
                                    "frac": 0.4, "room": rooms[0]})
        sp.mark_rooms_done(SLUG, {"service": "HVAC", "item_code": "QI1", "rooms": [rooms[1], rooms[2]]})
        raw = sp._item_prog(d)["HVAC"]["QI1"]
        assert raw[rooms[0]] == 0.4   # completely undisturbed
        assert raw[rooms[1]] == 1.0
        assert raw[rooms[2]] == 1.0

    def test_does_not_touch_other_items(self, clean_project):
        d = clean_project
        rooms = _setup(d)
        _write_json(d, "item_progress.json",
                   {"HVAC": {"QI1": {"*": 0.0}, "QI2": {rooms[0]: 0.5}}})
        sp.mark_rooms_done(SLUG, {"service": "HVAC", "item_code": "QI1", "rooms": [rooms[0]]})
        raw = sp._item_prog(d)["HVAC"]
        assert raw["QI2"][rooms[0]] == 0.5   # a different item's data, untouched

    def test_re_marking_an_already_done_room_is_a_harmless_noop(self, clean_project):
        d = clean_project
        rooms = _setup(d)
        sp.mark_rooms_done(SLUG, {"service": "HVAC", "item_code": "QI1", "rooms": [rooms[0]]})
        out = sp.mark_rooms_done(SLUG, {"service": "HVAC", "item_code": "QI1", "rooms": [rooms[0]]})
        row = next(r for r in out["items"] if r["code"] == "QI1")
        assert row["room_done"] == 1

    def test_requires_at_least_one_room(self, clean_project):
        d = clean_project
        _setup(d)
        with pytest.raises(Exception):
            sp.mark_rooms_done(SLUG, {"service": "HVAC", "item_code": "QI1", "rooms": []})

    def test_requires_service_and_item_code(self, clean_project):
        d = clean_project
        rooms = _setup(d)
        with pytest.raises(Exception):
            sp.mark_rooms_done(SLUG, {"item_code": "QI1", "rooms": rooms[:1]})
        with pytest.raises(Exception):
            sp.mark_rooms_done(SLUG, {"service": "HVAC", "rooms": rooms[:1]})


class TestItemProgressExposedToFrontend:
    """The service view now includes the raw item_progress store (same
    pattern as item_rooms/item_room_qty already being exposed raw) so the
    Rooms modal can show which specific rooms are already done."""

    def test_service_view_includes_item_progress(self, clean_project):
        d = clean_project
        rooms = _setup(d)
        sp.mark_rooms_done(SLUG, {"service": "HVAC", "item_code": "QI1", "rooms": [rooms[0]]})
        view = sp._service_view(d, "HVAC")
        assert "item_progress" in view
        assert view["item_progress"]["QI1"][rooms[0]] == 1.0


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
