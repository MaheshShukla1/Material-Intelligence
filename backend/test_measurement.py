"""Measurement entries (From / To / drum readings) in their THREE places -- zone, whole floor / level, whole project --
through the real FastAPI router against a real project fixture.

Reported need (site engineers, Hyatt / Thoth): a length of cable is not always inside one room. "Transformer -> MDB, 142 m" is a feeder of the
whole project; "Level 3 cable tray, 420 m" is one quantity for a whole level; "FACP-3 -> Corridor Loop A" sits in one zone. The first version
only took entries per zone, so a feeder had no honest place to go ("Raise the plan first").
Rules under test:
  * an entry ADDS to the quantity of its place through the Site Progress path that already owns that number
    (zone: /progress/rooms, level: /progress/area-total, project: /progress/item-qty) -- so % , DPR and bill see it;
  * the three places never overlap on one item, so "quantity = entries" stays true: a zone is closed while the item has whole-project entries
    or sits inside a whole-level total, a level is closed while one of its zones was recorded zone-wise, the project is closed while any zone /
    level of the item holds its own quantity -- each with a plain reason, and a refused entry saves nothing;
  * cancelling keeps the entry on record; it is refused (nothing changes) when the place has since been closed, because writing the quantity
    back would reset what the engineer recorded there;
  * the item's chip numbers (entries, backed, installed) come with the service view, only for items that have entries.
Harness: a mall-shaped project (levels L3, L4 x zones Z1..Z3), one FA-cable item in Mtr planned at 600 Mtr (100 per zone, 300 per level)."""
import json
import pytest
import pandas as pd
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import siteprogress, structure, runlog

SLUG = "thoth-mall"
SVC = "ELV"
CODE = "G.1"


def _row(code, qty, unit, desc):
    return {"service": SVC, "item_code": code, "description": desc, "unit": unit, "qty": qty,
            "section": None, "item_code_raw": code, "subcategory": "Other"}


@pytest.fixture
def site(tmp_path, monkeypatch):
    monkeypatch.setattr(siteprogress, "PROJECTS", tmp_path)
    app = FastAPI()
    app.include_router(siteprogress.router)
    c = TestClient(app)
    d = tmp_path / SLUG
    d.mkdir()
    s = structure.mall("Thoth", levels=["L3", "L4"], zone_labels=["Z1", "Z2", "Z3"])
    (d / "structure.json").write_text(s.to_json())
    ids = [r["id"] for r in s.rooms()]
    pd.DataFrame([_row(CODE, 600.0, "Mtr", "11 kV cable"), _row("N.1", 6.0, "Nos", "Detector")]).to_parquet(d / "boq.parquet")
    for name, obj in {"planned": {SVC: {CODE: 600.0, "N.1": 6.0}}, "activities": {SVC: ["Cabling"]},
                      "mapping": {SVC: {"Cabling": [CODE, "N.1"]}}, "rates": {SVC: {CODE: 40.0, "N.1": 900.0}}}.items():
        (d / f"{name}.json").write_text(json.dumps(obj))
    return c, d, ids


P = f"/api/siteprogress/{SLUG}"


def add(c, scope="project", **kw):
    body = {"service": SVC, "item_code": CODE, "from": "Transformer", "to": "MDB"}
    if scope:
        body["scope"] = scope
    body.update(kw)
    return c.post(f"{P}/progress/runs", json=body)


def cancel(c, scope, run_id, **kw):
    body = {"service": SVC, "item_code": CODE, "scope": scope, "run_id": run_id, "reason": "wrong drum"}
    body.update(kw)
    return c.post(f"{P}/progress/runs/cancel", json=body)


def table(c, code=CODE):
    r = c.get(f"{P}/progress/runs", params={"service": SVC, "item_code": code})
    assert r.status_code == 200, r.text
    return r.json()


def sc(c, key):
    return next(t for t in table(c)["scopes"] if t["key"] == key)


def item_row(c, code=CODE):
    r = c.get(f"{P}/service/{SVC}")
    assert r.status_code == 200, r.text
    return next(i for i in r.json()["items"] if i["code"] == code)


