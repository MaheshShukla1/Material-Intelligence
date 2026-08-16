"""Tests for this session's Site Progress changes:

  1. boq.py -- same-service BOQ sheets merge into one entry instead of
     "Plumbing (2)".
  2. siteprogress.py -- /pnl/{service} and every mutation route are
     room-scoped for planned/done/remaining, while waste stays whole-service
     always (no per-room waste estimate -- confirmed with Mahesh).

Run from the repo root (same level as the backend/ package):
    pytest tests/test_room_drilldown_and_boq_merge.py -v

Requires: pytest, fastapi, httpx, pandas, openpyxl, pyarrow.
"""
import json
import shutil

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import boq
from backend import siteprogress as sp
from backend import structure as structure_mod


# --------------------------------------------------------------------- boq
def _write_workbook(path, sheets):
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        for name, rows in sheets.items():
            pd.DataFrame(rows, dtype=object).to_excel(
                xw, sheet_name=name, header=False, index=False)


def test_same_service_sheets_merge_with_code_collision(tmp_path):
    path = tmp_path / "boq.xlsx"
    _write_workbook(path, {
        "PHE - CPVC": [
            ["Item No", "Description", "Unit", "Qty"],
            ["1", "CPVC pipe 20mm", "Mtr", 100],
            ["2", "CPVC elbow", "Nos", 20],
        ],
        "PHE - Fixtures": [
            ["Item No", "Description", "Unit", "Qty"],
            ["1", "PHE trap", "Nos", 15],   # collides with sheet 1's "1"
        ],
    })
    parsed, skipped = boq.parse_workbook(str(path))
    assert list(parsed.keys()) == ["Plumbing"], "must be ONE tab, not 'Plumbing (2)'"
    items = parsed["Plumbing"]["items"]
    assert len(items) == 3
    assert items["item_code"].tolist() == ["1", "2", "1#2"], \
        "colliding code must be suffixed, never silently conflated"
    assert set(items["source_sheet"]) == {"PHE - CPVC", "PHE - Fixtures"}


def test_same_service_sheets_merge_without_collision(tmp_path):
    path = tmp_path / "boq2.xlsx"
    _write_workbook(path, {
        "Electrical": [["Item No", "Description", "Unit", "Qty"],
                       ["1", "Wire 1.5 sqmm", "Mtr", 500]],
        "PHE - CPVC": [["Item No", "Description", "Unit", "Qty"],
                       ["1", "CPVC pipe", "Mtr", 100]],
        "PHE - Traps": [["Item No", "Description", "Unit", "Qty"],
                        ["5", "PHE trap", "Nos", 15]],
        "CHALLAN Log": [["random", "notes"], ["x", "y"]],
    })
    parsed, skipped = boq.parse_workbook(str(path))
    assert sorted(parsed.keys()) == ["Electrical", "Plumbing"]
    assert parsed["Electrical"]["items"]["item_code"].tolist() == ["1"], \
        "single-sheet service must be unaffected (regression)"
    assert parsed["Plumbing"]["items"]["item_code"].tolist() == ["1", "5"], \
        "non-colliding codes across merged sheets must stay untouched"
    assert [s["sheet"] for s in skipped] == ["CHALLAN Log"]


# --------------------------------------------------------------------- pnl
@pytest.fixture
def project(tmp_path, monkeypatch):
    """A tiny 4-room hotel, one Electrical BOQ item (planned 10/room), with
    room[0] at 100% progress and every other room at 0% -- picked so the
    whole-service and single-room numbers are guaranteed to differ."""
    monkeypatch.setattr(sp, "PROJECTS", tmp_path)
    d = tmp_path / "fixture"
    d.mkdir()

    s = structure_mod.hotel("Fixture", floors=["Floor 1", "Floor 2"],
                            room_labels=["Room 101", "Room 102"])
    (d / "structure.json").write_text(s.to_json())
    room_ids = [r["id"] for r in s.rooms()]

    boq_df = pd.DataFrame([{
        "service": "Electrical", "item_code": "2.1", "description": "conduit",
        "unit": "Mtr", "qty": 10.0, "section": "Wiring", "item_code_raw": "2.1",
        "subcategory": "Pipe",
    }])
    boq_df.to_parquet(d / "boq.parquet")
    (d / "activities.json").write_text(json.dumps({"Electrical": ["Wall Piping"]}))
    (d / "mapping.json").write_text(json.dumps({"Electrical": {"Wall Piping": ["2.1"]}}))
    (d / "rates.json").write_text(json.dumps({"Electrical": {"2.1": 50.0}}))
    (d / "item_progress.json").write_text(json.dumps(
        {"Electrical": {"2.1": {room_ids[0]: 1.0, room_ids[1]: 0.0}}}))

    app = FastAPI()
    app.include_router(sp.router)
    client = TestClient(app)
    return client, "fixture", room_ids


