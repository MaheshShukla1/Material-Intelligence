"""Runs (From / To / drum readings) for length items -- through the real FastAPI router against a real project fixture.

Reported need (site engineer, Hyatt): for Mtr / RMT items the client signs a measurement sheet that says WHERE the cable was laid
(From, To, drum reading start / end, total), not only how much. Rules under test:
  * a run ADDS to the zone's recorded quantity, through the same checks as a typed zone quantity (plan is a limit);
  * the quantity of a run is |start - end| of the drum, or a typed quantity when there is no drum mark;
  * a run is never deleted: cancel keeps it on record with a reason, restore brings it back;
  * runs are only for length units, never for Nos / Sq.Mtr / Kg;
  * a failed run saves nothing (no quantity, no run on record);
  * quantity recorded before runs (or by a tick) stays and is reported as "other", never lost.
Harness: a mall-shaped project (levels x zones), one FA-cable item in Mtr planned at 600 Mtr over 6 zones (100 each)."""
import json
import pytest
import pandas as pd
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import siteprogress, structure, itemprog, runlog

SLUG = "hyatt-hotel"
SVC = "ELV"
CODE = "G.1"
DESC = "2C x 1.5 sq mm FA cable"


def _row(code, qty, unit, desc=DESC):
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
    s = structure.mall("Hyatt", levels=["L3", "L4"], zone_labels=["Z1", "Z2", "Z3"])
    (d / "structure.json").write_text(s.to_json())
    ids = [r["id"] for r in s.rooms()]
    pd.DataFrame([_row(CODE, 600.0, "Mtr"), _row("N.1", 6.0, "Nos", "Smoke detector"), _row("A.1", 60.0, "Sq.Mtr", "Insulation")]).to_parquet(d / "boq.parquet")
    for name, obj in {"planned": {SVC: {CODE: 600.0, "N.1": 6.0, "A.1": 60.0}}, "activities": {SVC: ["Cabling"]},
                      "mapping": {SVC: {"Cabling": [CODE, "N.1", "A.1"]}}, "rates": {SVC: {CODE: 40.0, "N.1": 900.0, "A.1": 120.0}}}.items():
        (d / f"{name}.json").write_text(json.dumps(obj))
    return c, d, ids


P = f"/api/siteprogress/{SLUG}"


def plan(c, code=CODE):
    r = c.get(f"{P}/item-room-plan", params={"service": SVC, "item_code": code})
    assert r.status_code == 200, r.text
    return r.json()


def done(c, rid, code=CODE):
    p = plan(c, code)["rooms"][rid]
    return round(p["planned"] * p["frac"], 3)


def run(c, rid, **kw):
    body = {"service": SVC, "item_code": CODE, "room": rid, "from": "FACP-3", "to": "Corridor Loop A"}
    body.update(kw)
    return c.post(f"{P}/progress/runs", json=body)


def runs(c, code=CODE):
    r = c.get(f"{P}/progress/runs", params={"service": SVC, "item_code": code})
    assert r.status_code == 200, r.text
    return r.json()


# ------------------------------------------------------------------ pure rules
def test_run_units_are_lengths_only():
    for u in ("Mtr", "MTR", "Mtrs", "RMT", "Rmt", "Rmtr", "RM", "m", "Metre", "Meters", "Mtr."):
        assert runlog.is_run_unit(u), u
    for u in ("Nos", "No", "Sq.Mtr", "Sq mtr", "Kg", "Set", "LS", "", None, "Cum", "Rolls"):
        assert not runlog.is_run_unit(u), u


def test_quantity_is_the_drum_difference_either_way_round():
    assert runlog.clean_run({"from": "a", "to": "b", "start": 1000, "end": 858})["qty"] == 142
    assert runlog.clean_run({"from": "a", "to": "b", "start": 858, "end": 1000})["qty"] == 142        # counting up
    assert runlog.clean_run({"from": "a", "to": "b", "start": "1000.5", "end": "858"})["qty"] == 142.5
    assert runlog.clean_run({"from": "a", "to": "b", "qty": "67"})["qty"] == 67                       # no drum mark: typed
    c = runlog.clean_run({"from": " a ", "to": "b", "start": 10, "end": 4, "qty": 999})
    assert c["qty"] == 6 and c["from"] == "a"                                                         # readings win over a typed number


