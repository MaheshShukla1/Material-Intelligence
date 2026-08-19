"""Real-logic pytest suite for the broader Site Progress performance pass
(see HANDOFF_CONTEXT.md's "Current task"). Calls the actual siteprogress.py
route/helper functions directly against a throwaway project directory, same
pattern as test_bugfixes.py -- not mocked.

Three things every change here needs proof of, not assumption:
  1. Caching is actually effective (repeat calls hit the cache, don't re-do
     the expensive work).
  2. Caching never serves STALE data -- a real change on disk (new BOQ, new
     quick item, new forecast run) must be reflected on the very next call,
     with zero manual cache-bust anywhere.
  3. The /overall refactor (sharing one items/used pair between the service
     view and its waste calc) produces EXACTLY the same numbers as the old,
     fully independent computation path -- this is a pure performance
     refactor, not a behaviour change, so it must be bit-identical.
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


def _make_run(materials, run_id="r1", created="2026-08-18T10:00:00"):
    run_dir = sp.RUNS / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "meta.json").write_text(json.dumps(
        {"project_slug": SLUG, "created": created}))
    cols = ["material", "service", "unit", "stock", "rate_per_day",
            "days_left", "status", "total_consumed", "order_by"]
    rows = [{c: m.get(c) for c in cols} for m in materials]
    pd.DataFrame(rows, columns=cols).to_parquet(run_dir / "forecast.parquet")
    return run_dir


def _boq_df(rows):
    """rows: [{"service","item_code","description","unit","qty"}]."""
    recs = [{**r, "item_code_raw": r["item_code"], "section": "Main",
            "subcategory": "Other"} for r in rows]
    return pd.DataFrame(recs, columns=["service", "item_code", "item_code_raw",
                                       "description", "unit", "qty",
                                       "section", "subcategory"])


def _setup_electrical(d, n_items=8, n_rooms=6):
    """A small but non-trivial Electrical service: BOQ items, structure,
    activities, mapping, progress, rates, a linked forecast run -- enough
    for /overall's waste calc to have something real to compute."""
    from backend import structure as struct_mod
    s = struct_mod.hotel("Test Hotel", floors=["F1"],
                         room_labels=[f"Room {i}" for i in range(1, n_rooms + 1)])
    (d / "structure.json").write_text(s.to_json())
    room_ids = [r["id"] for r in s.rooms()]

    rows = [{"service": "Electrical", "item_code": f"E.{i}",
             "description": f"25MM PVC PIPE line {i}", "unit": "MTR",
             "qty": 10.0 + i} for i in range(1, n_items + 1)]
    _boq_df(rows).to_parquet(d / "boq.parquet")

    _write_json(d, "activities.json", {"Electrical": ["Wall Piping", "Wire Pulling"]})
    mapping = {"Electrical": {"Wall Piping": [f"E.{i}" for i in range(1, n_items // 2 + 1)],
                              "Wire Pulling": [f"E.{i}" for i in range(n_items // 2 + 1, n_items + 1)]}}
    _write_json(d, "mapping.json", mapping)
    _write_json(d, "rates.json", {"Electrical": {f"E.{i}": 100.0 for i in range(1, n_items + 1)}})

    prog = {f"E.{i}": {"*": round(0.1 * i, 2)} for i in range(1, n_items + 1)}
    _write_json(d, "item_progress.json", {"Electrical": prog})

    _make_run([{"material": "25MM PVC PIPE", "service": "Electrical", "unit": "MTR",
               "stock": 500, "rate_per_day": 5, "days_left": 40, "status": "GREEN",
               "total_consumed": 300, "order_by": None}])
    links = {"Electrical": {f"E.{i}": [{"material": "25MM PVC PIPE", "factor": None}]
                            for i in range(1, n_items + 1)}}
    _write_json(d, "links.json", links)
    return room_ids


# ------------------------------------------------------- value equivalence
class TestOverallRefactorMatchesOldSemantics:
    """The core safety property of the /overall refactor: sharing one
    items/used pair between _service_view_core and _waste_for must give
    EXACTLY the numbers the old, fully-independent double-computation gave.
    """

    def test_waste_for_precomputed_matches_fresh(self, clean_project):
        d = clean_project
        _setup_electrical(d)
        items = sp._load_boq(d, "Electrical")
        prog = sp._item_prog(d).get("Electrical", {})
        rooms_cfg = sp._item_rooms(d).get("Electrical", {})
        room_qty_groups = sp._item_room_qty(d).get("Electrical", {})
        all_rooms = sp._all_room_ids(d)
        used = sp.itemprog.compute(items, prog, rooms_cfg, all_rooms,
                                   sp._planned(d, "Electrical"),
                                   room_qty_groups=room_qty_groups)

        fresh = sp._waste_for(d, SLUG, "Electrical")            # old-style: loads everything itself
        precomputed = sp._waste_for(d, SLUG, "Electrical", items=items, used=used)  # new-style
        assert fresh == precomputed, (
            "passing a precomputed items/used pair must never change the "
            "waste numbers -- it's a performance-only change"
        )

    def test_overall_totals_match_independent_per_service_calls(self, clean_project):
        d = clean_project
        _setup_electrical(d)
        result = sp.overall(SLUG)

        # independently recompute Electrical's numbers the OLD way (fully
        # separate _service_view() + _waste_for() calls, no sharing at all)
        # and confirm /overall's own per-service figures agree exactly.
        view = sp._service_view(d, "Electrical")
        waste = sp._waste_for(d, SLUG, "Electrical")

        per = result["by_service"]["Electrical"]
        assert per["pct"] == view["overall_pct"]
        assert per["done_value"] == round(view["pnl_totals"]["done_value"] or 0.0, 2)
        assert per["remaining_value"] == round(view["pnl_totals"]["remaining_value"] or 0.0, 2)
        assert per["waste_value"] == round(waste["wasted"], 2)
        assert per["waste_recorded_pct"] == waste.get("recorded_pct")

    def test_overall_project_totals_are_internally_consistent(self, clean_project):
        d = clean_project
        _setup_electrical(d)
        result = sp.overall(SLUG)
        per = result["by_service"]["Electrical"]
        # project-level totals are a straight sum over services (only one
        # service here, so they must match that service's own figures)
        assert result["done_value"] == per["done_value"]
        assert result["remaining_value"] == per["remaining_value"]
        assert result["waste_value"] == per["waste_value"]


# --------------------------------------------------------- cache staleness
class TestCacheNeverServesStaleData:
    """Every new mtime-keyed cache in this pass must reflect a real on-disk
    change on the VERY NEXT call -- no manual bust required anywhere, and
    none must ever be added just to make one of these pass."""

    def test_forecast_pool_reflects_a_brand_new_run(self, clean_project):
        d = clean_project
        _make_run([{"material": "OLD MATERIAL", "service": "Electrical", "unit": "MTR",
                   "stock": 1, "rate_per_day": 1, "days_left": 1, "status": "GREEN",
                   "total_consumed": 0, "order_by": None}], run_id="r1",
                  created="2026-08-18T09:00:00")
        rows, names, run_name, _f = sp._forecast_pool(SLUG, "Electrical")
        assert names == ["OLD MATERIAL"]

        import time
        time.sleep(0.01)
        _make_run([{"material": "NEW MATERIAL", "service": "Electrical", "unit": "MTR",
                   "stock": 1, "rate_per_day": 1, "days_left": 1, "status": "GREEN",
                   "total_consumed": 0, "order_by": None}], run_id="r2",
                  created="2026-08-18T11:00:00")   # newer -> becomes the latest run

        rows2, names2, run_name2, _f2 = sp._forecast_pool(SLUG, "Electrical")
        assert names2 == ["NEW MATERIAL"], (
            "a genuinely new forecast run must never be served the previous "
            "run's cached rows/names"
        )
        assert run_name2 != run_name

    def test_full_run_rows_reflects_a_brand_new_run(self, clean_project):
        d = clean_project
        _make_run([{"material": "OLD MATERIAL", "service": "Electrical", "unit": "MTR",
                   "stock": 1, "rate_per_day": 1, "days_left": 1, "status": "GREEN",
                   "total_consumed": 0, "order_by": None}], run_id="r1",
                  created="2026-08-18T09:00:00")
        rows = sp._full_run_rows(SLUG)
        assert "OLD MATERIAL" in rows

        import time
        time.sleep(0.01)
        _make_run([{"material": "NEW MATERIAL", "service": "Electrical", "unit": "MTR",
                   "stock": 1, "rate_per_day": 1, "days_left": 1, "status": "GREEN",
                   "total_consumed": 0, "order_by": None}], run_id="r2",
                  created="2026-08-18T11:00:00")
        rows2 = sp._full_run_rows(SLUG)
        assert "NEW MATERIAL" in rows2
        assert "OLD MATERIAL" not in rows2

    def test_linkage_match_reflects_a_boq_reupload(self, clean_project):
        d = clean_project
        _make_run([
            {"material": "25MM PVC PIPE", "service": "Electrical", "unit": "MTR",
             "stock": 100, "rate_per_day": 2, "days_left": 50, "status": "GREEN",
             "total_consumed": 10, "order_by": None},
            {"material": "3X1.5 SQMM FRZH WIRE", "service": "Electrical", "unit": "MTR",
             "stock": 200, "rate_per_day": 3, "days_left": 60, "status": "GREEN",
             "total_consumed": 20, "order_by": None},
        ])
        _boq_df([{"service": "Electrical", "item_code": "E.1",
                 "description": "25MM PVC PIPE conduit run", "unit": "MTR",
                 "qty": 10.0}]).to_parquet(d / "boq.parquet")

        out1 = sp.get_links(SLUG, "Electrical")
        sugg1 = out1["items"][0]["suggestion"]
        assert sugg1["best"] == "25MM PVC PIPE"

        import time
        time.sleep(0.01)
        # BOQ re-uploaded with a genuinely different description for the
        # SAME item code -- the wire, not the pipe
        _boq_df([{"service": "Electrical", "item_code": "E.1",
                 "description": "3X1.5 SQMM FRZH WIRE run", "unit": "MTR",
                 "qty": 10.0}]).to_parquet(d / "boq.parquet")

        out2 = sp.get_links(SLUG, "Electrical")
        sugg2 = out2["items"][0]["suggestion"]
        assert sugg2["best"] == "3X1.5 SQMM FRZH WIRE", (
            "linkage.match() caching must never suggest a stale match from "
            "before the BOQ was re-uploaded"
        )

    def test_linkage_match_reflects_a_new_quick_item(self, clean_project):
        d = clean_project
        _write_json(d, "activities.json", {"Electrical": ["Wall Piping"]})
        _make_run([{"material": "25MM PVC PIPE", "service": "Electrical", "unit": "MTR",
                   "stock": 100, "rate_per_day": 2, "days_left": 50, "status": "GREEN",
                   "total_consumed": 10, "order_by": None}])
        _boq_df([{"service": "Electrical", "item_code": "E.1",
                 "description": "irrelevant line", "unit": "MTR",
                 "qty": 10.0}]).to_parquet(d / "boq.parquet")

        # warm the match cache before the quick item exists
        sp.get_links(SLUG, "Electrical")

        sp.add_quick_item(SLUG, {"service": "Electrical", "activity": "Wall Piping",
                                 "material": "25MM PVC PIPE"})

        actual = sp._actual_consumed(SLUG, "Electrical", sp._load_boq(d, "Electrical"))
        # the quick item (QI1) is an exact link to a real, consumed material
        # -- it must show up in actual-consumed, not be invisible because
        # the cached match predates the quick item's own creation.
        assert actual is not None and "QI1" in actual, (
            f"quick item missing from _actual_consumed after cache warm-up: {actual}"
        )

    def test_forecast_link_reflects_a_boq_reupload(self, clean_project):
        d = clean_project
        _make_run([{"material": "25MM PVC PIPE", "service": "Electrical", "unit": "MTR",
                   "stock": 100, "rate_per_day": 2, "days_left": 50, "status": "GREEN",
                   "total_consumed": 10, "order_by": None},
                  {"material": "3X1.5 SQMM FRZH WIRE", "service": "Electrical", "unit": "MTR",
                   "stock": 200, "rate_per_day": 3, "days_left": 60, "status": "GREEN",
                   "total_consumed": 20, "order_by": None}])
        _boq_df([{"service": "Electrical", "item_code": "E.1",
                 "description": "25MM PVC PIPE conduit run", "unit": "MTR",
                 "qty": 10.0}]).to_parquet(d / "boq.parquet")
        out1 = sp.forecast_link(SLUG, "Electrical")
        assert out1["links"]["E.1"]["best"] == "25MM PVC PIPE"

        import time
        time.sleep(0.01)
        _boq_df([{"service": "Electrical", "item_code": "E.1",
                 "description": "3X1.5 SQMM FRZH WIRE run", "unit": "MTR",
                 "qty": 10.0}]).to_parquet(d / "boq.parquet")
        out2 = sp.forecast_link(SLUG, "Electrical")
        assert out2["links"]["E.1"]["best"] == "3X1.5 SQMM FRZH WIRE"


class TestAttachForecastRewriteMatchesOldImplementation:
    """linkage.attach_forecast() was rewritten from a pandas groupby()+iloc[0]
    pass to a plain itertuples() dict build (~75x faster on a 160-material
    pool -- it was ~95% of a single warm forecast_link() call). This locks
    in byte-identical output against the original implementation forever,
    on real multi-row, multi-column, real-dtype data -- not just "looks
    similar"."""

    @staticmethod
    def _old_attach_forecast(link, forecast_df):
        """The exact original implementation, kept here ONLY as a reference
        oracle for this test -- never called from application code."""
        if forecast_df is None or len(forecast_df) == 0:
            return {}
        fdf = forecast_df.copy()
        fdf["_key"] = fdf["material"].astype(str).map(sp.linkage._norm)
        keep = [c for c in sp.linkage._FORECAST_COLS if c in fdf.columns]
        lut = {k: g.iloc[0][keep].to_dict() for k, g in fdf.groupby("_key")}
        out = {}
        for code, info in link.items():
            if info.get("best"):
                row = lut.get(sp.linkage._norm(info["best"]))
                if row is not None:
                    out[code] = row
        return out

    def test_byte_identical_on_hyatt_scale_pool(self, clean_project):
        d = clean_project
        _setup_electrical(d, n_items=12)
        # widen the run to several distinct materials, including duplicate
        # material names (first-occurrence-wins is the exact behaviour being
        # protected here) and a genuinely missing optional column, so the
        # oracle comparison exercises every branch attach_forecast can hit.
        rows = [{"material": "25MM PVC PIPE", "service": "Electrical", "unit": "MTR",
                "stock": 500, "rate_per_day": 5, "days_left": 40, "status": "GREEN",
                "total_consumed": 300, "order_by": None}]
        for i in range(2, 30):
            rows.append({"material": f"MATERIAL {i}", "service": "Electrical", "unit": "MTR",
                        "stock": float(i), "rate_per_day": float(i) / 2,
                        "days_left": float(i), "status": "AMBER",
                        "total_consumed": float(i) * 3, "order_by": None})
        # a genuine duplicate material name -- first occurrence must win
        rows.append({"material": "25MM PVC PIPE", "service": "Electrical", "unit": "MTR",
                    "stock": 999, "rate_per_day": 999, "days_left": 999,
                    "status": "RED", "total_consumed": 999, "order_by": "2099-01-01"})
        _make_run(rows)

        items = sp._load_boq(d, "Electrical")
        run = sp._latest_run_for(SLUG)
        fdf = sp._read_forecast_parquet(run)
        pool = fdf[fdf.service == "Electrical"]
        link = sp.linkage.match(items, pool.material.astype(str).tolist())

        old = self._old_attach_forecast(link, pool)
        new = sp.linkage.attach_forecast(link, pool)
        assert set(old.keys()) == set(new.keys())
        for code in old:
            assert sp._scrub(old[code]) == sp._scrub(new[code]), (
                f"attach_forecast rewrite diverged from the original for {code}"
            )
        # the duplicate-material case specifically: first occurrence's real
        # values (stock=500), never the later duplicate's (stock=999)
        pipe_code = next(c for c, i in link.items() if i.get("best") == "25MM PVC PIPE")
        assert new[pipe_code]["stock"] == 500, (
            "first-occurrence-wins must survive the rewrite exactly"
        )

    def test_forecast_link_route_end_to_end_uses_the_rewrite(self, clean_project):
        d = clean_project
        _setup_electrical(d)
        out = sp.forecast_link(SLUG, "Electrical")
        assert out["linked"] is True
        assert out["confident"] >= 1
        code = next(iter(out["links"]))
        assert out["links"][code]["forecast"] is not None


# ------------------------------------------------------- cache effectiveness
class TestCachesAreActuallyEffective:
    """Not just present -- actually hit on repeat calls, so the perf claim is
    real and not just untested plumbing."""

    def test_forecast_pool_cached_hits_on_repeat_calls(self, clean_project):
        d = clean_project
        _setup_electrical(d)
        sp._forecast_pool_cached.cache_clear()
        sp._forecast_pool(SLUG, "Electrical")
        info1 = sp._forecast_pool_cached.cache_info()
        sp._forecast_pool(SLUG, "Electrical")
        sp._forecast_pool(SLUG, "Electrical")
        info2 = sp._forecast_pool_cached.cache_info()
        assert info1.misses == 1
        assert info2.misses == 1
        assert info2.hits == 2

    def test_linkage_match_cached_hits_on_repeat_calls(self, clean_project):
        d = clean_project
        _setup_electrical(d)
        sp._linkage_match_cached.cache_clear()
        sp.get_links(SLUG, "Electrical")
        info1 = sp._linkage_match_cached.cache_info()
        sp.get_links(SLUG, "Electrical")
        sp.forecast_link(SLUG, "Electrical")
        info2 = sp._linkage_match_cached.cache_info()
        assert info1.misses == 1, "first call must do the real match"
        assert info2.misses == 1, (
            "get_links() and forecast_link() call the SAME service's match -- "
            "the second and third calls must both hit the cache, not re-match"
        )
        assert info2.hits == 2

    def test_overall_only_matches_once_per_service_not_twice(self, clean_project):
        """The actual bug this pass fixes: /overall used to trigger
        linkage.match() once inside _service_view's... actually inside
        _waste_for's _actual_consumed -- called once per service already,
        but confirms no NEW duplication crept in via the refactor (e.g. the
        service view and the waste calc each separately triggering a match)."""
        d = clean_project
        _setup_electrical(d)
        sp._linkage_match_cached.cache_clear()
        sp.overall(SLUG)
        info = sp._linkage_match_cached.cache_info()
        assert info.misses == 1, (
            f"expected exactly one real linkage.match() for the one service "
            f"in this project, got {info.misses}"
        )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