def test_pnl_done_remaining_are_room_scoped(project):
    client, slug, room_ids = project
    whole = client.get(f"/api/siteprogress/{slug}/pnl/Electrical").json()
    done_room = client.get(f"/api/siteprogress/{slug}/pnl/Electrical",
                           params={"room": room_ids[0]}).json()   # the 100% room
    zero_room = client.get(f"/api/siteprogress/{slug}/pnl/Electrical",
                           params={"room": room_ids[1]}).json()   # the 0% room

    assert whole["project"]["done_value"] == 500.0
    assert done_room["project"]["done_value"] == 500.0
    assert done_room["project"]["remaining_value"] == 0.0
    assert zero_room["project"]["done_value"] == 0.0, \
        "a 0%-progress room must show ₹0 done, not the whole project's total"
    assert zero_room["project"]["remaining_value"] == 500.0


def test_waste_never_varies_by_room(project, monkeypatch):
    client, slug, room_ids = project
    from backend import siteprogress as sp_mod
    monkeypatch.setattr(sp_mod, "_actual_consumed",
                        lambda slug, service, items: {"2.1": 30.0})

    whole = client.get(f"/api/siteprogress/{slug}/pnl/Electrical").json()
    r0 = client.get(f"/api/siteprogress/{slug}/pnl/Electrical",
                    params={"room": room_ids[0]}).json()
    r1 = client.get(f"/api/siteprogress/{slug}/pnl/Electrical",
                    params={"room": room_ids[1]}).json()

    assert whole["waste"]["wasted_value"] == r0["waste"]["wasted_value"] == \
        r1["waste"]["wasted_value"] == 1000.0, \
        "waste must be identical regardless of which room is selected -- " \
        "the stock register has no room column, so a per-room number would " \
        "be a guess, not a fact (decided with Mahesh)"


def test_mutation_routes_preserve_room_scope(project):
    """Editing anything (mapping, rates, planned, activities...) while a room
    is selected must return a response still scoped to that room -- not
    silently snap back to whole-service numbers."""
    client, slug, room_ids = project
    r = client.post(f"/api/siteprogress/{slug}/mapping",
                    params={"room": room_ids[0]},
                    json={"service": "Electrical", "activity": "Wall Piping",
                          "codes": ["2.1"]})
    assert r.status_code == 200
    assert r.json()["items"][0]["pct"] == 100.0, \
        "response after a mutation must still reflect the selected room"


def test_legacy_tick_route_does_not_crash(project):
    """Regression: the legacy /progress route referenced an undefined `room`
    query-param variable after the room-passthrough refactor; it must use its
    own payload's room instead."""
    client, slug, room_ids = project
    r = client.post(f"/api/siteprogress/{slug}/progress", json={
        "service": "Electrical", "floor": "Floor 1", "room": room_ids[0],
        "activity": "Wall Piping", "frac": 0.5})
    assert r.status_code == 200


# ------------------------------------------------------- received quantity
def test_received_quantity_derived_without_grn(monkeypatch):
    """Mahesh's correction: received quantity does NOT need separate PO/GRN
    files. It's derivable purely from data the forecast run already carries
    (total_consumed + on-hand stock) -- nothing new to upload."""
    from backend import realtime
    item = {"item_code": "QI1", "unit": "Mtr", "planned_total": 52.0,
            "used": 20.0, "remaining": 32.0, "progress_pct": 39.0}
    stock_rows = [{"material": "MS CONDUIT 25MM", "unit": "Mtr", "stock": 9062.0,
                   "rate_per_day": 58.0, "days_left": 155.0, "status": "GREEN",
                   "order_by": None, "total_consumed": 4200.0}]
    res = realtime.combine_item(item, stock_rows)
    link = res["links"][0]
    assert link["received"] == 4200.0 + 9062.0
    assert link["total_consumed"] == 4200.0