@pytest.mark.parametrize("body,msg", [
    ({"from": "", "to": "b", "qty": 5}, "From and To"),
    ({"from": "a", "to": "", "qty": 5}, "From and To"),
    ({"from": "a", "to": "b"}, "readings or the length"),
    ({"from": "a", "to": "b", "start": 10}, "both drum readings"),
    ({"from": "a", "to": "b", "start": 10, "end": 10}, "no length"),
    ({"from": "a", "to": "b", "qty": 0}, "no length"),
    ({"from": "a", "to": "b", "qty": -4}, "no length"),
    ({"from": "a", "to": "b", "qty": "abc"}, "numbers"),
    ({"from": "a", "to": "b", "start": -1, "end": 5}, "negative"),
    ({"from": "x" * 81, "to": "b", "qty": 5}, "80"),
])
def test_bad_runs_are_refused_with_a_plain_message(body, msg):
    with pytest.raises(ValueError) as e:
        runlog.clean_run(body)
    assert msg in str(e.value)


# ------------------------------------------------------------------ through the router
def test_reported_case_runs_add_up_to_the_zone_quantity(site):
    """FA cable laid zone by zone with the drum readings of the client's sheet: 1000->858, 858->762, 762->674 = 142 + 96 + 88."""
    c, d, ids = site
    z = ids[0]
    assert plan(c)["rooms"][z]["planned"] == 100.0 and done(c, z) == 0
    r1 = run(c, z, start=1000, end=858, to="Loop A")
    assert r1.status_code == 400 and "plan" in r1.text.lower()           # 142 does not fit a 100 m zone: refused, as a typed number is
    assert done(c, z) == 0 and runs(c)["rooms"] == {}                    # and nothing was saved
    plan_free = c.post(f"{P}/planned", json={"service": SVC, "item_code": CODE, "qty": 1800.0})
    assert plan_free.status_code == 200, plan_free.text
    assert plan(c)["rooms"][z]["planned"] >= 326                          # the plan is raised: now the three runs fit
    for st, en, to in ((1000, 858, "Loop A"), (858, 762, "Rooms 301-306"), (762, 674, "Rooms 307-312")):
        r = run(c, z, start=st, end=en, to=to)
        assert r.status_code == 200, r.text
    assert abs(done(c, z) - 326) < 0.01
    got = runs(c)["rooms"][z]
    assert [x["qty"] for x in got["runs"]] == [142, 96, 88]
    assert got["active"] == 326 and abs(got["done"] - 326) < 0.01 and got["other"] == 0
    assert all(x["from"] == "FACP-3" for x in got["runs"]) and got["runs"][0]["start"] == 1000 and got["runs"][0]["end"] == 858
    assert got["runs"][1]["to"] == "Rooms 301-306"


def _roomy(c, z, total=1800.0):
    r = c.post(f"{P}/planned", json={"service": SVC, "item_code": CODE, "qty": total})
    assert r.status_code == 200, r.text


def test_a_run_adds_to_the_zone_and_the_item_progress(site):
    c, d, ids = site
    z = ids[0]
    _roomy(c, z)                                                          # 300 m per zone
    r = run(c, z, qty=50)
    assert r.status_code == 200, r.text
    assert abs(done(c, z) - 50) < 0.01
    item = next(i for i in r.json()["svc"]["items"] if i["code"] == CODE)
    assert item["used"] > 0                                                # the item row sees the run, like any zone quantity
    run(c, z, qty=25.5)
    assert abs(done(c, z) - 75.5) < 0.01
    assert abs(runs(c)["rooms"][z]["active"] - 75.5) < 1e-9


def test_over_the_zone_plan_is_refused_and_nothing_is_saved(site):
    c, d, ids = site
    z = ids[0]
    run(c, z, qty=90)
    before = (done(c, z), runs(c))
    r = run(c, z, qty=20)                                                 # 110 > 100
    assert r.status_code == 400
    assert (done(c, z), runs(c)) == before


def test_runs_are_only_for_length_items(site):
    c, d, ids = site
    for code in ("N.1", "A.1"):
        r = c.post(f"{P}/progress/runs", json={"service": SVC, "item_code": code, "room": ids[0], "from": "a", "to": "b", "qty": 1})
        assert r.status_code == 400 and "length items" in r.text, code
    assert c.get(f"{P}/progress/runs", params={"service": SVC, "item_code": "N.1"}).json()["runs_ok"] is False
    assert runs(c)["runs_ok"] is True


def test_unknown_item_room_or_bad_input(site):
    c, d, ids = site
    assert c.post(f"{P}/progress/runs", json={"service": SVC, "item_code": "ZZ", "room": ids[0], "from": "a", "to": "b", "qty": 1}).status_code == 404
    assert c.get(f"{P}/progress/runs", params={"service": SVC, "item_code": "ZZ"}).status_code == 404
    assert run(c, "no-such-room", qty=1).status_code == 400
    assert run(c, ids[0], qty=0).status_code == 400
    assert run(c, ids[0], start=5).status_code == 400
    assert c.post(f"{P}/progress/runs", json={"item_code": CODE}).status_code == 400
    assert runs(c)["rooms"] == {}


