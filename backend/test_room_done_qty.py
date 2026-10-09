"""Real per-room done-quantity (partial progress), through the real FastAPI
router against a real project fixture -- not a unit stub.

Reported scenario (Thoth Mall, live): a jet fan item planned at 9 Nos in
Zone 5. Ticking Zone 5 could only ever say "9 done"; the site had fitted 5.
The Rooms/Zones dialog had no way to record 5, and its only quantity box
edits the PLAN, not the done qty. These tests pin the new backend half:
  GET  /item-room-plan   -> each applicable room's own plan + recorded fraction
  POST /progress/rooms   -> record a real quantity per room (stored as a
                            fraction of that room's plan, other rooms untouched)
and the invariant that makes it trustworthy: the dialog's per-room numbers add
up to the item row's own planned/used (itemprog.compute), in every shape.
"""
import json
import pytest
import pandas as pd
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import siteprogress, structure, itemprog

SLUG = "thoth-mall"
SVC = "HVAC"
CODE = "e#16"            # real Thoth item code format (fan, Zone 5)
ITEM_DESC = "2500 CFM Normal Mode/ 4300 CFM Fire Mode For Basement-1 (Zone-5)"


def _client(tmp_path, monkeypatch):
    monkeypatch.setattr(siteprogress, "PROJECTS", tmp_path)
    app = FastAPI()
    app.include_router(siteprogress.router)
    return TestClient(app)


def _write(d, **files):
    for name, obj in files.items():
        (d / f"{name}.json").write_text(json.dumps(obj))


def _boq(d, rows):
    pd.DataFrame(rows).to_parquet(d / "boq.parquet")


def _row(code, qty, unit="Nos", desc=ITEM_DESC, svc=SVC):
    return {"service": svc, "item_code": code, "description": desc, "unit": unit, "qty": qty,
            "section": None, "item_code_raw": code, "subcategory": "Other"}


@pytest.fixture
def thoth(tmp_path, monkeypatch):
    """Thoth-shaped: 3 basements x 7 zones; e#16 = 9 Nos in B1 Zone 5 only
    (one quantity group), planned.json carries the project total (9)."""
    c = _client(tmp_path, monkeypatch)
    d = tmp_path / SLUG
    d.mkdir()
    s = structure.mall("Thoth Mall", levels=["B3", "B2", "B1"], zone_labels=[f"Zone {i}" for i in range(1, 8)])
    (d / "structure.json").write_text(s.to_json())
    ids = {(r["path"][-1], r["name"]): r["id"] for r in s.rooms()}
    z5, z6 = ids[("B1", "Zone 5")], ids[("B1", "Zone 6")]
    _boq(d, [_row(CODE, 9.0), _row("f#15", 6.0, desc="2500 CFM ... Basement-1 (Zone-6)")])
    _write(d,
           item_room_qty={SVC: {CODE: [{"rooms": [z5], "qty": 9.0}], "f#15": [{"rooms": [z6], "qty": 6.0}]}},
           planned={SVC: {CODE: 9.0, "f#15": 6.0}},
           activities={SVC: ["Jet Fan Installation"]},
           mapping={SVC: {"Jet Fan Installation": [CODE, "f#15"]}},
           rates={SVC: {CODE: 3875.0, "f#15": 4650.0}})
    return c, d, z5, z6, ids


def _item(view, code=CODE):
    return next(i for i in view["items"] if i["code"] == code)


def _plan(c, code=CODE, svc=SVC):
    r = c.get(f"/api/siteprogress/{SLUG}/item-room-plan", params={"service": svc, "item_code": code})
    assert r.status_code == 200, r.text
    return r.json()


def _post(c, entries, code=CODE, svc=SVC):
    return c.post(f"/api/siteprogress/{SLUG}/progress/rooms",
                  json={"service": svc, "item_code": code, "entries": entries})


# --------------------------------------------------------------- the reported case
def test_reported_case_zone5_planned_9_site_has_5(thoth):
    c, d, z5, z6, _ = thoth
    p = _plan(c)
    assert p["unit"] == "Nos"
    assert p["rooms"] == {z5: {"planned": 9.0, "frac": 0.0}}      # only Zone 5; no invented rooms

    r = _post(c, [{"room": z5, "qty": 5}])
    assert r.status_code == 200, r.text
    it = _item(r.json())
    assert it["planned"] == 9.0            # the PLAN is untouched
    assert it["used"] == pytest.approx(5.0, abs=1e-3)
    assert it["pct"] == pytest.approx(55.6, abs=0.1)
    prog = json.loads((d / "item_progress.json").read_text())
    assert prog[SVC][CODE][z5] == pytest.approx(5 / 9, abs=1e-6)
    assert json.loads((d / "item_room_qty.json").read_text())[SVC][CODE] == [{"rooms": [z5], "qty": 9.0}]

    # the plan route now reports the recorded fraction, so the dialog can show "5"
    assert _plan(c)["rooms"][z5]["frac"] == pytest.approx(5 / 9, abs=1e-6)