def test_received_quantity_never_invented_when_data_missing():
    from backend import realtime
    item = {"item_code": "QI1", "unit": "Mtr", "planned_total": 52.0,
            "used": 20.0, "remaining": 32.0, "progress_pct": 39.0}
    stock_rows = [{"material": "X", "unit": "Mtr", "stock": 100.0,
                   "rate_per_day": 5.0, "days_left": 20.0, "status": "GREEN",
                   "order_by": None}]   # no total_consumed at all
    res = realtime.combine_item(item, stock_rows)
    assert res["links"][0]["received"] is None, \
        "must never guess a received figure when consumed data isn't present"


# ---------------------------------------- quick-item cross-service leak fix
def _fake_run(tmp_path, monkeypatch, slug, rows):
    monkeypatch.setattr(sp, "RUNS", tmp_path / "runs")
    run_dir = sp.RUNS / "run1"
    run_dir.mkdir(parents=True)
    (run_dir / "meta.json").write_text(json.dumps(
        {"project_slug": slug, "created": "2026-08-10T00:00:00"}))
    pd.DataFrame(rows).to_parquet(run_dir / "forecast.parquet")


def test_quick_item_candidates_never_leak_other_services(project, tmp_path, monkeypatch):
    """The old `pool = sub if len(sub) else fdf` fallback silently showed
    EVERY service's materials in e.g. Electrical's '+ Add item' picker the
    moment the forecast run's own service label didn't exactly match. Must
    never happen -- an empty, honestly-reported result beats a wrong one."""
    client, slug, room_ids = project
    _fake_run(tmp_path, monkeypatch, slug, [
        {"material": "PVC PIPE 20MM", "service": "MEP-Plumbing", "stock": 500,
         "rate_per_day": 5, "total_consumed": 200},
        {"material": "CPVC ELBOW", "service": "MEP-Plumbing", "stock": 40,
         "rate_per_day": 1, "total_consumed": 10},
    ])
    r = client.get(f"/api/siteprogress/{slug}/quick-items/Electrical/candidates").json()
    assert r["materials"] == [], "Plumbing materials must not leak into Electrical's picker"
    assert "no materials tagged" in r["reason"]


def test_quick_item_candidates_still_work_when_labels_match(project, tmp_path, monkeypatch):
    client, slug, room_ids = project
    _fake_run(tmp_path, monkeypatch, slug, [
        {"material": "MS CONDUIT 25MM", "service": "Electrical", "stock": 9062,
         "rate_per_day": 58, "total_consumed": 4200},
        {"material": "PVC PIPE 20MM", "service": "Plumbing", "stock": 500,
         "rate_per_day": 5, "total_consumed": 200},
    ])
    r = client.get(f"/api/siteprogress/{slug}/quick-items/Electrical/candidates").json()
    names = [m["name"] for m in r["materials"]]
    assert names == ["MS CONDUIT 25MM"], "regression: correctly-tagged materials must still show"


# --------------------------------------------- explicit cross-service widen
# Found from Mahesh's real Hyatt Hotel register: HVAC's "PVC Fresh Air Piping"
# activity has zero real PVC stock under Fire & HVAC (verified against the
# actual file -- the site's PVC piping is genuinely kept under the
# Electrical tab). The narrow per-service search is CORRECT to show nothing
# here; the fix is an explicit, engineer-initiated widen, never automatic
# (automatic cross-service guessing is exactly the "wrong match" risk fixed
# earlier in _forecast_pool/_actual_consumed/forecast_link).
def test_quick_item_widen_is_off_by_default(project, tmp_path, monkeypatch):
    client, slug, room_ids = project
    _fake_run(tmp_path, monkeypatch, slug, [
        {"material": "MS CONDUIT 25MM", "service": "Electrical",
         "stock": 50, "rate_per_day": 2, "total_consumed": 10},
        {"material": "25MM PVC PIPE", "service": "Plumbing",
         "stock": 2400, "rate_per_day": 10, "total_consumed": 4200},
    ])
    r = client.get(f"/api/siteprogress/{slug}/quick-items/Electrical/candidates").json()
    names = [m["name"] for m in r["materials"]]
    assert "25MM PVC PIPE" not in names, \
        "cross-service material must never appear without the explicit widen"


