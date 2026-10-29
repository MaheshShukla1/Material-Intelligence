"""Measurement grid, read-only data (GET /{slug}/measure-grid/{service}) -- through the real FastAPI router, real project fixture.

Reported scenario (Thoth Mall, Fire > Piping Work, item 39): the grid showed nothing ("-" everywhere) although quantities were recorded, and
its 'BOQ qty' column was really the edited planned quantity. Rules under test:
  * the grid reads Site Progress's own numbers: a zone's cell = what is recorded there; the row total = the item's `used` in the service view;
  * BOQ qty is the BOQ's own quantity (ProjectBase 'Design Quantity'), shown separately from the Site plan (planned, edited with the pencil);
  * plan above BOQ is flagged, pending = BOQ - done and never below 0;
  * a service whose qty column is NOT a whole-project total gets boq_qty / pending = null, never a guess;
  * zones an item does not apply to have no cell;
  * how much of the installed quantity is backed by measurement entries, and which entries are the latest.
Harness: a mall (B3 / B2 / B1 x 7 zones = 21 zones), Fire BOQ in the real Thoth shape: item 39a-39f in Rmt, BOQ qty vs edited plan as in the live app."""
import json
import pytest
import pandas as pd
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import siteprogress, structure

SLUG = "thoth-mall"
SVC = "Fire"
P = f"/api/siteprogress/{SLUG}"

# code: (description, BOQ qty = Design Quantity, planned = what the site plan was edited to)
PIPES = {
    "39a": ("a) 150 mm dia", 4200.0, 5120.0),
    "39b": ("b) 100 mm dia", 1947.5, 1259.5),
    "39c": ("c) 80 mm dia", 1599.0, 1096.2),
    "39d": ("d) 65 mm dia", 2168.33, 1553.0),
    "39e": ("e) 50 mm dia", 3706.67, 2543.0),
    "39f": ("f) 40 mm dia", 3534.0, 1683.0),
}


def _row(svc, code, desc, unit, qty):
    return {"service": svc, "item_code": code, "description": desc, "unit": unit, "qty": qty,
            "section": None, "item_code_raw": code, "subcategory": "Other"}


@pytest.fixture
def site(tmp_path, monkeypatch):
    monkeypatch.setattr(siteprogress, "PROJECTS", tmp_path)
    app = FastAPI()
    app.include_router(siteprogress.router)
    c = TestClient(app)
    d = tmp_path / SLUG
    d.mkdir()
    s = structure.mall("Thoth Mall", levels=["B3", "B2", "B1"], zone_labels=[f"Zone {i}" for i in range(1, 8)])
    (d / "structure.json").write_text(s.to_json())
    ids = [r["id"] for r in s.rooms()]
    assert len(ids) == 21
    rows = [_row(SVC, k, v[0], "Rmt", v[1]) for k, v in PIPES.items()]
    rows += [_row(SVC, "N.1", "Smoke detector", "Nos", 210.0)]
    # a second service whose qty column is PER ROOM (raw BOQ): no planned figures were ever written for it
    rows += [_row("HVAC", f"H.{i}", f"Duct {i}", "Sqm", 10.0) for i in range(1, 7)]
    pd.DataFrame(rows).to_parquet(d / "boq.parquet")
    planned = {k: v[2] for k, v in PIPES.items()}
    planned["N.1"] = 210.0                                   # ProjectBase upload writes a planned figure for every line
    (d / "planned.json").write_text(json.dumps({SVC: planned}))
    (d / "activities.json").write_text(json.dumps({SVC: ["Piping"], "HVAC": ["Ducting"]}))
    (d / "mapping.json").write_text(json.dumps({SVC: {"Piping": list(PIPES) + ["N.1"]}, "HVAC": {"Ducting": [f"H.{i}" for i in range(1, 7)]}}))
    return c, d, ids


def grid(c, svc=SVC):
    r = c.get(f"{P}/measure-grid/{svc}")
    assert r.status_code == 200, r.text
    return r.json()


def row(g, code):
    return next(r for r in g["rows"] if r["code"] == code)


def typed(c, rid, qty, code="39b"):
    r = c.post(f"{P}/progress/rooms", json={"service": SVC, "item_code": code, "entries": [{"room": rid, "qty": qty}]})
    assert r.status_code == 200, r.text


def measured(c, rid, qty, frm="FACP-1", to="Loop A", code="39b"):
    r = c.post(f"{P}/progress/runs", json={"service": SVC, "item_code": code, "room": rid, "from": frm, "to": to, "qty": qty})
    assert r.status_code == 200, r.text