def zone_done(c, rid):
    r = c.get(f"{P}/item-room-plan", params={"service": SVC, "item_code": CODE}).json()["rooms"][rid]
    return round(r["planned"] * r["frac"], 3)


LVL = lambda a: runlog.area_key(a)


# ------------------------------------------------------------------ the places
def test_places_are_listed_project_first_then_levels_then_zones(site):
    c, d, ids = site
    t = table(c)
    keys = [x["key"] for x in t["scopes"]]
    assert keys[0] == runlog.PROJECT and keys[1:3] == [LVL("L3"), LVL("L4")] and keys[3:] == ids
    assert [x["kind"] for x in t["scopes"]] == ["project", "area", "area"] + ["zone"] * 6
    assert t["scopes"][0]["label"].startswith("Whole project") and t["scopes"][1]["label"] == "L3 · whole level"
    assert [x["plan"] for x in t["scopes"][:3]] == [600, 300, 300] and all(x["allowed"] for x in t["scopes"])
    assert t["summary"] == {"n": 0, "backed": 0, "done": 0} and t["runs_ok"] is True and t["unit"] == "Mtr"


def test_a_feeder_goes_to_the_whole_project_and_is_counted(site):
    """The reported gap: 'Transformer -> MDB, 1000 -> 858 = 142 m' belongs to no room."""
    c, d, ids = site
    r = add(c, "project", start=1000, end=858)
    assert r.status_code == 200, r.text
    row = next(i for i in r.json()["svc"]["items"] if i["code"] == CODE)
    assert abs(row["used"] - 142) < 0.01 and abs(row["pct"] - 142 / 6) < 0.05
    p = sc(c, runlog.PROJECT)
    assert [x["qty"] for x in p["runs"]] == [142] and p["active"] == 142 and abs(p["done"] - 142) < 0.01 and p["other"] == 0
    assert table(c)["summary"] == {"n": 1, "backed": 142, "done": 142}
    r2 = add(c, "project", qty=58.5, to="DB-2")
    assert r2.status_code == 200, r2.text
    assert abs(item_row(c)["used"] - 200.5) < 0.01 and table(c)["summary"]["backed"] == 200.5
    node = json.loads((d / "item_progress.json").read_text())[SVC][CODE]
    assert set(node) == {"*"}                                   # recorded overall, no zone got a number it was never given


def test_a_whole_level_is_one_quantity_and_stays_a_valid_area_total(site):
    c, d, ids = site
    r = add(c, "area", area="L3", start=0, end=150, **{"from": "Riser", "to": "L3 end"})
    assert r.status_code == 200, r.text
    assert abs(sc(c, LVL("L3"))["done"] - 150) < 0.01
    assert abs(zone_done(c, ids[0]) - 50) < 0.01 and abs(zone_done(c, ids[2]) - 50) < 0.01 and zone_done(c, ids[3]) == 0   # spread, other level untouched
    pools = json.loads((d / "item_pools.json").read_text())[SVC][CODE]
    assert pools == [{"area": "L3", "rooms": ids[:3], "frac": 0.5}]
    assert add(c, "area", area="L3", qty=100).status_code == 200
    assert abs(sc(c, LVL("L3"))["done"] - 250) < 0.01 and sc(c, LVL("L3"))["active"] == 250
    assert json.loads((d / "item_pools.json").read_text())[SVC][CODE][0]["area"] == "L3"
    over = add(c, "area", area="L3", qty=60)                    # 250 + 60 > the level's plan 300
    assert over.status_code == 400 and "Raise the plan first" in over.text
    assert abs(sc(c, LVL("L3"))["done"] - 250) < 0.01           # nothing saved


def test_a_zone_is_unchanged_from_the_first_version(site):
    c, d, ids = site
    assert add(c, "zone", room=ids[4], qty=40).status_code == 200
    assert abs(zone_done(c, ids[4]) - 40) < 0.01 and sc(c, ids[4])["active"] == 40
    assert add(c, None, room=ids[4], qty=10).status_code == 200       # no "scope" at all = a zone, as the first version posted it
    assert abs(zone_done(c, ids[4]) - 50) < 0.01