def test_quick_item_widen_surfaces_and_tags_cross_service_material(project, tmp_path, monkeypatch):
    client, slug, room_ids = project
    _fake_run(tmp_path, monkeypatch, slug, [
        {"material": "MS CONDUIT 25MM", "service": "Electrical",
         "stock": 50, "rate_per_day": 2, "total_consumed": 10},
        {"material": "25MM PVC PIPE", "service": "Plumbing",
         "stock": 2400, "rate_per_day": 10, "total_consumed": 4200},
    ])
    r = client.get(f"/api/siteprogress/{slug}/quick-items/Electrical/candidates",
                   params={"all_services": "true"}).json()
    pvc = next((m for m in r["materials"] if m["name"] == "25MM PVC PIPE"), None)
    assert pvc is not None, "explicit widen must surface the real cross-service material"
    assert pvc["other_service"] == "Plumbing", \
        "must be tagged with its REAL service, never disguised as belonging to Electrical"


def test_quick_add_succeeds_for_a_widen_discovered_material(project, tmp_path, monkeypatch):
    """Regression: the picker could SHOW a cross-service material via widen,
    but clicking + Add used to 404, because add_quick_item only checked the
    current service's own scoped pool."""
    client, slug, room_ids = project
    _fake_run(tmp_path, monkeypatch, slug, [
        {"material": "25MM PVC PIPE", "service": "Fire & HVAC",
         "stock": 2400, "rate_per_day": 10, "total_consumed": 4200, "unit": "Mtr"},
    ])
    # note: item is tagged "Fire & HVAC" here (Electrical's own forecast
    # label), simulating adding it while viewing a DIFFERENT service than
    # the one it's tagged under would require a second, distinctly-labelled
    # service row -- reuse the simpler single-row case to prove the fallback
    # path in add_quick_item resolves it via the full run.
    r = client.post(f"/api/siteprogress/{slug}/quick-item",
                    json={"service": "Electrical", "activity": "Wall Piping",
                          "material": "25MM PVC PIPE"})
    assert r.status_code == 200, r.text


# --------------------------------------------------- mall / hospital shapes
def test_room_drilldown_works_on_a_mall_structure(tmp_path, monkeypatch):
    """The room-drilldown pipeline must not be hotel-specific. A mall's
    Level>Zone tree uses the same leaf 'room' node type internally
    (structure.py's mall() template), so every route above should behave
    identically -- proven end to end here, not just asserted."""
    monkeypatch.setattr(sp, "PROJECTS", tmp_path)
    app = FastAPI()
    app.include_router(sp.router)
    client = TestClient(app)

    r = client.post("/api/siteprogress/thoth-mall/structure/template",
                    json={"kind": "mall", "name": "Thoth Mall",
                          "levels": ["Ground", "Basement 1"], "zones_per_level": 3})
    assert r.status_code == 200 and r.json()["rooms"] == 6

    d = tmp_path / "thoth-mall"
    zones = structure_mod.Structure.from_dict(
        json.loads((d / "structure.json").read_text())).rooms()
    zone_ids = [z["id"] for z in zones]

    pd.DataFrame([{"service": "Electrical", "item_code": "2.1",
                   "description": "LED downlight", "unit": "Nos", "qty": 4.0,
                   "section": "Lighting", "item_code_raw": "2.1",
                   "subcategory": "Fixture"}]).to_parquet(d / "boq.parquet")
    (d / "activities.json").write_text(json.dumps({"Electrical": ["Lighting"]}))
    (d / "mapping.json").write_text(json.dumps({"Electrical": {"Lighting": ["2.1"]}}))
    (d / "rates.json").write_text(json.dumps({"Electrical": {"2.1": 500.0}}))
    (d / "item_progress.json").write_text(json.dumps(
        {"Electrical": {"2.1": {zone_ids[0]: 1.0, zone_ids[1]: 0.0}}}))

    zone_done = client.get(f"/api/siteprogress/thoth-mall/pnl/Electrical",
                           params={"room": zone_ids[0]}).json()
    zone_zero = client.get(f"/api/siteprogress/thoth-mall/pnl/Electrical",
                           params={"room": zone_ids[1]}).json()
    assert zone_done["project"]["remaining_value"] == 0.0
    assert zone_zero["project"]["done_value"] == 0.0


def test_hospital_wing_floor_room_nesting_builds(tmp_path, monkeypatch):
    monkeypatch.setattr(sp, "PROJECTS", tmp_path)
    app = FastAPI()
    app.include_router(sp.router)
    client = TestClient(app)
    r = client.post("/api/siteprogress/city-hospital/structure/template",
                    json={"kind": "hospital", "name": "City Hospital",
                          "wings": ["Wing A", "Wing B"], "floors": ["Floor 1"],
                          "rooms_per_floor": 4})
    assert r.status_code == 200 and r.json()["rooms"] == 8


