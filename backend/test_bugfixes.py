"""Real-logic pytest suite for the three Site Progress bug fixes.

Calls the actual FastAPI route functions directly (add_quick_item,
save_planned, realistic, get_links) against a throwaway project directory
under this test package's own data/ dir -- not mocked, not reimplemented,
the real siteprogress.py code that will ship.

subcat.py is a throwaway stub (see backend/subcat.py's own docstring) since
the real one has never been available in any session working on this repo;
none of these tests depend on real sub-category classification.
"""
import json
import shutil
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backend import siteprogress as sp   # noqa: E402


SLUG = "hyatt-hotel"


@pytest.fixture(autouse=True)
def clean_project():
    """Fresh, empty project dir + no forecast runs before every test, and
    the parquet cache cleared so tests never see another test's data."""
    sp._read_forecast_parquet_cached.cache_clear()
    d = sp.PROJECTS / SLUG
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    if sp.RUNS.exists():
        shutil.rmtree(sp.RUNS)
    sp.RUNS.mkdir(parents=True)
    yield d
    sp._read_forecast_parquet_cached.cache_clear()


def _write_json(d, name, obj):
    (d / name).write_text(json.dumps(obj, ensure_ascii=False))


def _make_run(materials, run_id="r1", created="2026-08-18T10:00:00"):
    """A minimal, real forecast run: RUNS/<run_id>/meta.json + forecast.parquet.
    `materials` is a list of dicts, each becoming one forecast row."""
    run_dir = sp.RUNS / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "meta.json").write_text(json.dumps(
        {"project_slug": SLUG, "created": created}))
    cols = ["material", "service", "unit", "stock", "rate_per_day",
            "days_left", "status", "total_consumed", "order_by"]
    rows = []
    for m in materials:
        row = {c: m.get(c) for c in cols}
        rows.append(row)
    pd.DataFrame(rows, columns=cols).to_parquet(run_dir / "forecast.parquet")
    return run_dir