def test_the_three_places_add_up_in_the_summary(site):
    c, d, ids = site
    add(c, "zone", room=ids[3], qty=40)
    add(c, "area", area="L3", qty=90)
    s = table(c)["summary"]
    assert s["n"] == 2 and abs(s["backed"] - 130) < 0.01 and abs(s["done"] - 130) < 0.01
    assert add(c, "project", qty=5).status_code == 400          # the project is closed once zones / levels hold their own quantity


# ------------------------------------------------------------------ places that close, and why
def test_zone_and_level_are_closed_while_the_item_has_whole_project_entries(site):
    c, d, ids = site
    assert add(c, "project", qty=100).status_code == 200
    for body in ({"scope": "zone", "room": ids[0]}, {"scope": "area", "area": "L4"}):
        r = add(c, qty=10, **body)
        assert r.status_code == 400 and "whole project" in r.text, r.text
    assert sc(c, ids[0])["allowed"] is False and "whole project" in sc(c, ids[0])["why"]
    assert sc(c, LVL("L4"))["allowed"] is False
    assert abs(item_row(c)["used"] - 100) < 0.01                # nothing was saved


def test_project_is_closed_once_a_zone_or_level_holds_its_own_quantity(site):
    c, d, ids = site
    assert add(c, "zone", room=ids[0], qty=20).status_code == 200
    r = add(c, "project", qty=10)
    assert r.status_code == 400 and "already have their own quantity" in r.text
    assert sc(c, runlog.PROJECT)["allowed"] is False
    assert abs(zone_done(c, ids[0]) - 20) < 0.01


def test_a_level_is_closed_when_one_of_its_zones_was_recorded_zone_by_zone(site):
    c, d, ids = site
    assert add(c, "zone", room=ids[1], qty=30).status_code == 200
    r = add(c, "area", area="L3", qty=50)
    assert r.status_code == 400 and "zone by zone" in r.text and "L3" in r.text
    assert add(c, "area", area="L4", qty=50).status_code == 200                       # the other level is not affected
    assert abs(zone_done(c, ids[1]) - 30) < 0.01                                       # and the zone's own number was not spread over


def test_a_zone_inside_a_whole_level_total_is_closed(site):
    c, d, ids = site
    assert add(c, "area", area="L3", qty=90).status_code == 200
    r = add(c, "zone", room=ids[0], qty=5)
    assert r.status_code == 400 and "one total" in r.text
    assert abs(sc(c, LVL("L3"))["done"] - 90) < 0.01
    assert add(c, "zone", room=ids[3], qty=5).status_code == 200                       # a zone of the other level is open


def test_a_plain_tick_before_any_entry_is_kept_and_reported_as_other(site):
    c, d, ids = site
    r = c.post(f"{P}/mark-rooms-done", json={"service": SVC, "item_code": CODE, "rooms": [ids[0]], "done": True})
    assert r.status_code == 200, r.text
    assert abs(zone_done(c, ids[0]) - 100) < 0.01
    assert add(c, "project", qty=1).status_code == 400                                 # a zone holds a number now
    assert add(c, "zone", room=ids[1], qty=40).status_code == 200
    assert sc(c, ids[0])["other"] == 100 and sc(c, ids[0])["active"] == 0


def test_unknown_place_scope_or_item_and_non_length_items(site):
    c, d, ids = site
    assert add(c, "area", area="Roof").status_code == 400
    assert add(c, "zone", room="nope", qty=1).status_code == 400
    assert add(c, "sideways", qty=1).status_code == 400
    r = c.post(f"{P}/progress/runs", json={"service": SVC, "item_code": "N.1", "scope": "project", "from": "a", "to": "b", "qty": 1})
    assert r.status_code == 400 and "length" in r.text
    assert c.post(f"{P}/progress/runs", json={"service": SVC, "item_code": "ZZ", "scope": "project", "from": "a", "to": "b", "qty": 1}).status_code == 404
    assert add(c, "project", **{"from": ""}, qty=5).status_code == 400
    assert add(c, "project").status_code == 400                                         # neither readings nor length