# ------------------------------------------------------- low-progress waste caveat
# Mahesh's ₹23.99L question: the waste formula (actual_consumed - used) is
# correct, but when almost no progress has been ENTERED yet, waste collapses
# to ~the full actual_consumed figure -- looks inflated, isn't a bug, but
# must be flagged rather than presented as settled fact.
def test_waste_caveat_fires_when_progress_near_zero():
    from backend import pnl as pnl_mod
    df = pd.DataFrame([{
        "item_code": "1", "description": "Wire", "planned_total": 1000, "used": 0,
        "remaining": 1000, "progress_pct": 0.0, "rate": 10, "rated": True,
        "planned_value": 10000, "done_value": 0, "remaining_value": 10000,
        "actual_qty": 500, "waste_qty": 500, "waste_value": 5000,
        "saving_qty": None, "saving_value": None,
    }])
    w = pnl_mod.waste_summary(df)
    assert w["caveat"] is not None
    assert "0.0%" in w["caveat"]


def test_waste_caveat_does_not_fire_with_real_progress():
    from backend import pnl as pnl_mod
    df = pd.DataFrame([{
        "item_code": "1", "description": "Wire", "planned_total": 1000, "used": 600,
        "remaining": 400, "progress_pct": 60.0, "rate": 10, "rated": True,
        "planned_value": 10000, "done_value": 6000, "remaining_value": 4000,
        "actual_qty": 700, "waste_qty": 100, "waste_value": 1000,
        "saving_qty": None, "saving_value": None,
    }])
    w = pnl_mod.waste_summary(df)
    assert w["caveat"] is None


def test_overall_route_carries_waste_caveat(tmp_path, monkeypatch):
    """Same caveat, surfaced at the whole-project /overall level (not just
    per-service pnl), since that's where Mahesh actually saw the number."""
    monkeypatch.setattr(sp, "PROJECTS", tmp_path)
    app = FastAPI()
    app.include_router(sp.router)
    client = TestClient(app)
    slug = "hyatt-zero-progress"
    d = tmp_path / slug
    d.mkdir()
    s = structure_mod.hotel("Hyatt", floors=["Floor 1"], room_labels=["Room 1"])
    (d / "structure.json").write_text(s.to_json())
    pd.DataFrame([{"service": "Electrical", "item_code": "2.1", "description": "conduit",
                   "unit": "Mtr", "qty": 10.0, "section": "Wiring", "item_code_raw": "2.1",
                   "subcategory": "Pipe"}]).to_parquet(d / "boq.parquet")
    (d / "activities.json").write_text(json.dumps({"Electrical": ["Wall Piping"]}))
    (d / "mapping.json").write_text(json.dumps({"Electrical": {"Wall Piping": ["2.1"]}}))
    (d / "rates.json").write_text(json.dumps({"Electrical": {"2.1": 50.0}}))
    # zero progress recorded anywhere -- item_progress.json intentionally absent

    def fake_actual(slug, service, items):
        return {"2.1": 30.0}   # real material was consumed on the real site
    monkeypatch.setattr(sp, "_actual_consumed", fake_actual)

    r = client.get(f"/api/siteprogress/{slug}/overall").json()
    assert r["overall_pct"] == 0.0
    assert r["waste_value"] > 0
    assert r["waste_caveat"] is not None, \
        "0% recorded progress with real waste must carry the caveat at /overall too"