def test_quantity_recorded_before_runs_stays_and_is_reported_as_other(site):
    c, d, ids = site
    z = ids[0]
    r = c.post(f"{P}/progress/rooms", json={"service": SVC, "item_code": CODE, "entries": [{"room": z, "qty": 30}]})
    assert r.status_code == 200
    run(c, z, qty=20)
    assert abs(done(c, z) - 50) < 0.01
    v = runs(c)["rooms"][z]
    assert v["active"] == 20 and abs(v["other"] - 30) < 0.01


def test_a_plain_tick_after_runs_is_not_lost_and_leaves_no_room_for_more(site):
    c, d, ids = site
    z = ids[0]
    run(c, z, qty=40)
    r = c.post(f"{P}/mark-rooms-done", json={"service": SVC, "item_code": CODE, "rooms": [z], "done": True})
    assert r.status_code == 200
    assert abs(done(c, z) - 100) < 0.01
    v = runs(c)["rooms"][z]
    assert v["active"] == 40 and abs(v["other"] - 60) < 0.01              # the tick's 60 m has no run behind it, and says so
    assert run(c, z, qty=1).status_code == 400                            # the zone is full


def test_cancel_keeps_the_run_on_record_and_takes_its_quantity_off(site):
    c, d, ids = site
    z = ids[0]
    run(c, z, qty=40, to="A")
    run(c, z, qty=30, to="B")
    rid = runs(c)["rooms"][z]["runs"][0]["id"]
    r = c.post(f"{P}/progress/runs/cancel", json={"service": SVC, "item_code": CODE, "room": z, "run_id": rid, "reason": "wrong room"})
    assert r.status_code == 200, r.text
    assert abs(done(c, z) - 30) < 0.01
    v = runs(c)["rooms"][z]
    assert len(v["runs"]) == 2 and v["runs"][0]["cancelled"] is True and v["runs"][0]["reason"] == "wrong room"
    assert v["active"] == 30 and v["other"] == 0
    assert c.post(f"{P}/progress/runs/cancel", json={"service": SVC, "item_code": CODE, "room": z, "run_id": rid}).status_code == 409
    back = c.post(f"{P}/progress/runs/cancel", json={"service": SVC, "item_code": CODE, "room": z, "run_id": rid, "cancelled": False})
    assert back.status_code == 200, back.text
    assert abs(done(c, z) - 70) < 0.01
    v = runs(c)["rooms"][z]
    assert v["runs"][0]["cancelled"] is False and v["runs"][0]["reason"] == "" and v["active"] == 70


def test_restore_that_no_longer_fits_is_refused(site):
    c, d, ids = site
    z = ids[0]
    run(c, z, qty=60)
    rid = runs(c)["rooms"][z]["runs"][0]["id"]
    c.post(f"{P}/progress/runs/cancel", json={"service": SVC, "item_code": CODE, "room": z, "run_id": rid})
    run(c, z, qty=70)                                                      # the zone has 70 of 100 now
    r = c.post(f"{P}/progress/runs/cancel", json={"service": SVC, "item_code": CODE, "room": z, "run_id": rid, "cancelled": False})
    assert r.status_code == 400
    assert abs(done(c, z) - 70) < 0.01 and runs(c)["rooms"][z]["runs"][0]["cancelled"] is True


def test_cancel_unknown_run(site):
    c, d, ids = site
    run(c, ids[0], qty=10)
    assert c.post(f"{P}/progress/runs/cancel", json={"service": SVC, "item_code": CODE, "room": ids[0], "run_id": "nope"}).status_code == 404
    assert c.post(f"{P}/progress/runs/cancel", json={"service": SVC, "item_code": CODE}).status_code == 400


def test_each_zone_keeps_its_own_runs(site):
    c, d, ids = site
    run(c, ids[0], qty=10, to="A")
    run(c, ids[1], qty=20, to="B")
    run(c, ids[1], qty=5, to="C")
    v = runs(c)["rooms"]
    assert set(v) == {ids[0], ids[1]}
    assert v[ids[0]]["active"] == 10 and v[ids[1]]["active"] == 25
    assert abs(done(c, ids[2])) < 1e-9


