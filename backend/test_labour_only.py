"""Real-logic pytest suite for the labour-only activity progress feature
(Zari work, core-cutting, chasing, testing -- activities with no BOQ
material at all). Calls the actual siteprogress.py route functions
directly, same pattern as test_bugfixes.py / test_performance.py.
"""
import json
import shutil
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backend import siteprogress as sp   # noqa: E402
from backend import structure as struct_mod, activity as activity_mod   # noqa: E402


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


def _setup(d, n_items=4, n_rooms=5, extra_activities=None):
    """Electrical service with a normal item-tracked activity (Wire Pulling)
    and a genuinely material-less one (Zari Work), plus real structure so
    room-scoped calls have something to key against."""
    s = struct_mod.hotel("Test Hotel", floors=["F1"],
                         room_labels=[f"Room {i}" for i in range(1, n_rooms + 1)])
    (d / "structure.json").write_text(s.to_json())
    room_ids = [r["id"] for r in s.rooms()]

    rows = [{"service": "Electrical", "item_code": f"E.{i}",
             "description": f"3x1.5 sqmm wire line {i}", "unit": "MTR",
             "qty": 10.0} for i in range(1, n_items + 1)]
    _boq_df(rows).to_parquet(d / "boq.parquet")

    acts = ["Wire Pulling", "Zari Work"] + (extra_activities or [])
    _write_json(d, "activities.json", {"Electrical": acts})
    _write_json(d, "mapping.json",
               {"Electrical": {"Wire Pulling": [f"E.{i}" for i in range(1, n_items + 1)],
                               "Zari Work": []}})
    _write_json(d, "rates.json", {"Electrical": {f"E.{i}": 100.0 for i in range(1, n_items + 1)}})
    return room_ids


# --------------------------------------------------------- keyword suggestion
class TestLabourKeywordSuggestion:
    def test_zari_and_core_cutting_are_recognised(self):
        assert activity_mod.is_labour_keyword("Zari Work")
        assert activity_mod.is_labour_keyword("Core Cutting - Plumbing")
        assert activity_mod.is_labour_keyword("Chasing & Grooving")
        assert activity_mod.is_labour_keyword("Pressure Testing")

    def test_normal_material_activities_are_not(self):
        assert not activity_mod.is_labour_keyword("Wire Pulling")
        assert not activity_mod.is_labour_keyword("Wall Piping")
        assert not activity_mod.is_labour_keyword("Metal Box")

    def test_service_view_surfaces_the_suggestion_per_activity(self, clean_project):
        d = clean_project
        _setup(d)
        view = sp._service_view(d, "Electrical")
        assert view["labour_suggested"]["Zari Work"] is True
        assert view["labour_suggested"]["Wire Pulling"] is False


# ------------------------------------------------------------- toggle on/off
class TestLabourToggle:
    def test_toggle_on_with_zero_items_succeeds(self, clean_project):
        d = clean_project
        _setup(d)
        out = sp.set_activity_labour(SLUG, {"service": "Electrical", "activity": "Zari Work", "on": True})
        assert out["labour_only"]["Zari Work"] is True
        assert out["labour_only"]["Wire Pulling"] is False

    def test_toggle_on_refused_when_items_are_mapped(self, clean_project):
        d = clean_project
        _setup(d)
        with pytest.raises(Exception):
            sp.set_activity_labour(SLUG, {"service": "Electrical", "activity": "Wire Pulling", "on": True})
        # untouched: Wire Pulling must still be item-tracked, not silently flipped
        view = sp._service_view(d, "Electrical")
        assert view["labour_only"]["Wire Pulling"] is False

    def test_toggle_off_preserves_recorded_progress(self, clean_project):
        d = clean_project
        _setup(d)
        sp.set_activity_labour(SLUG, {"service": "Electrical", "activity": "Zari Work", "on": True})
        sp.set_activity_progress(SLUG, {"service": "Electrical", "activity": "Zari Work", "frac": 0.6})
        sp.set_activity_labour(SLUG, {"service": "Electrical", "activity": "Zari Work", "on": False})
        # the raw store still has the 60% -- turning off only stops counting it
        raw = sp._activity_prog(d)
        assert raw["Electrical"]["Zari Work"]["*"] == 0.6
        view = sp._service_view(d, "Electrical")
        assert view["labour_only"]["Zari Work"] is False
        assert view["act_pct"]["Zari Work"] is None   # back to item-based (0 items -> dash)

        # and turning it back on again resumes showing the preserved value
        sp.set_activity_labour(SLUG, {"service": "Electrical", "activity": "Zari Work", "on": True})
        view2 = sp._service_view(d, "Electrical")
        assert view2["act_pct"]["Zari Work"] == 60.0