# ------------------------------------------------------- structure rebuild wipes progress
# Mahesh's report: rebuilding the structure (a new shape / different room
# count) kept each item's overall "*" progress value, on the theory it was
# "real recorded work independent of any room id". That's wrong -- "*" was
# still computed against the OLD room count, so it's just as stale as a
# per-room override once the structure changes. Must clear fully, for every
# service at once (one project-wide file), while BOQ/activities/mapping/
# rates/links -- none of which depend on room structure -- survive untouched.
def test_structure_reset_clears_all_progress_but_keeps_everything_else(tmp_path, monkeypatch):
    monkeypatch.setattr(sp, "PROJECTS", tmp_path)
    app = FastAPI()
    app.include_router(sp.router)
    client = TestClient(app)
    slug = "reset-repro"
    d = tmp_path / slug
    d.mkdir()

    s = structure_mod.hotel("Hyatt", floors=["Floor 1"], room_labels=["Room 1", "Room 2"])
    (d / "structure.json").write_text(s.to_json())
    room_ids = [r["id"] for r in s.rooms()]

    pd.DataFrame([{"service": "Electrical", "item_code": "2.1", "description": "conduit",
                   "unit": "Mtr", "qty": 10.0, "section": "Wiring", "item_code_raw": "2.1",
                   "subcategory": "Pipe"}]).to_parquet(d / "boq.parquet")
    (d / "activities.json").write_text(json.dumps({"Electrical": ["Wall Piping"]}))
    (d / "mapping.json").write_text(json.dumps({"Electrical": {"Wall Piping": ["2.1"]}}))
    (d / "rates.json").write_text(json.dumps({"Electrical": {"2.1": 50.0}}))
    (d / "links.json").write_text(json.dumps({"Electrical": {"2.1": ["MS CONDUIT 25MM"]}}))
    # both an overall "*" value AND a per-room override -- both must go
    (d / "item_progress.json").write_text(json.dumps(
        {"Electrical": {"2.1": {"*": 0.5, room_ids[0]: 1.0}}}))
    (d / "item_rooms.json").write_text(json.dumps({"Electrical": {"2.1": room_ids}}))

    r = client.post(f"/api/siteprogress/{slug}/structure/reset")
    assert r.status_code == 200

    assert not (d / "structure.json").exists()
    assert not (d / "item_rooms.json").exists()
    assert not (d / "item_progress.json").exists(), \
        "the overall '*' progress must be cleared too, not just per-room overrides"

    assert (d / "boq.parquet").exists()
    assert json.loads((d / "mapping.json").read_text())["Electrical"]["Wall Piping"] == ["2.1"]
    assert json.loads((d / "rates.json").read_text())["Electrical"]["2.1"] == 50.0
    assert json.loads((d / "links.json").read_text())["Electrical"]["2.1"] == ["MS CONDUIT 25MM"]


def test_structure_reset_clears_every_service_at_once(tmp_path, monkeypatch):
    monkeypatch.setattr(sp, "PROJECTS", tmp_path)
    app = FastAPI()
    app.include_router(sp.router)
    client = TestClient(app)
    slug = "reset-multisvc"
    d = tmp_path / slug
    d.mkdir()
    s = structure_mod.hotel("Hyatt", floors=["Floor 1"], room_labels=["Room 1"])
    (d / "structure.json").write_text(s.to_json())
    (d / "item_progress.json").write_text(json.dumps({
        "Electrical": {"2.1": {"*": 0.5}},
        "Plumbing": {"5.1": {"*": 0.8}},
        "HVAC": {"9.1": {"*": 0.3}},
    }))
    r = client.post(f"/api/siteprogress/{slug}/structure/reset")
    assert r.status_code == 200
    assert not (d / "item_progress.json").exists(), \
        "must clear progress for every service in one pass, not just the first one"


# ------------------------------------------------------- cross-service leak in waste/linking
# Found from Mahesh's screenshots: Electrical/Plumbing/HVAC showed three wildly
# different "Material waste" states in the same project. Root cause: the exact
# same `pool = sub if len(sub) else fdf` anti-pattern already fixed in
# _forecast_pool() (round 2) was still present, unfixed, in TWO more places --
# _actual_consumed() (drives the waste figure) and forecast_link() (drives the
# "Link stock" modal's suggested matches). When a service's own forecast rows
# are empty (e.g. HVAC folds to "Fire & HVAC" for the forecast side, and that
# label happens to be absent from a given run), both silently searched every
# OTHER service's materials instead of honestly reporting "no match" --
# exactly the "no match beats wrong match" rule this codebase already commits
# to elsewhere (linkage.py, boq.py, and _forecast_pool itself).
def _make_run_with_other_services_only(sp_mod, tmp_path, slug):
    sp_mod.RUNS = tmp_path / "runs"
    run_dir = sp_mod.RUNS / "run1"
    run_dir.mkdir(parents=True)
    (run_dir / "meta.json").write_text(json.dumps(
        {"project_slug": slug, "created": "2026-08-10T00:00:00"}))
    pd.DataFrame([
        {"material": "GI DUCT 300X300", "service": "Electrical", "stock": 200,
         "rate_per_day": 2, "total_consumed": 300},
        {"material": "25MM PVC PIPE", "service": "Plumbing", "stock": 500,
         "rate_per_day": 5, "total_consumed": 50},
    ]).to_parquet(run_dir / "forecast.parquet")
    return run_dir