def test_a_refused_entry_saves_nothing(site):
    c, d, ids = site
    r = add(c, "project", qty=601)                                                      # more than the item's plan
    assert r.status_code == 400 and "Raise the plan first" in r.text
    assert not (d / "item_runs.json").exists() or json.loads((d / "item_runs.json").read_text()) == {}
    assert item_row(c)["used"] == 0 and "meas" not in item_row(c)


# ------------------------------------------------------------------ cancel / restore
def test_cancel_and_restore_a_whole_project_entry(site):
    c, d, ids = site
    a = add(c, "project", qty=142).json()["zone"]["runs"][0]["id"]
    add(c, "project", qty=58, to="DB-2")
    r = cancel(c, "project", a)
    assert r.status_code == 200, r.text
    assert abs(item_row(c)["used"] - 58) < 0.01
    p = sc(c, runlog.PROJECT)
    assert p["runs"][0]["cancelled"] is True and p["runs"][0]["reason"] == "wrong drum" and p["active"] == 58          # stays on record
    assert table(c)["summary"] == {"n": 1, "backed": 58, "done": 58}
    assert cancel(c, "project", a).status_code == 409
    assert cancel(c, "project", a, cancelled=False).status_code == 200
    assert abs(item_row(c)["used"] - 200) < 0.01 and sc(c, runlog.PROJECT)["runs"][0]["cancelled"] is False


def test_cancel_and_restore_a_whole_level_entry(site):
    c, d, ids = site
    a = add(c, "area", area="L4", qty=120).json()["zone"]["runs"][0]["id"]
    assert abs(zone_done(c, ids[3]) - 40) < 0.01
    assert cancel(c, "area", a, area="L4").status_code == 200
    assert zone_done(c, ids[3]) == 0 and sc(c, LVL("L4"))["runs"][0]["cancelled"] is True
    assert cancel(c, "area", a, area="L4", cancelled=False).status_code == 200
    assert abs(sc(c, LVL("L4"))["done"] - 120) < 0.01


def test_cancel_is_refused_when_it_would_reset_what_was_recorded_since(site):
    """A feeder was measured for the whole project; later a zone of the item was ticked. Cancelling the feeder would write the overall
    quantity back and wipe that zone's own number, so it is refused, with the reason, and nothing changes."""
    c, d, ids = site
    a = add(c, "project", qty=100).json()["zone"]["runs"][0]["id"]
    r = c.post(f"{P}/progress/rooms", json={"service": SVC, "item_code": CODE, "entries": [{"room": ids[0], "qty": 30}]})
    assert r.status_code == 200, r.text
    before = json.loads((d / "item_progress.json").read_text())
    r = cancel(c, "project", a)
    assert r.status_code == 400 and "reset" in r.text
    assert json.loads((d / "item_progress.json").read_text()) == before
    assert sc(c, runlog.PROJECT)["runs"][0]["cancelled"] is False
    # and the sheet will not claim the feeder as backed beyond what is held: 100 recorded overall minus nothing, but the zone changed things
    assert sc(c, runlog.PROJECT)["writable"] is False


def test_restore_that_no_longer_fits_is_refused(site):
    c, d, ids = site
    a = add(c, "project", qty=400).json()["zone"]["runs"][0]["id"]
    assert cancel(c, "project", a).status_code == 200
    assert add(c, "project", qty=300, to="Z").status_code == 200
    r = cancel(c, "project", a, cancelled=False)                                        # 300 + 400 > 600
    assert r.status_code == 400 and "does not fit" in r.text
    assert abs(item_row(c)["used"] - 300) < 0.01 and sc(c, runlog.PROJECT)["runs"][0]["cancelled"] is True


# ------------------------------------------------------------------ the chip, the DPR, the plan removed
def test_the_item_row_carries_the_chip_numbers_only_when_there_are_entries(site):
    c, d, ids = site
    assert "meas" not in item_row(c) and "meas" not in item_row(c, "N.1")
    add(c, "project", qty=150)
    assert item_row(c)["meas"] == {"n": 1, "backed": 150, "done": 150}
    c.post(f"{P}/progress/item-qty", json={"service": SVC, "item_code": CODE, "qty": 400})          # the engineer overwrites the total by hand
    m = item_row(c)["meas"]
    assert m["n"] == 1 and abs(m["done"] - 400) < 0.01 and m["backed"] == 150                      # 150 explained, 250 not: the chip can say so


