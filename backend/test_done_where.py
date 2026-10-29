"""'Done where': the one line under an item on Site Progress that says in which zones the item has been executed and how much.

It reads locqty.split() -- the same split the bill's backup sheet (Annexure C) prints -- so these tests tie it to the grid's cells, the
item's own total and the bill split. Shapes: mall (Level > Zone, the Thoth case), hotel (Floor > Room), hospital (Wing > Floor > Room)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from backend import siteprogress as sp, structure
from backend.test_dpr_count_units import env, U                                  # the same fixture: a tray (100 Rmt) + fans
from backend.test_plan_removed import big, plan, item, off, rooms_qty, area, tick, level_of, SVC


def where(c):
    r = c.get(f"{U}/done-where/{SVC}")
    assert r.status_code == 200, r.text
    return r.json()


def of(c, key="tray"):
    return where(c)["items"].get(c.codes[key])


def test_nothing_recorded_gives_no_items(big):
    w = where(big)
    assert w["items"] == {} and w["word"] == "level" and w["leaf"] == "zone"


def test_unknown_service_is_404(big):
    assert big.get(f"{U}/done-where/NOPE").status_code == 404


def test_zone_wise_quantities_come_back_per_level_with_names(big):
    zs = list(plan(big)["rooms"])
    b3, b2 = [z for z in zs if level_of(big, z) == level_of(big, zs[0])], [z for z in zs if level_of(big, z) == level_of(big, zs[7])]
    assert rooms_qty(big, [(b3[1], 40), (b3[4], 14), (b2[2], 30)]).status_code == 200
    it = of(big)
    assert [a["area"] for a in it["areas"]] == [level_of(big, zs[0]), level_of(big, zs[7])]      # structure order, only levels with work
    a3, a2 = it["areas"]
    assert a3["n"] == 7 and a3["whole"] is False and a3["q"] == pytest.approx(54.0)
    assert [z["q"] for z in a3["done"]] == [40.0, 14.0]
    assert len(a3["cells"]) == 7 and [i for i, c in enumerate(a3["cells"]) if c > 0] == [1, 4]
    assert a2["q"] == pytest.approx(30.0) and a2["done"][0]["name"] == "Zone 3"
    assert it["sum"] == pytest.approx(84.0) and it["rest"] == 0
    assert it["unit"] == "Rmt" or it["unit"]                                              # the item's own unit, never invented


def test_it_ties_to_the_item_total_the_grid_and_the_bill_split(big):
    zs = list(plan(big)["rooms"])
    assert rooms_qty(big, [(zs[0], 20), (zs[8], 12.5), (zs[15], 7)]).status_code == 200
    it = of(big)
    g = big.get(f"{U}/measure-grid/{SVC}").json()
    cells = {k.split("|", 1)[1]: v for k, v in g["cells"].items() if k.startswith(big.codes["tray"] + "|") and v > 0}
    mine = {z["id"]: z["q"] for a in it["areas"] for z in a["done"]}
    assert mine == pytest.approx(cells)                                                  # the grid's own cells, zone by zone
    assert it["sum"] == pytest.approx(item(big)["used"], abs=1e-3)                       # and the item's installed quantity
    bill = sp.item_location_qty(big.dir, SVC, big.codes["tray"])
    assert it["sum"] == pytest.approx(bill["sum"]) and mine == pytest.approx(bill["rooms"])


def test_a_whole_area_total_is_one_number_never_split_into_zones(big):
    zs = list(plan(big)["rooms"])
    b3 = [z for z in zs if level_of(big, z) == level_of(big, zs[0])]
    assert off(big).status_code == 200
    assert area(big, b3, 500).status_code == 200
    it = of(big)
    assert len(it["areas"]) == 1
    a = it["areas"][0]
    assert a["whole"] is True and a["q"] == pytest.approx(500.0) and a["cells"] == [] and a["done"] == []
    assert it["sum"] == pytest.approx(500.0)


def test_an_overall_percentage_is_shown_as_rest_not_as_zones(big):
    r = big.post(f"{U}/progress/item", json={"service": SVC, "item_code": big.codes["tray"], "frac": 0.2})
    assert r.status_code == 200
    it = of(big)
    assert it["areas"] == [] and it["rest"] == pytest.approx(230.0) and it["sum"] == pytest.approx(230.0)


def test_with_the_plan_removed_a_typed_quantity_shows_exactly(big):
    z = list(plan(big)["rooms"])[0]
    assert off(big).status_code == 200
    assert rooms_qty(big, [(z, 100)]).status_code == 200
    a = of(big)["areas"][0]
    assert a["done"][0]["q"] == pytest.approx(100.0) and a["q"] == pytest.approx(100.0)


def test_an_item_with_nothing_recorded_is_left_out_while_another_is_there(big):
    zs = list(plan(big)["rooms"])
    assert rooms_qty(big, [(zs[0], 10)]).status_code == 200
    w = where(big)["items"]
    assert big.codes["tray"] in w and big.codes["c"] not in w


# ------------------------------------------------------------------ the three shapes
def _shape(c, s):
    (c.dir / "structure.json").write_text(s.to_json())
    return list(plan(c)["rooms"])


def test_hotel_floor_room(big):
    zs = _shape(big, structure.hotel("Hyatt", floors=["F1", "F2", "F3"], rooms_per_floor=10))
    assert len(zs) == 30
    assert tick(big, zs[10:13]).status_code == 200
    w = where(big)
    it = w["items"][big.codes["tray"]]
    assert w["word"] == "floor" and w["leaf"] == "room"
    assert [(a["area"], a["n"], len(a["done"])) for a in it["areas"]] == [("F2", 10, 3)]


def test_hospital_wing_floor_room_names_the_wing_and_the_floor(big):
    zs = _shape(big, structure.hospital("City", wings=["Wing A", "Wing B"], floors=["G", "1"], rooms_per_floor=3))
    assert len(zs) == 12
    assert tick(big, [zs[0], zs[7]]).status_code == 200
    it = where(big)["items"][big.codes["tray"]]
    assert [(a["area"], a["n"], len(a["done"])) for a in it["areas"]] == [("Wing A · G", 3, 1), ("Wing B · G", 3, 1)]


def test_a_big_hotel_stays_one_entry_per_floor(big):
    zs = _shape(big, structure.hotel("Hyatt", floors=[f"F{i}" for i in range(1, 11)], rooms_per_floor=20))
    assert len(zs) == 200
    assert tick(big, zs[:200]).status_code == 200
    it = where(big)["items"][big.codes["tray"]]
    assert len(it["areas"]) == 10 and all(a["n"] == 20 and len(a["cells"]) == 20 for a in it["areas"])