def test_actual_consumed_never_leaks_into_other_services_pool(tmp_path, monkeypatch):
    monkeypatch.setattr(sp, "PROJECTS", tmp_path)
    d = tmp_path / "hvac-leak"
    d.mkdir()
    pd.DataFrame([{"service": "HVAC", "item_code": "9.1",
                   "description": "GI duct 300x300", "unit": "Sqm", "qty": 5.0,
                   "section": "Ducting", "item_code_raw": "9.1",
                   "subcategory": "Duct"}]).to_parquet(d / "boq.parquet")
    _make_run_with_other_services_only(sp, tmp_path, "hvac-leak")

    items = sp._load_boq(d, "HVAC")
    actual = sp._actual_consumed("hvac-leak", "HVAC", items)
    assert actual is None, \
        "HVAC (no 'Fire & HVAC' rows in this run) must not match against " \
        "Electrical/Plumbing materials just because its own label is absent"


def test_forecast_link_never_leaks_into_other_services_pool(tmp_path, monkeypatch):
    monkeypatch.setattr(sp, "PROJECTS", tmp_path)
    app = FastAPI()
    app.include_router(sp.router)
    client = TestClient(app)
    slug = "hvac-link-leak"
    d = tmp_path / slug
    d.mkdir()
    pd.DataFrame([{"service": "HVAC", "item_code": "9.1",
                   "description": "GI duct 300x300", "unit": "Sqm", "qty": 5.0,
                   "section": "Ducting", "item_code_raw": "9.1",
                   "subcategory": "Duct"}]).to_parquet(d / "boq.parquet")
    _make_run_with_other_services_only(sp, tmp_path, slug)

    r = client.get(f"/api/siteprogress/{slug}/forecast-link/HVAC").json()
    assert r["linked"] is True
    assert r["confident"] == 0, \
        "the 'Link stock' modal must not suggest a confident match pulled " \
        "from another service's materials"