# --------------------------------------------------------------- progress %
class TestLabourProgress:
    def test_set_and_read_back(self, clean_project):
        d = clean_project
        _setup(d)
        sp.set_activity_labour(SLUG, {"service": "Electrical", "activity": "Zari Work", "on": True})
        out = sp.set_activity_progress(SLUG, {"service": "Electrical", "activity": "Zari Work", "frac": 0.35})
        assert out["act_pct"]["Zari Work"] == 35.0
        assert out["labour_pct"]["Zari Work"] == 35.0

    def test_frac_is_clamped(self, clean_project):
        d = clean_project
        _setup(d)
        sp.set_activity_labour(SLUG, {"service": "Electrical", "activity": "Zari Work", "on": True})
        out = sp.set_activity_progress(SLUG, {"service": "Electrical", "activity": "Zari Work", "frac": 1.7})
        assert out["act_pct"]["Zari Work"] == 100.0

    def test_untouched_labour_activity_shows_zero_not_dash(self, clean_project):
        """The whole point of the feature: a TOGGLED-ON labour activity with
        no recorded value yet is a real, trackable 0% -- distinct from an
        activity that hasn't been configured at all (which stays None/dash)."""
        d = clean_project
        _setup(d)
        sp.set_activity_labour(SLUG, {"service": "Electrical", "activity": "Zari Work", "on": True})
        view = sp._service_view(d, "Electrical")
        assert view["act_pct"]["Zari Work"] == 0.0
        assert view["act_pct"]["Wire Pulling"] is not None   # has items -> real item-based number

    def test_room_scoped_progress(self, clean_project):
        d = clean_project
        room_ids = _setup(d)
        sp.set_activity_labour(SLUG, {"service": "Electrical", "activity": "Zari Work", "on": True})
        sp.set_activity_progress(SLUG, {"service": "Electrical", "activity": "Zari Work", "frac": 0.5})
        sp.set_activity_progress(SLUG, {"service": "Electrical", "activity": "Zari Work",
                                        "frac": 0.9, "room": room_ids[0]})
        view_room = sp._service_view(d, "Electrical", room=room_ids[0])
        assert view_room["act_pct"]["Zari Work"] == 90.0
        view_other_room = sp._service_view(d, "Electrical", room=room_ids[1])
        assert view_other_room["act_pct"]["Zari Work"] == 50.0   # falls back to "*"


# -------------------------------------------------------- rollup consistency
class TestRollupConsistency:
    """The numbers this feature feeds into (service overall %, project
    overall %, and every rupee figure) must stay internally consistent --
    this is what the earlier /overall refactor's own tests protect for
    item-based numbers; this class protects the same invariant now that
    labour-only activities are a second kind of contributor."""

    def test_labour_activity_never_contributes_money(self, clean_project):
        d = clean_project
        _setup(d)
        sp.set_activity_labour(SLUG, {"service": "Electrical", "activity": "Zari Work", "on": True})
        sp.set_activity_progress(SLUG, {"service": "Electrical", "activity": "Zari Work", "frac": 0.8})
        view = sp._service_view(d, "Electrical")
        zari_pnl = view["pnl_by_activity"]["Zari Work"]
        assert zari_pnl["planned_value"] == 0.0
        assert zari_pnl["done_value"] == 0.0
        assert zari_pnl["items"] == 0

    def test_service_overall_pct_includes_labour_activity(self, clean_project):
        d = clean_project
        _setup(d, n_items=4)
        # all 4 Wire Pulling items at 100% -> item-based overall would be 100%
        for i in range(1, 5):
            sp.set_item_progress(SLUG, {"service": "Electrical", "item_code": f"E.{i}", "frac": 1.0})
        sp.set_activity_labour(SLUG, {"service": "Electrical", "activity": "Zari Work", "on": True})
        sp.set_activity_progress(SLUG, {"service": "Electrical", "activity": "Zari Work", "frac": 0.0})
        view = sp._service_view(d, "Electrical")
        # 4 items at 100% + Zari Work at 0% (one more data point in the same
        # flat average) -> 400/5 = 80%, not 100%
        assert view["overall_pct"] == 80.0

    def test_project_overall_pct_includes_labour_activity(self, clean_project):
        d = clean_project
        _setup(d, n_items=4)
        for i in range(1, 5):
            sp.set_item_progress(SLUG, {"service": "Electrical", "item_code": f"E.{i}", "frac": 1.0})
        sp.set_activity_labour(SLUG, {"service": "Electrical", "activity": "Zari Work", "on": True})
        sp.set_activity_progress(SLUG, {"service": "Electrical", "activity": "Zari Work", "frac": 0.0})
        result = sp.overall(SLUG)
        assert result["overall_pct"] == 80.0   # same maths, at the project level

    def test_overall_route_does_not_crash_with_labour_activities(self, clean_project):
        d = clean_project
        _setup(d)
        sp.set_activity_labour(SLUG, {"service": "Electrical", "activity": "Zari Work", "on": True})
        sp.set_activity_progress(SLUG, {"service": "Electrical", "activity": "Zari Work", "frac": 0.4})
        result = sp.overall(SLUG)
        assert result["by_service"]["Electrical"]["items"] == 4   # unaffected -- Zari has no items


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
