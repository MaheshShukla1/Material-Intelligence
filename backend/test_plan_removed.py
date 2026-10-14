"""'Remove plan' for ONE item of the Zones dialog.

The reported case (Thoth Mall, a perforated cable tray in Rmt, 21 zones B3/B2/B1 x 7, item planned 1,150): the dialog showed
"Whole B3 of 383.33 Rmt" and "of 54.76 Rmt" per zone -- the item's total split equally, which nobody typed. Whole / a zone could
never take more than that share (typed quantity capped, tick credits the share). With the plan removed the typed quantity counts
exactly, the item's own planned total is the one limit, and everything already recorded stays as it is.
Through the real routes; the DPR is read back cell by cell."""
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from backend import siteprogress as sp, structure
from backend.test_dpr_count_units import env, export, dpr_lines, day, U      # the same fixture: a tray (100 Rmt) + fans, 4 zones

SVC = "HVAC"


@pytest.fixture
def big(env):
    """The reported shape: 3 levels x 7 zones = 21 zones, the tray planned at 1,150 Rmt."""
    c = env
    s = structure.mall("Thoth Mall", levels=["B3", "B2", "B1"], zones_per_level=7)
    (c.dir / "structure.json").write_text(s.to_json())
    pl = c.dir / "planned.json"
    allp = json.loads(pl.read_text()) if pl.exists() else {}
    allp.setdefault(SVC, {})[c.codes["tray"]] = 1150.0
    pl.write_text(json.dumps(allp))
    return c


def plan(c, key="tray"):
    r = c.get(f"{U}/item-room-plan", params={"service": SVC, "item_code": c.codes[key]})
    assert r.status_code == 200, r.text
    return r.json()


def item(c, key="tray"):
    r = c.get(f"{U}/service/{SVC}")
    assert r.status_code == 200, r.text
    return next(i for i in r.json()["items"] if i["code"] == c.codes[key])


def off(c, key="tray", value=True):
    return c.post(f"{U}/item-plan-off", json={"service": SVC, "item_code": c.codes[key], "off": value})


def rooms_qty(c, entries, key="tray"):
    return c.post(f"{U}/progress/rooms", json={"service": SVC, "item_code": c.codes[key], "entries": [{"room": r, "qty": q} for r, q in entries]})


def area(c, rooms, qty, key="tray"):
    return c.post(f"{U}/progress/area-total", json={"service": SVC, "item_code": c.codes[key], "rooms": rooms, "qty": qty})


def tick(c, rooms, done=True, key="tray"):
    return c.post(f"{U}/mark-rooms-done", json={"service": SVC, "item_code": c.codes[key], "rooms": rooms, "done": done})


def level_of(c, rid):
    return sp._room_location_map(c.dir)[rid]["location"]


def test_the_reported_screen_is_383_per_level_and_54_per_zone(big):
    p = plan(big)
    assert len(p["rooms"]) == 21 and p["free"] is False
    shares = {round(v["planned"], 2) for v in p["rooms"].values()}
    assert shares == {54.76}
    assert item(big)["planned"] == 1150


def test_before_removing_a_typed_quantity_is_capped_at_the_share(big):
    """What the engineer hit: 100 Rmt typed in one zone comes back as 54.76."""
    z = list(plan(big)["rooms"])[0]
    assert rooms_qty(big, [(z, 100)]).status_code == 200
    assert item(big)["used"] == pytest.approx(54.7619, abs=1e-3)


def test_removing_the_plan_makes_a_zone_take_exactly_the_typed_quantity(big):
    z = list(plan(big)["rooms"])[0]
    assert off(big).status_code == 200
    assert plan(big)["free"] is True
    assert rooms_qty(big, [(z, 100)]).status_code == 200
    it = item(big)
    assert it["used"] == pytest.approx(100.0, abs=1e-6)                  # as typed, not 54.76
    assert it["planned"] == 1150 and it["pct"] == pytest.approx(100 * 100 / 1150, abs=0.06)


def test_whole_area_takes_the_typed_quantity_above_the_equal_share(big):
    p = plan(big)
    zs = list(p["rooms"])
    b3 = [z for z in zs if level_of(big, z) == level_of(big, zs[0])]
    assert len(b3) == 7
    assert area(big, b3, 500).status_code == 200                         # plan still there: 500 > 383.33 -> capped
    assert item(big)["used"] == pytest.approx(1150 * 7 / 21, abs=1e-3)
    assert off(big).status_code == 200
    assert area(big, b3, 500).status_code == 200
    assert item(big)["used"] == pytest.approx(500.0, abs=1e-6)           # now exactly 500
    w = plan(big)
    assert sum(v["planned"] * v["frac"] for v in w["rooms"].values()) == pytest.approx(500.0, abs=1e-4)


def test_what_was_recorded_before_is_not_changed_by_removing_the_plan(big):
    zs = list(plan(big)["rooms"])
    assert tick(big, zs[:3]).status_code == 200
    before = (big.dir / "item_progress.json").read_text()
    used = item(big)["used"]
    assert off(big).status_code == 200
    assert (big.dir / "item_progress.json").read_text() == before        # file untouched
    assert item(big)["used"] == pytest.approx(used)
    assert off(big, value=False).status_code == 200                      # and restoring is just as quiet
    assert (big.dir / "item_progress.json").read_text() == before
    assert item(big)["used"] == pytest.approx(used)