# ------------------------------------------------------------------ BOQ qty vs site plan (the mislabel in the first mockup)
def test_boq_qty_and_site_plan_are_separate_numbers(site):
    c, d, ids = site
    g = grid(c)
    assert g["basis"] == "total"
    a, b = row(g, "39a"), row(g, "39b")
    assert (a["boq_qty"], a["plan"]) == (4200.0, 5120.0)          # plan raised above the BOQ
    assert a["plan_above_boq"] is True
    assert (b["boq_qty"], b["plan"]) == (1947.5, 1259.5)          # plan edited below the BOQ
    assert b["plan_above_boq"] is False
    assert row(g, "39f")["boq_qty"] == 3534.0 and row(g, "39f")["plan"] == 1683.0


def test_nothing_recorded_gives_zero_and_full_pending(site):
    c, d, ids = site
    b = row(grid(c), "39b")
    assert b["done"] == 0 and b["pending"] == 1947.5 and b["pct"] == 0.0
    assert b["entries"] == {"n": 0, "backed": 0.0, "other": 0.0, "latest": []}


# ------------------------------------------------------------------ the numbers are Site Progress's own
def test_cells_and_total_follow_what_was_recorded(site):
    c, d, ids = site
    measured(c, ids[1], 37.0, "FACP-1", "Riser 2")                # B3 > Zone 2, a measured run
    measured(c, ids[1], 9.4, "Riser 2", "Zone 2 end")
    typed(c, ids[4], 58.0)                                        # B3 > Zone 5, typed (no entries)
    g = grid(c)
    b = row(g, "39b")
    assert g["cells"][f"39b|{ids[1]}"] == pytest.approx(46.4)
    assert g["cells"][f"39b|{ids[4]}"] == pytest.approx(58.0)
    assert b["done"] == pytest.approx(104.4)
    assert b["pending"] == pytest.approx(1947.5 - 104.4)          # against the BOQ, not against the site plan
    assert b["pct"] == pytest.approx(round(100 * 104.4 / 1947.5, 1))
    # the row total is exactly what Site Progress itself reports as used
    view = c.get(f"{P}/service/{SVC}").json()
    used = next(i["used"] for i in view["items"] if i["code"] == "39b")
    assert b["done"] == pytest.approx(used, abs=1e-3)


def test_evidence_says_how_much_was_measured(site):
    c, d, ids = site
    measured(c, ids[1], 37.0, "FACP-1", "Riser 2")
    measured(c, ids[1], 9.4, "Riser 2", "Zone 2 end")
    typed(c, ids[4], 58.0)
    e = row(grid(c), "39b")["entries"]
    assert e["n"] == 2
    assert e["backed"] == pytest.approx(46.4)                     # explained by the two entries
    assert e["other"] == pytest.approx(58.0)                      # the typed zone has no entry behind it
    assert [x["qty"] for x in e["latest"]] == [9.4, 37.0]         # newest first
    assert e["latest"][0]["from"] == "Riser 2" and e["latest"][0]["to"] == "Zone 2 end"
    assert e["latest"][0]["place"] == "B3 \u25B8 Zone 2"                   # the level is named too: "Zone 2" alone exists on every level


def test_cancelled_entry_is_not_counted_as_measured(site):
    c, d, ids = site
    measured(c, ids[1], 20.0)
    rid = c.get(f"{P}/progress/runs", params={"service": SVC, "item_code": "39b"}).json()["rooms"][ids[1]]["runs"][0]["id"]
    r = c.post(f"{P}/progress/runs/cancel", json={"service": SVC, "item_code": "39b", "scope": "zone", "room": ids[1], "run_id": rid, "reason": "wrong zone"})
    assert r.status_code == 200, r.text
    b = row(grid(c), "39b")
    assert b["entries"]["n"] == 0 and b["entries"]["latest"] == []
    assert b["done"] == pytest.approx(0.0, abs=1e-3)


# ------------------------------------------------------------------ pending is never negative; over-BOQ is flagged
def test_done_above_boq_is_flagged_and_pending_floors_at_zero(site):
    c, d, ids = site
    # 39a: BOQ 4,200, plan 5,120 over 21 zones (243.8 a zone). Fill every zone: 5,120 done > BOQ 4,200
    for rid in ids:
        typed(c, rid, 5120.0 / 21, "39a")
    a = row(grid(c), "39a")
    assert a["done"] == pytest.approx(5120.0, abs=0.01)
    assert a["done_above_boq"] is True and a["pending"] == 0.0


# ------------------------------------------------------------------ applicability and 'never invent a number'
def test_zone_an_item_does_not_apply_to_has_no_cell(site):
    c, d, ids = site
    (d / "item_rooms.json").write_text(json.dumps({SVC: {"39c": ids[:7]}}))     # 39c only in B3
    g = grid(c)
    assert f"39c|{ids[0]}" in g["cells"] and f"39c|{ids[7]}" not in g["cells"]
    assert f"39b|{ids[7]}" in g["cells"]                                          # others still apply everywhere
    assert row(g, "39c")["plan"] == pytest.approx(1096.2)