def test_dpr_logs_the_real_quantity_5_not_9(thoth):
    c, d, z5, _, _ = thoth
    _post(c, [{"room": z5, "qty": 5}])
    log = json.loads((d / "dpr_log.json").read_text())
    assert len(log) == 1
    assert log[0]["room"] == "Zone 5" and log[0]["floor"] == "B1"
    assert log[0]["qty"] == pytest.approx(5.0, abs=1e-3) and log[0]["unit"] == "Nos"


def test_full_quantity_is_stored_as_exactly_done(thoth):
    c, d, z5, _, _ = thoth
    r = _post(c, [{"room": z5, "qty": 9}])
    assert json.loads((d / "item_progress.json").read_text())[SVC][CODE][z5] == 1.0
    assert _item(r.json())["pct"] == pytest.approx(100.0)


def test_more_than_planned_is_clamped_to_done_never_over_100(thoth):
    c, d, z5, _, _ = thoth
    r = _post(c, [{"room": z5, "qty": 12}])
    assert r.status_code == 200
    assert json.loads((d / "item_progress.json").read_text())[SVC][CODE][z5] == 1.0
    assert _item(r.json())["used"] == pytest.approx(9.0)


def test_zero_sets_back_to_not_started_and_is_not_logged(thoth):
    c, d, z5, _, _ = thoth
    _post(c, [{"room": z5, "qty": 5}])
    n_before = len(json.loads((d / "dpr_log.json").read_text()))
    r = _post(c, [{"room": z5, "qty": 0}])
    assert json.loads((d / "item_progress.json").read_text())[SVC][CODE][z5] == 0.0
    assert _item(r.json())["used"] == 0
    assert len(json.loads((d / "dpr_log.json").read_text())) == n_before   # undo is not "work done today"


# --------------------------------------------------------------- never wipes other state
def test_other_rooms_and_items_are_never_touched(thoth):
    c, d, z5, z6, _ = thoth
    # Zone 6 / f#15 already marked done through the existing route
    r = c.post(f"/api/siteprogress/{SLUG}/mark-rooms-done",
               json={"service": SVC, "item_code": "f#15", "rooms": [z6], "done": True})
    assert r.status_code == 200
    _post(c, [{"room": z5, "qty": 5}])
    prog = json.loads((d / "item_progress.json").read_text())
    assert prog[SVC]["f#15"][z6] == 1.0
    assert set(prog[SVC][CODE]) == {z5}          # no '*' key, nothing else written for this item


def test_existing_mark_rooms_done_route_still_works_after_a_partial(thoth):
    c, d, z5, _, _ = thoth
    _post(c, [{"room": z5, "qty": 5}])
    r = c.post(f"/api/siteprogress/{SLUG}/mark-rooms-done",
               json={"service": SVC, "item_code": CODE, "rooms": [z5], "done": True})
    assert r.status_code == 200
    assert _item(r.json())["pct"] == pytest.approx(100.0)