def test_the_item_total_is_still_the_limit(big):
    zs = list(plan(big)["rooms"])
    assert off(big).status_code == 200
    assert rooms_qty(big, [(zs[0], 1000)]).status_code == 200
    r = rooms_qty(big, [(zs[1], 200)])                                   # 1000 + 200 > 1150
    assert r.status_code == 400 and "1,150" in r.json()["detail"] and "1,200" in r.json()["detail"]
    assert item(big)["used"] == pytest.approx(1000.0)                    # nothing of the refused call was written
    assert rooms_qty(big, [(zs[1], 150)]).status_code == 200
    assert item(big)["used"] == pytest.approx(1150.0) and item(big)["pct"] == pytest.approx(100.0)


def test_one_zone_holding_the_whole_item_reads_100_percent_not_more(big):
    zs = list(plan(big)["rooms"])
    assert off(big).status_code == 200
    assert rooms_qty(big, [(zs[0], 1150)]).status_code == 200
    it = item(big)
    assert it["used"] == pytest.approx(1150.0) and it["pct"] == pytest.approx(100.0)


def test_a_plain_tick_has_no_quantity_when_the_plan_is_removed(big):
    zs = list(plan(big)["rooms"])
    assert off(big).status_code == 200
    r = tick(big, zs[:2])
    assert r.status_code == 400 and "quantity" in r.json()["detail"]
    assert item(big)["used"] == 0
    assert rooms_qty(big, [(zs[0], 80)]).status_code == 200
    assert tick(big, [zs[0]], done=False).status_code == 200             # undo is still a tick
    assert item(big)["used"] == 0


def test_other_items_keep_their_plan(big):
    """Only the item the button was pressed on changes -- a fan's zones are still capped at their whole-number share."""
    zs = list(plan(big, "c")["rooms"])
    assert off(big).status_code == 200
    assert plan(big, "c")["free"] is False
    r = rooms_qty(big, [(zs[0], 99)], key="c")
    assert r.status_code == 200
    assert item(big, "c")["used"] <= item(big, "c")["planned"]
    assert plan(big, "c")["rooms"][zs[0]]["frac"] == pytest.approx(1.0)  # still capped at 100 % of the zone's share


def test_restore_is_refused_while_a_zone_holds_more_than_its_share_and_names_it(big):
    zs = list(plan(big)["rooms"])
    assert off(big).status_code == 200
    assert rooms_qty(big, [(zs[0], 100)]).status_code == 200
    r = off(big, value=False)
    assert r.status_code == 400 and "more than an equal share" in r.json()["detail"]
    assert plan(big)["free"] is True
    assert rooms_qty(big, [(zs[0], 50)]).status_code == 200              # lower it
    assert off(big, value=False).status_code == 200
    assert plan(big)["free"] is False
    assert item(big)["used"] == pytest.approx(50.0, abs=1e-6)


def test_an_item_whose_plan_is_quantity_groups_must_remove_them_first(big):
    zs = list(plan(big)["rooms"])
    g = big.post(f"{U}/item-room-qty", json={"service": SVC, "item_code": big.codes["tray"], "rooms": zs[:4], "qty": 40})
    assert g.status_code == 200, g.text
    r = off(big)
    assert r.status_code == 400 and "group" in r.json()["detail"]
    assert plan(big)["free"] is False


def test_unknown_item_and_missing_fields(big):
    assert big.post(f"{U}/item-plan-off", json={"service": SVC}).status_code == 400
    assert big.post(f"{U}/item-plan-off", json={"service": SVC, "item_code": "nope", "off": True}).status_code == 404


def test_the_plan_off_flag_survives_a_reload_and_the_dialog_reports_the_limit(big):
    zs = list(plan(big)["rooms"])
    assert off(big).status_code == 200
    assert rooms_qty(big, [(zs[0], 300)]).status_code == 200
    p = plan(big)
    assert p["free"] is True and p["total"] == pytest.approx(1150.0) and p["used"] == pytest.approx(300.0)
    assert json.loads((big.dir / "item_free.json").read_text()) == {SVC: {big.codes["tray"]: True}}


def test_the_dpr_prints_the_typed_quantity_not_the_share(big):
    day(14)
    zs = list(plan(big)["rooms"])
    assert off(big).status_code == 200
    assert rooms_qty(big, [(zs[0], 100)]).status_code == 200
    got = dpr_lines(export(big, 14, "none"))["150 X 50"]
    assert (got["today"], got["cum"]) == (100, 100), got
    assert got["bal"] == 1050


def test_re_typing_a_smaller_quantity_lowers_it_and_the_dpr_never_goes_negative(big):
    day(14)
    zs = list(plan(big)["rooms"])
    assert off(big).status_code == 200
    assert rooms_qty(big, [(zs[0], 100)]).status_code == 200
    assert rooms_qty(big, [(zs[0], 80)]).status_code == 200
    assert item(big)["used"] == pytest.approx(80.0)
    got = dpr_lines(export(big, 14, "none"))["150 X 50"]
    assert got["today"] is not None and float(got["today"]) >= 0
    assert got["cum"] == 80


def test_a_small_hotel_style_item_keeps_ticking_exactly_as_before(env):
    """No 'Remove plan' pressed: a tick credits the share, a typed quantity is capped, no new key in any reply."""
    c = env
    zs = list(plan(c)["rooms"])
    assert plan(c)["free"] is False and "total" not in plan(c)
    assert tick(c, zs[:2]).status_code == 200
    assert item(c)["used"] == pytest.approx(50.0)
    assert rooms_qty(c, [(zs[2], 99)]).status_code == 200
    assert item(c)["used"] == pytest.approx(75.0)
    assert not (c.dir / "item_free.json").exists()