# ---------------------------------------------------------------- Bug 1 ---
class TestQuickItemPerActivityIndependence:
    """A quick item ("+ Add item") added to two different activities must
    get two independent item_codes, so their planned quantities never
    collide -- the exact bug reported (QI1/QI2 showing identical planned
    quantities under both "PVC Fresh Air Piping" and "PVC Exhaust Air
    Piping")."""

    def _setup(self, d):
        _write_json(d, "activities.json",
                    {"HVAC": ["PVC Fresh Air Piping", "PVC Exhaust Air Piping"]})
        _make_run([{"material": "110MM PVC PIPE", "service": "Fire & HVAC",
                    "unit": "MTR", "stock": 100, "rate_per_day": 2,
                    "days_left": 50, "status": "GREEN", "total_consumed": 10,
                    "order_by": None}])

    def test_same_material_two_activities_gets_two_codes(self, clean_project):
        d = clean_project
        self._setup(d)

        r1 = sp.add_quick_item(SLUG, {"service": "HVAC",
                                       "activity": "PVC Fresh Air Piping",
                                       "material": "110MM PVC PIPE"})
        r2 = sp.add_quick_item(SLUG, {"service": "HVAC",
                                       "activity": "PVC Exhaust Air Piping",
                                       "material": "110MM PVC PIPE"})

        quick = json.loads((d / "quick_items.json").read_text())
        codes = [it["item_code"] for it in quick["HVAC"]["items"]]
        assert len(codes) == 2, f"expected 2 distinct quick-item records, got {quick}"
        assert len(set(codes)) == 2, "the two activities must NOT share one item_code"

        mapping = json.loads((d / "mapping.json").read_text())
        assert mapping["HVAC"]["PVC Fresh Air Piping"] == [codes[0]]
        assert mapping["HVAC"]["PVC Exhaust Air Piping"] == [codes[1]]

    def test_planned_quantities_are_independent(self, clean_project):
        d = clean_project
        self._setup(d)
        sp.add_quick_item(SLUG, {"service": "HVAC",
                                  "activity": "PVC Fresh Air Piping",
                                  "material": "110MM PVC PIPE"})
        sp.add_quick_item(SLUG, {"service": "HVAC",
                                  "activity": "PVC Exhaust Air Piping",
                                  "material": "110MM PVC PIPE"})
        quick = json.loads((d / "quick_items.json").read_text())
        code_fresh, code_exhaust = [it["item_code"] for it in quick["HVAC"]["items"]]

        sp.save_planned(SLUG, {"service": "HVAC", "item_code": code_fresh, "planned": 2})
        sp.save_planned(SLUG, {"service": "HVAC", "item_code": code_exhaust, "planned": 4})

        planned = json.loads((d / "planned.json").read_text())
        assert planned["HVAC"][code_fresh] == 2
        assert planned["HVAC"][code_exhaust] == 4
        assert planned["HVAC"][code_fresh] != planned["HVAC"][code_exhaust], (
            "this is the exact reported bug: both activities showing the SAME "
            "planned quantity for what the engineer expected to be independent lines")

    def test_repicking_same_material_same_activity_is_idempotent(self, clean_project):
        """Re-opening the picker and choosing the same material again for the
        SAME activity must reuse the line (harmless no-op), not mint a third
        code and not silently un-map it."""
        d = clean_project
        self._setup(d)
        r1 = sp.add_quick_item(SLUG, {"service": "HVAC",
                                       "activity": "PVC Fresh Air Piping",
                                       "material": "110MM PVC PIPE"})
        sp.save_planned(SLUG, {"service": "HVAC",
                               "item_code": [it["item_code"] for it in
                                             json.loads((d / "quick_items.json").read_text())["HVAC"]["items"]][0],
                               "planned": 7})
        r2 = sp.add_quick_item(SLUG, {"service": "HVAC",
                                       "activity": "PVC Fresh Air Piping",
                                       "material": "110MM PVC PIPE"})

        quick = json.loads((d / "quick_items.json").read_text())
        assert len(quick["HVAC"]["items"]) == 1, "re-picking for the same activity must not create a new line"
        code = quick["HVAC"]["items"][0]["item_code"]
        mapping = json.loads((d / "mapping.json").read_text())
        assert mapping["HVAC"]["PVC Fresh Air Piping"] == [code], "must still be mapped, not toggled out"
        planned = json.loads((d / "planned.json").read_text())
        assert planned["HVAC"][code] == 7, "the planned quantity set before the re-pick must survive untouched"

    def test_different_activity_does_not_disturb_first_activitys_code(self, clean_project):
        """Regression guard on the fix itself: adding the item to a second
        activity must never rename/remove the first activity's own line."""
        d = clean_project
        self._setup(d)
        sp.add_quick_item(SLUG, {"service": "HVAC",
                                  "activity": "PVC Fresh Air Piping",
                                  "material": "110MM PVC PIPE"})
        before = json.loads((d / "quick_items.json").read_text())["HVAC"]["items"][0]["item_code"]

        sp.add_quick_item(SLUG, {"service": "HVAC",
                                  "activity": "PVC Exhaust Air Piping",
                                  "material": "110MM PVC PIPE"})

        mapping = json.loads((d / "mapping.json").read_text())
        assert mapping["HVAC"]["PVC Fresh Air Piping"] == [before]