def test_per_room_service_has_no_boq_qty(site):
    c, d, ids = site
    g = grid(c, "HVAC")
    assert g["basis"] == "unknown"
    r = row(g, "H.1")
    assert r["boq_qty"] is None and r["pending"] is None and r["pct"] is None
    assert r["plan"] == pytest.approx(10.0 * 21)                  # per-room qty x 21 zones is the plan the app already works with
    assert r["plan_above_boq"] is False


def test_unknown_service_is_404(site):
    c, d, ids = site
    assert c.get(f"{P}/measure-grid/Plumbing").status_code == 404


def plan(c, code):
    r = c.get(f"{P}/item-room-plan", params={"service": SVC, "item_code": code})
    assert r.status_code == 200, r.text
    return r.json()


def runs(c, code="39b"):
    r = c.get(f"{P}/progress/runs", params={"service": SVC, "item_code": code})
    assert r.status_code == 200, r.text
    return r.json()


# ------------------------------------------------------------------ what a cell may be typed into (the grid becomes editable)
def lock(g, code, rid):
    return g["locks"].get(f"{code}|{rid}")


def test_a_zone_made_of_measurement_entries_is_locked_and_a_typed_one_is_not(site):
    c, d, ids = site
    measured(c, ids[1], 37.0, "FACP-1", "Riser 2")
    typed(c, ids[4], 58.0)
    g = grid(c)
    assert lock(g, "39b", ids[1])["kind"] == "entries" and "Measurement sheet" in lock(g, "39b", ids[1])["why"]
    assert lock(g, "39b", ids[4]) is None                          # typed quantities are exactly what the grid edits
    assert lock(g, "39b", ids[0]) is None and lock(g, "39c", ids[1]) is None


def test_a_cancelled_entry_unlocks_the_zone(site):
    c, d, ids = site
    measured(c, ids[1], 20.0)
    rid = runs(c)["rooms"][ids[1]]["runs"][0]["id"]
    assert lock(grid(c), "39b", ids[1]) is not None
    c.post(f"{P}/progress/runs/cancel", json={"service": SVC, "item_code": "39b", "scope": "zone", "room": ids[1], "run_id": rid, "reason": "x"})
    assert lock(grid(c), "39b", ids[1]) is None


def test_zones_inside_a_whole_level_total_are_locked(site):
    c, d, ids = site
    r = c.post(f"{P}/progress/area-total", json={"service": SVC, "item_code": "39b", "rooms": ids[7:14], "qty": 140.0})
    assert r.status_code == 200, r.text
    g = grid(c)
    for rid in ids[7:14]:
        assert lock(g, "39b", rid)["kind"] == "area" and "B2" in lock(g, "39b", rid)["why"]
    assert lock(g, "39b", ids[0]) is None                          # other levels stay typable
    assert row(g, "39b")["done"] == pytest.approx(140.0, abs=0.01)


def test_whole_project_entries_lock_every_zone_of_that_item_only(site):
    c, d, ids = site
    r = c.post(f"{P}/progress/runs", json={"service": SVC, "item_code": "39b", "scope": "project", "from": "Main riser", "to": "Roof", "qty": 80.0})
    assert r.status_code == 200, r.text
    g = grid(c)
    assert all(lock(g, "39b", rid)["kind"] == "project" for rid in ids)
    assert all(lock(g, "39c", rid) is None for rid in ids)


def test_row_flags_count_unit_and_removed_plan(site):
    c, d, ids = site
    g = grid(c)
    assert row(g, "N.1")["count"] is True and row(g, "39b")["count"] is False
    assert row(g, "39b")["free"] is False
    assert c.post(f"{P}/item-plan-off", json={"service": SVC, "item_code": "39b"}).status_code == 200
    assert row(grid(c), "39b")["free"] is True and row(grid(c), "39c")["free"] is False


def test_one_item_can_be_refreshed_alone(site):
    c, d, ids = site
    typed(c, ids[4], 58.0)
    r = c.get(f"{P}/measure-grid/{SVC}", params={"code": "39b"}).json()
    assert [x["code"] for x in r["rows"]] == ["39b"] and r["rows"][0]["done"] == pytest.approx(58.0)
    assert r["rows"][0]["boq_qty"] == 1947.5                       # same basis as the whole-service read
    assert all(k.startswith("39b|") for k in r["cells"])
    assert c.get(f"{P}/measure-grid/{SVC}", params={"code": "nope"}).status_code == 404


# ------------------------------------------------------------------ the write path the grid will use (facts the grid must respect)
def test_typing_above_a_zones_plan_is_silently_cut_to_the_plan_by_the_server(site):
    """So the grid must never send it blindly: it checks the zone's plan first and asks before removing the plan."""
    c, d, ids = site
    plan_b3z1 = plan(c, "39b")["rooms"][ids[0]]["planned"]
    typed(c, ids[0], plan_b3z1 * 3, "39b")
    g = grid(c)
    assert g["cells"][f"39b|{ids[0]}"] == pytest.approx(plan_b3z1, abs=1e-3)