def test_a_whole_project_entry_reaches_the_dpr(site):
    c, d, ids = site
    add(c, "project", qty=60)
    log = json.loads((d / "dpr_log.json").read_text())
    assert log and all(x["item_code"] == CODE for x in log) and round(sum(x["qty"] for x in log), 2) == 60.0


def test_a_whole_level_entry_reaches_the_dpr(site):
    c, d, ids = site
    assert add(c, "area", area="L3", qty=90).status_code == 200
    log = json.loads((d / "dpr_log.json").read_text())
    assert log and all(x["item_code"] == CODE for x in log) and round(sum(x["qty"] for x in log), 2) == 90.0


def test_plan_removed_item_takes_a_feeder_longer_than_an_equal_share(site):
    c, d, ids = site
    assert c.post(f"{P}/item-plan-off", json={"service": SVC, "item_code": CODE, "off": True}).status_code == 200
    r = add(c, "project", qty=450)                                                       # far above 100 m per zone; below the item total 600
    assert r.status_code == 200, r.text
    assert abs(item_row(c)["used"] - 450) < 0.01
    assert add(c, "project", qty=200, to="Z").status_code == 400                         # 650 > 600: the total is the limit


# ------------------------------------------------------------------ pure: the sheet lines of the three places
INFO = {"name": {"r1": "Zone 1", "r2": "Zone 2"}, "loc": {"r1": "L3", "r2": "L3"}, "order": {"r1": 0, "r2": 1}, "word": "level"}


def R(q, to="x", **kw):
    d = {"id": to, "from": "f", "to": to, "start": None, "end": None, "qty": q, "remarks": "", "cancelled": False}
    d.update(kw)
    return d


def test_key_quantities_names_the_three_places():
    split = {"pools": {"L3": {"q": 250.0, "o": 0}}, "rooms": {"r9": 40.0}, "rest": 60.0, "sum": 350.0}
    q, o = runlog.key_quantities(split)
    assert q == {"~area:L3": 250.0, "r9": 40.0, "~project": 60.0} and o == {"~area:L3": 0}
    assert runlog.key_quantities({"pools": {}, "rooms": {}, "rest": 0.0, "sum": 0.0})[0] == {}


def test_sheet_lines_of_a_level_and_the_project_split_previous_and_this_bill():
    runs = {runlog.PROJECT: [R(142, "MDB"), R(58, "DB-2")], "~area:L3": [R(100, "L3 end"), R(150, "L3 tray")], "r1": [R(20, "ZZ")]}
    now = {runlog.PROJECT: 200.0, "~area:L3": 250.0, "r1": 20.0}
    prev = {runlog.PROJECT: 150.0, "~area:L3": 100.0, "r1": 0.0}
    out = runlog.sheet_lines(runs, now, prev, "loc", INFO, {"~area:L3": 5})
    assert [(l["scope"], l["qty"], l["prev"], l["this"]) for l in out] == [
        ("zone", 20, 0, 20),                                              # zones first, in structure order
        ("area", 100, 100, 0), ("area", 150, 0, 150),                    # then whole levels
        ("project", 142, 142, 0), ("project", 58, 8, 50)]                # the project last; the 150 billed before: 142 + 8 of the 58
    assert out[1]["floor"] == "L3" and "Whole level" in out[1]["zone"] and out[3]["floor"] == "" and "Whole project" in out[3]["zone"]
    for key, tot in (("~project", 200), ("~area:L3", 250), ("r1", 20)):
        assert round(sum(l["qty"] for l in out if l["key"] == key), 4) == tot                 # every place ties to its quantity


def test_sheet_lines_show_the_rest_of_a_place_recorded_without_entries():
    out = runlog.sheet_lines({runlog.PROJECT: [R(40)]}, {runlog.PROJECT: 100.0}, None, "none", INFO)
    assert [(l["kind"], l["qty"]) for l in out] == [("before", 60), ("run", 40)] and out[0]["remarks"] == runlog.BEFORE
    assert [(l["prev"], l["this"]) for l in out] == [(0, 60), (0, 40)]