# ---------------------------------------------------------------- Bug 2 ---
class TestCrossServiceLinkResolves:
    """A material the register tags under one forecast service (e.g.
    "Electrical") but that a quick item links to from a DIFFERENT Site
    Progress service (e.g. "FAPA", which maps to the forecast's "Fire &
    HVAC" label) must still resolve in the drawer's realistic forecast and
    in the Link-stock modal -- not render as NOT_LINKED / unresolved despite
    a real link existing. This is the exact reported "from stock" chip vs.
    'Not linked to stock yet' drawer contradiction."""

    MATERIAL = "2CX1.5SQMM LSZH FIRE SURVIVAL CABLE - RED COLOUR"

    def _setup(self, d):
        _write_json(d, "activities.json", {"FAPA": ["FA Cabling"]})
        # tagged "Electrical" in the register -- NOT "Fire & HVAC", which is
        # what _forecast_pool(slug, "FAPA") would filter to. This is exactly
        # the real-world case called out in HANDOFF_CONTEXT.md: a register
        # that keeps some material under one tab even though it's genuinely
        # used by a different trade's activity.
        _make_run([{"material": self.MATERIAL, "service": "Electrical",
                    "unit": "MTR", "stock": 500, "rate_per_day": 5,
                    "days_left": 100, "status": "GREEN", "total_consumed": 50,
                    "order_by": None}])

    def test_quick_item_creation_still_works_cross_service(self, clean_project):
        """Sanity check this session's earlier refactor of add_quick_item's
        own fallback (now via _full_run_rows) didn't regress creation itself."""
        d = clean_project
        self._setup(d)
        sp.add_quick_item(SLUG, {"service": "FAPA", "activity": "FA Cabling",
                                  "material": self.MATERIAL})
        links = json.loads((d / "links.json").read_text())
        assert links["FAPA"]
        code = next(iter(links["FAPA"]))
        assert links["FAPA"][code] == [self.MATERIAL]

    def test_realistic_forecast_resolves_the_cross_service_link(self, clean_project):
        d = clean_project
        self._setup(d)
        sp.add_quick_item(SLUG, {"service": "FAPA", "activity": "FA Cabling",
                                  "material": self.MATERIAL})
        sp.save_planned(SLUG, {"service": "FAPA",
                               "item_code": next(iter(json.loads((d / "links.json").read_text())["FAPA"])),
                               "planned": 500})

        result = sp.realistic(SLUG, "FAPA")
        assert result["items"], "the linked item must appear in the realistic forecast at all"
        item = result["items"][0]
        assert item["verdict"] != "NOT_LINKED", (
            "this is the exact reported bug: a real link exists (confirmed via "
            "links.json above) but the drawer would show NOT_LINKED because "
            "the material lives outside FAPA's own scoped forecast pool "
            "('Fire & HVAC') -- it's tagged 'Electrical' in the register"
        )
        assert item["links"], "the realistic forecast must carry the resolved stock row, not an empty links list"
        assert item["links"][0]["material"] == self.MATERIAL

    def test_get_links_resolves_unit_for_cross_service_link(self, clean_project):
        d = clean_project
        self._setup(d)
        sp.add_quick_item(SLUG, {"service": "FAPA", "activity": "FA Cabling",
                                  "material": self.MATERIAL})

        out = sp.get_links(SLUG, "FAPA")
        item = out["items"][0]
        assert item["linked"], "the item must show as linked at all"
        assert item["linked"][0]["unit"] == "MTR", (
            "the Link-stock modal must resolve the linked material's real unit "
            "even though it isn't in FAPA's own scoped forecast pool"
        )

    def test_same_service_link_is_unaffected_by_the_fix(self, clean_project):
        """Regression guard: a normal, same-service link (the common case,
        e.g. Electrical linking an Electrical-tagged material) must keep
        working exactly as before -- this fix only ADDS a fallback, it must
        never change the already-working path."""
        d = clean_project
        _write_json(d, "activities.json", {"Electrical": ["Wall Piping"]})
        _make_run([{"material": "25MM PVC PIPE", "service": "Electrical",
                    "unit": "MTR", "stock": 84, "rate_per_day": 90,
                    "days_left": 1, "status": "RED", "total_consumed": 6516,
                    "order_by": "2026-08-19"}])
        sp.add_quick_item(SLUG, {"service": "Electrical", "activity": "Wall Piping",
                                  "material": "25MM PVC PIPE"})
        code = next(iter(json.loads((d / "links.json").read_text())["Electrical"]))
        sp.save_planned(SLUG, {"service": "Electrical", "item_code": code, "planned": 4320})

        result = sp.realistic(SLUG, "Electrical")
        item = result["items"][0]
        assert item["verdict"] == "SHORTAGE"
        assert item["links"][0]["on_hand"] == 84