# --------------------------------------------------------------- validation: nothing invented, all-or-nothing
def test_unknown_plan_is_refused_not_guessed(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    d = tmp_path / SLUG; d.mkdir()
    s = structure.mall("Thoth Mall", levels=["B1"], zone_labels=["Zone 1", "Zone 2"])
    (d / "structure.json").write_text(s.to_json())
    _boq(d, [_row("q#1", 0.0, desc="quick-added, no qty yet")])      # qty 0 = plan unknown
    _write(d, activities={SVC: ["A"]}, mapping={SVC: {"A": ["q#1"]}})
    rid = s.rooms()[0]["id"]
    assert all(v["planned"] is None for v in _plan(c, "q#1")["rooms"].values())
    r = _post(c, [{"room": rid, "qty": 3}], code="q#1")
    assert r.status_code == 400 and "planned quantity" in r.json()["detail"]
    assert not (d / "item_progress.json").exists()


def test_one_bad_entry_saves_nothing(thoth):
    c, d, z5, _, ids = thoth
    other = ids[("B3", "Zone 1")]                   # not one of this item's rooms
    r = _post(c, [{"room": z5, "qty": 5}, {"room": other, "qty": 1}])
    assert r.status_code == 400
    assert not (d / "item_progress.json").exists()


@pytest.mark.parametrize("bad", [-1, "abc", None])   # NaN cannot be sent as JSON at all
def test_bad_quantity_is_rejected(thoth, bad):
    c, d, z5, _, _ = thoth
    r = _post(c, [{"room": z5, "qty": bad}])
    assert r.status_code == 400
    assert not (d / "item_progress.json").exists()


def test_empty_entries_and_missing_fields(thoth):
    c, _, _, _, _ = thoth
    assert _post(c, []).status_code == 400
    assert c.post(f"/api/siteprogress/{SLUG}/progress/rooms", json={"entries": [{"room": "x", "qty": 1}]}).status_code == 400


# --------------------------------------------------------------- hotel shape: plan 1 per room, MTR items
@pytest.fixture
def hyatt(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    d = tmp_path / SLUG; d.mkdir()
    s = structure.hotel("Hyatt Hotel", floors=["9TH", "10TH"], room_labels=["901", "902", "903"])
    (d / "structure.json").write_text(s.to_json())
    _boq(d, [_row("fan1", 1.0, desc="Exhaust fan"), _row("cab1", 120.0, unit="MTR", desc="Cable per room")])
    _write(d, activities={SVC: ["Fans", "Cable"]}, mapping={SVC: {"Fans": ["fan1"], "Cable": ["cab1"]}})
    return c, d, s


def test_hotel_plan_one_per_room_is_reported_as_one(hyatt):
    c, d, s = hyatt
    p = _plan(c, "fan1")
    assert len(p["rooms"]) == 6 and {v["planned"] for v in p["rooms"].values()} == {1.0}


def test_hotel_mtr_item_partial_per_room(hyatt):
    c, d, s = hyatt
    rid = s.rooms()[0]["id"]
    r = _post(c, [{"room": rid, "qty": 30}], code="cab1")
    assert r.status_code == 200
    assert json.loads((d / "item_progress.json").read_text())[SVC]["cab1"][rid] == pytest.approx(0.25)
    assert _item(r.json(), "cab1")["used"] == pytest.approx(30.0, abs=1e-3)


# --------------------------------------------------------------- the invariant: dialog numbers == row numbers
def _check_sums(c, d, code):
    """sum(plan) == the row's planned; sum(plan x frac) == the row's used."""
    p = _plan(c, code)["rooms"]
    v = c.get(f"/api/siteprogress/{SLUG}/service/{SVC}").json()
    it = _item(v, code)
    assert sum(x["planned"] for x in p.values()) == pytest.approx(it["planned"], rel=1e-6)
    assert sum(x["planned"] * x["frac"] for x in p.values()) == pytest.approx(it["used"], rel=1e-3, abs=1e-3)


def test_invariant_group_item(thoth):
    c, d, z5, _, _ = thoth
    _post(c, [{"room": z5, "qty": 5}])
    _check_sums(c, d, CODE)


def test_invariant_groups_plus_uncovered_explicit_rooms(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    d = tmp_path / SLUG; d.mkdir()
    s = structure.mall("Thoth Mall", levels=["B1"], zone_labels=[f"Zone {i}" for i in range(1, 6)])
    (d / "structure.json").write_text(s.to_json())
    ids = [r["id"] for r in s.rooms()]
    _boq(d, [_row("g#1", 4.0)])
    # explicit applicability: 4 rooms; group re-quantifies two of them (9 and 7); the other two fall back to qty 4
    _write(d, item_rooms={SVC: {"g#1": ids[:4]}},
           item_room_qty={SVC: {"g#1": [{"rooms": [ids[0]], "qty": 9.0}, {"rooms": [ids[1]], "qty": 7.0}]}},
           activities={SVC: ["A"]}, mapping={SVC: {"A": ["g#1"]}})
    p = _plan(c, "g#1")["rooms"]
    assert {k: v["planned"] for k, v in p.items()} == {ids[0]: 9.0, ids[1]: 7.0, ids[2]: 4.0, ids[3]: 4.0}
    _post(c, [{"room": ids[0], "qty": 3}, {"room": ids[2], "qty": 4}, {"room": ids[3], "qty": 1}], code="g#1")
    _check_sums(c, d, "g#1")


def test_invariant_uniform_item_with_planned_override(hyatt):
    c, d, s = hyatt
    rids = [r["id"] for r in s.rooms()]
    _write(d, planned={SVC: {"cab1": 600.0}})             # whole-project override: 600 over 6 rooms = 100 each
    p = _plan(c, "cab1")["rooms"]
    assert {v["planned"] for v in p.values()} == {100.0}
    _post(c, [{"room": rids[0], "qty": 40}, {"room": rids[1], "qty": 100}], code="cab1")
    _check_sums(c, d, "cab1")


def test_invariant_uniform_item_restricted_to_some_rooms(hyatt):
    c, d, s = hyatt
    rids = [r["id"] for r in s.rooms()]
    _write(d, item_rooms={SVC: {"cab1": rids[:3]}})
    p = _plan(c, "cab1")["rooms"]
    assert set(p) == set(rids[:3])                           # rooms it doesn't apply to are not listed
    _post(c, [{"room": rids[2], "qty": 60}], code="cab1")
    _check_sums(c, d, "cab1")


def test_plan_route_item_code_with_slash_and_hash(tmp_path, monkeypatch):
    """Real item codes can hold '/' and '#': the route takes them as query params."""
    c = _client(tmp_path, monkeypatch)
    d = tmp_path / SLUG; d.mkdir()
    s = structure.mall("Thoth Mall", levels=["B1"], zone_labels=["Zone 1"])
    (d / "structure.json").write_text(s.to_json())
    _boq(d, [_row("QI8/13.2.1", 2.0)])
    _write(d, activities={SVC: ["A"]}, mapping={SVC: {"A": ["QI8/13.2.1"]}})
    p = _plan(c, "QI8/13.2.1")
    assert list(p["rooms"].values()) == [{"planned": 2.0, "frac": 0.0}]