# ------------------------------------------- empty-activity seeding bug
# Found from Mahesh's screenshots: every activity showed "no items yet" after
# a BOQ upload, even though activities existed. Root cause: the seed-skip
# check was `m.data.get(svc)` -- truthy the moment the SERVICE KEY exists in
# mapping.json at all, even if every activity under it has zero item codes
# (which is exactly what a freshly-created activity looks like). One empty
# activity permanently blocked auto-seeding for the whole service, forever.
def _upload_boq_xlsx(client, slug, sp_mod, sheets):
    d = sp_mod.PROJECTS / slug
    path = d / "boq_upload.xlsx"
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        for name, rows in sheets.items():
            pd.DataFrame(rows, dtype=object).to_excel(
                xw, sheet_name=name, header=False, index=False)
    with open(path, "rb") as f:
        return client.post(f"/api/siteprogress/{slug}/boq",
                           files={"file": ("boq.xlsx", f,
                                  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})


def test_boq_upload_seeds_activities_that_start_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(sp, "PROJECTS", tmp_path)
    app = FastAPI()
    app.include_router(sp.router)
    client = TestClient(app)
    slug = "hyatt-empty-act"
    d = tmp_path / slug
    d.mkdir()
    s = structure_mod.hotel("Hyatt", floors=["Floor 1"], room_labels=["Room 1", "Room 2"])
    (d / "structure.json").write_text(s.to_json())
    # activities created up front, exactly like the app does -- always start
    # with zero item codes
    (d / "activities.json").write_text(json.dumps({"Electrical": ["Wall Piping", "Ceiling Piping"]}))
    (d / "mapping.json").write_text(json.dumps({"Electrical": {"Wall Piping": [], "Ceiling Piping": []}}))

    r = _upload_boq_xlsx(client, slug, sp, {
        "Electrical": [["Item No", "Description", "Unit", "Qty"],
                       ["1", "Wall piping conduit 25mm", "Mtr", 50],
                       ["2", "Ceiling piping conduit 20mm", "Mtr", 30]],
    })
    assert r.status_code == 200
    mapping_after = json.loads((d / "mapping.json").read_text())
    assert mapping_after["Electrical"]["Wall Piping"], \
        "activity that started empty must get auto-seeded from the BOQ upload"
    assert mapping_after["Electrical"]["Ceiling Piping"], \
        "a SECOND empty activity for the same service must also get seeded " \
        "-- the old bug blocked the whole service after just one empty activity"


def test_boq_upload_never_overwrites_a_real_mapping(tmp_path, monkeypatch):
    """Regression: seeding must stay scoped to genuinely-empty activities --
    an activity the engineer already configured (even with a code the
    suggester wouldn't have picked) must never be silently replaced."""
    monkeypatch.setattr(sp, "PROJECTS", tmp_path)
    app = FastAPI()
    app.include_router(sp.router)
    client = TestClient(app)
    slug = "hyatt-partial"
    d = tmp_path / slug
    d.mkdir()
    s = structure_mod.hotel("Hyatt", floors=["Floor 1"], room_labels=["Room 1"])
    (d / "structure.json").write_text(s.to_json())
    (d / "activities.json").write_text(json.dumps({"Electrical": ["Wall Piping", "Ceiling Piping"]}))
    (d / "mapping.json").write_text(json.dumps(
        {"Electrical": {"Wall Piping": ["MANUAL1"], "Ceiling Piping": []}}))

    _upload_boq_xlsx(client, slug, sp, {
        "Electrical": [["Item No", "Description", "Unit", "Qty"],
                       ["1", "Wall piping conduit 25mm", "Mtr", 50],
                       ["2", "Ceiling piping conduit 20mm", "Mtr", 30]],
    })
    mapping_after = json.loads((d / "mapping.json").read_text())
    assert mapping_after["Electrical"]["Wall Piping"] == ["MANUAL1"], \
        "an already-configured activity must never be overwritten by re-seeding"
    assert mapping_after["Electrical"]["Ceiling Piping"], \
        "the still-empty sibling activity should still get seeded"


# ------------------------------------------- quick-item re-add after removal
def test_quick_item_can_be_readded_after_removal_from_activity(tmp_path, monkeypatch):
    """Found from Mahesh's report: removing a quick-added item from its
    activity (mapping.json) does not delete its quick_items.json record, so
    the backend must still allow re-picking it (reusing the same item_code
    and re-attaching to whichever activity it's added to again). The bug he
    hit was purely client-side (the picker button refused to even try) --
    this proves the server-side contract it relies on is correct."""
    monkeypatch.setattr(sp, "PROJECTS", tmp_path)
    monkeypatch.setattr(sp, "RUNS", tmp_path / "runs")
    app = FastAPI()
    app.include_router(sp.router)
    client = TestClient(app)
    slug = "hyatt-requick"
    d = tmp_path / slug
    d.mkdir()
    s = structure_mod.hotel("Hyatt", floors=["Floor 1"], room_labels=["Room 1"])
    (d / "structure.json").write_text(s.to_json())
    (d / "activities.json").write_text(json.dumps({"Electrical": ["Wall Piping"]}))
    (d / "mapping.json").write_text(json.dumps({"Electrical": {"Wall Piping": []}}))

    run_dir = sp.RUNS / "run1"
    run_dir.mkdir(parents=True)
    (run_dir / "meta.json").write_text(json.dumps(
        {"project_slug": slug, "created": "2026-08-10T00:00:00"}))
    pd.DataFrame([{"material": "25MM PVC PIPE", "service": "Electrical",
                   "stock": 500, "rate_per_day": 5, "total_consumed": 100,
                   "unit": "Mtr"}]).to_parquet(run_dir / "forecast.parquet")

    r1 = client.post(f"/api/siteprogress/{slug}/quick-item",
                     json={"service": "Electrical", "activity": "Wall Piping",
                           "material": "25MM PVC PIPE"})
    assert r1.status_code == 200
    code = r1.json()["items"][0]["code"]

    # remove it from the activity (the row's x button)
    r2 = client.post(f"/api/siteprogress/{slug}/mapping",
                     json={"service": "Electrical", "activity": "Wall Piping", "codes": []})
    item_after_remove = next(it for it in r2.json()["items"] if it["code"] == code)
    assert item_after_remove["mapped"] is False, \
        "item must show as unmapped after being stripped from every activity"

    # candidates list must still flag it "already" (quick_items.json record persists)
    cands = client.get(f"/api/siteprogress/{slug}/quick-items/Electrical/candidates").json()
    assert next(m for m in cands["materials"] if m["name"] == "25MM PVC PIPE")["already"] is True

    # but re-picking it must succeed and re-attach the SAME item_code
    r3 = client.post(f"/api/siteprogress/{slug}/quick-item",
                     json={"service": "Electrical", "activity": "Wall Piping",
                           "material": "25MM PVC PIPE"})
    assert r3.status_code == 200
    assert any(it["code"] == code for it in r3.json()["items"]), \
        "re-picking a removed quick-item must succeed and reuse its item_code"