def test_after_the_plan_is_removed_a_zone_holds_what_was_typed(site):
    c, d, ids = site
    assert c.post(f"{P}/item-plan-off", json={"service": SVC, "item_code": "39b"}).status_code == 200
    typed(c, ids[0], 300.0, "39b")                                 # far above an equal share (59.98), still inside the item's total
    assert grid(c)["cells"][f"39b|{ids[0]}"] == pytest.approx(300.0)
    over = c.post(f"{P}/progress/rooms", json={"service": SVC, "item_code": "39b", "entries": [{"room": ids[1], "qty": 5000.0}]})
    assert over.status_code == 400 and "planned total" in over.text   # the item's total is the one limit


# ------------------------------------------------------------------ the sum behind a typed quantity (=112+155)
def save_qty(c, rid, qty, expr=None, code="39c"):
    e = {"room": rid, "qty": qty}
    if expr is not None:
        e["expr"] = expr
    r = c.post(f"{P}/progress/rooms", json={"service": SVC, "item_code": code, "entries": [e]})
    assert r.status_code == 200, r.text


def test_a_sum_typed_in_a_box_is_kept_with_its_pieces(site):
    c, d, ids = site
    save_qty(c, ids[0], 40, "=10+30")
    g = grid(c)
    assert g["cells"][f"39c|{ids[0]}"] == 40 and g["exprs"] == {f"39c|{ids[0]}": "=10+30"}
    save_qty(c, ids[1], 12.5, "10 + 2.5")                                  # no "=", spaces: remembered normalised
    assert grid(c)["exprs"][f"39c|{ids[1]}"] == "=10+2.5"
    save_qty(c, ids[2], 12, "3x4")                                         # x as times
    assert grid(c)["exprs"][f"39c|{ids[2]}"] == "=3*4"


def test_a_sum_that_does_not_add_up_or_is_not_a_sum_is_not_kept(site):
    c, d, ids = site
    save_qty(c, ids[0], 41, "=10+30")                                       # adds to 40, not 41
    save_qty(c, ids[1], 5, "5")                                             # a plain number has no pieces
    save_qty(c, ids[2], 5, "=A1+4")                                         # a cell reference is not a sum of numbers
    save_qty(c, ids[3], 9, "__import__('os')")
    assert grid(c)["exprs"] == {}


def test_a_sum_never_outlives_the_number_it_adds_up_to(site):
    c, d, ids = site
    save_qty(c, ids[0], 40, "=10+30")
    assert grid(c)["exprs"]
    save_qty(c, ids[0], 45)                                                 # typed again without a sum: forgotten
    assert grid(c)["exprs"] == {}
    save_qty(c, ids[0], 40, "=10+30")
    r = c.post(f"{P}/progress/area-total", json={"service": SVC, "item_code": "39c", "rooms": ids[:7], "qty": 70.0})   # changed by another route
    assert r.status_code == 200, r.text
    g = grid(c)
    assert g["exprs"] == {} and g["cells"][f"39c|{ids[0]}"] != 40


def test_a_sum_survives_the_plan_fraction_rounding_of_a_big_zone_plan(site):
    # 39a has a plan of 5120 over 21 zones (243.8 each): the stored fraction is rounded to 6 decimals, so 143.7 reads back as 143.7001.
    # The sum must still show -- that rounding is not somebody changing the number.
    c, d, ids = site
    cases = [(143.7, "=100+43.7"), (200.5, "=150+50.5"), (77.77, "=50+27.77"), (199.99, "=100+99.99")]
    for i, (q, e) in enumerate(cases):
        save_qty(c, ids[i], q, e, "39a")
    g = grid(c)
    for i, (q, e) in enumerate(cases):
        assert g["cells"][f"39a|{ids[i]}"] == pytest.approx(q, abs=1e-3)
        assert g["exprs"].get(f"39a|{ids[i]}") == e, (q, g["cells"][f"39a|{ids[i]}"])
    save_qty(c, ids[0], 145.0, None, "39a")                                   # a real change (not rounding) still drops the sum
    assert f"39a|{ids[0]}" not in grid(c)["exprs"]


def test_a_count_item_keeps_its_sum_and_a_locked_box_has_none(site):
    c, d, ids = site
    save_qty(c, ids[0], 5, "=2+3", "N.1")
    assert grid(c)["exprs"] == {f"N.1|{ids[0]}": "=2+3"}
    measured(c, ids[1], 20.0)
    assert not any(k.startswith("39b|") for k in grid(c)["exprs"])