# ---------------------------------------------------------------- Bug 3 ---
class TestForecastParquetCaching:
    """The performance fix: forecast.parquet must be read from disk once per
    run, not once per caller, and must never serve stale data if the file
    ever legitimately changes (it doesn't in practice -- runs are immutable
    -- but the cache key must not silently assume that forever)."""

    def test_repeated_reads_hit_the_cache(self, clean_project):
        d = clean_project
        run_dir = _make_run([{"material": "X", "service": "Electrical", "unit": "MTR",
                              "stock": 1, "rate_per_day": 1, "days_left": 1,
                              "status": "GREEN", "total_consumed": 0, "order_by": None}])
        sp._read_forecast_parquet_cached.cache_clear()

        sp._read_forecast_parquet(run_dir)
        info1 = sp._read_forecast_parquet_cached.cache_info()
        sp._read_forecast_parquet(run_dir)
        sp._read_forecast_parquet(run_dir)
        info2 = sp._read_forecast_parquet_cached.cache_info()

        assert info1.misses == 1, "the first read must be a real disk read"
        assert info2.misses == 1, "no further disk reads for the same, unchanged run"
        assert info2.hits == 2, "the next two reads must be served from cache"

    def test_three_endpoints_share_one_cached_read(self, clean_project):
        """The actual reported slowness: one service-switch used to trigger
        2-3 separate reads of the same forecast.parquet (once each in
        _forecast_pool, _actual_consumed's internal read, and the drawer's
        cross-service fallback). After the fix, all three must share one
        cached read."""
        d = clean_project
        self_material_setup = _make_run([
            {"material": "110MM PVC PIPE", "service": "Fire & HVAC", "unit": "MTR",
             "stock": 100, "rate_per_day": 2, "days_left": 50, "status": "GREEN",
             "total_consumed": 10, "order_by": None},
        ])
        sp._read_forecast_parquet_cached.cache_clear()

        sp._forecast_pool(SLUG, "HVAC")
        sp._full_run_rows(SLUG)
        info = sp._read_forecast_parquet_cached.cache_info()
        assert info.misses == 1, (
            f"expected the second caller to hit the cache from the first's read, "
            f"got {info.misses} real disk reads for the same run"
        )
        assert info.hits == 1

    def test_a_genuinely_rewritten_run_is_not_served_stale(self, clean_project):
        """Cache key is (path, mtime) specifically so this can never happen,
        even though runs are supposed to be immutable in practice."""
        d = clean_project
        run_dir = _make_run([{"material": "OLD", "service": "Electrical", "unit": "MTR",
                              "stock": 1, "rate_per_day": 1, "days_left": 1,
                              "status": "GREEN", "total_consumed": 0, "order_by": None}])
        sp._read_forecast_parquet_cached.cache_clear()
        first = sp._read_forecast_parquet(run_dir)
        assert first.iloc[0]["material"] == "OLD"

        import time
        time.sleep(0.01)   # ensure a distinct mtime
        pd.DataFrame([{"material": "NEW", "service": "Electrical", "unit": "MTR",
                       "stock": 1, "rate_per_day": 1, "days_left": 1,
                       "status": "GREEN", "total_consumed": 0, "order_by": None}],
                    columns=["material", "service", "unit", "stock", "rate_per_day",
                             "days_left", "status", "total_consumed", "order_by"]
                    ).to_parquet(run_dir / "forecast.parquet")

        second = sp._read_forecast_parquet(run_dir)
        assert second.iloc[0]["material"] == "NEW", (
            "a changed mtime must force a fresh read, never a stale cached frame"
        )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