def test_a_run_reaches_the_dpr_like_any_zone_update(site):
    c, d, ids = site
    run(c, ids[0], qty=40)
    log = json.loads((d / "dpr_log.json").read_text())
    assert len(log) == 1
    assert log[0]["item_code"] == CODE and log[0]["qty"] == 40.0 and log[0]["unit"] == "Mtr" and log[0]["qty_before"] == 0.0
    assert log[0]["floor"] == "L3" and log[0]["room"] == "Z1"
    run(c, ids[0], qty=10)
    log = json.loads((d / "dpr_log.json").read_text())
    assert len(log) == 1 and log[0]["qty"] == 50.0 and log[0]["qty_before"] == 0.0     # same day, same zone: one DPR line, like a typed number


# ------------------------------------------------------------------ the sheet lines (pure)
INFO = {"name": {"r1": "Zone 1", "r2": "Zone 2"}, "loc": {"r1": "L3", "r2": "L3"}, "order": {"r1": 0, "r2": 1}}


def R(q, to="x", **kw):
    d = {"id": to, "from": "f", "to": to, "start": None, "end": None, "qty": q, "remarks": "", "cancelled": False}
    d.update(kw)
    return d


def test_sheet_lines_with_nothing_billed_before():
    ls = runlog.sheet_lines({"r1": [R(142, "A"), R(96, "B")]}, {"r1": 238.0}, None, "none", INFO)
    assert [(l["to"], l["qty"], l["prev"], l["this"]) for l in ls] == [("A", 142, 0, 142), ("B", 96, 0, 96)]
    assert ls[0]["floor"] == "L3" and ls[0]["zone"] == "Zone 1" and all(l["kind"] == "run" for l in ls)


def test_sheet_lines_split_previous_and_this_bill_in_the_order_entered_and_straddle_the_cut():
    runs = {"r1": [R(142, "A"), R(96, "B"), R(88, "C")]}
    ls = runlog.sheet_lines(runs, {"r1": 326.0}, {"r1": 200.0}, "loc", INFO)           # 200 m were billed before: A(142) + 58 of B
    assert [(l["to"], l["prev"], l["this"]) for l in ls] == [("A", 142, 0), ("B", 58, 38), ("C", 0, 88)]
    assert sum(l["prev"] for l in ls) == 200 and sum(l["this"] for l in ls) == 126 and sum(l["qty"] for l in ls) == 326


def test_quantity_recorded_before_runs_is_its_own_first_line_and_counts_as_billed_first():
    ls = runlog.sheet_lines({"r1": [R(20, "A")]}, {"r1": 50.0}, {"r1": 30.0}, "loc", INFO)
    assert [(l["kind"], l["qty"], l["prev"], l["this"]) for l in ls] == [("before", 30, 30, 0), ("run", 20, 0, 20)]
    assert ls[0]["remarks"] == runlog.BEFORE


def test_cancelled_runs_never_print():
    ls = runlog.sheet_lines({"r1": [R(40, "A", cancelled=True), R(30, "B")]}, {"r1": 30.0}, None, "none", INFO)
    assert [l["to"] for l in ls] == ["B"]


def test_zone_changed_by_hand_below_its_runs_is_one_honest_total():
    ls = runlog.sheet_lines({"r1": [R(40, "A"), R(30, "B")]}, {"r1": 50.0}, {"r1": 10.0}, "loc", INFO)      # 50 < 70 of runs
    assert len(ls) == 1 and ls[0]["kind"] == "changed" and ls[0]["qty"] == 50 and ls[0]["prev"] == 10 and ls[0]["this"] == 40


def test_quantity_billed_earlier_that_the_records_no_longer_hold_is_shown_and_ties():
    ls = runlog.sheet_lines({"r1": [R(20, "A")]}, {"r1": 20.0}, {"r1": 35.0}, "loc", INFO)
    assert sum(l["prev"] for l in ls) == 35 and sum(l["this"] for l in ls) == -15 and sum(l["qty"] for l in ls) == 20
    assert ls[-1]["kind"] == "reduced"


def test_legacy_billing_leaves_previous_and_this_blank():
    ls = runlog.sheet_lines({"r1": [R(20, "A")]}, {"r1": 20.0}, None, "legacy", INFO)
    assert ls[0]["prev"] is None and ls[0]["this"] is None and ls[0]["qty"] == 20


def test_zones_in_structure_order_and_only_zones_recorded_as_rooms():
    runs = {"r2": [R(5, "B")], "r1": [R(7, "A")], "r9": [R(1, "gone")]}
    ls = runlog.sheet_lines(runs, {"r1": 7.0, "r2": 5.0}, None, "none", INFO)                   # r9 is no longer recorded: not printed
    assert [l["to"] for l in ls] == ["A", "B"]
