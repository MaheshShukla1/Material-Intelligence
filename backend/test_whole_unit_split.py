"""A counted item (Nos ...) shared over its zones is shared in WHOLE units: 7 Nos over 4 zones is 2, 2, 2, 1 -- never 1.75 a zone.

The reported case: a BOQ line of 7 Nos, 4 applicable zones. Every zone showed "of 1.75 Nos", ticking one zone gave 1.75 fans, and the
screen/DPR could only round that (2 + 2 + 2 + 2 = 8 on screen, 7 in the data). Through the real routes: the BOQ uploaded, zones ticked,
quantities typed, the DPR exported and read back. Lengths (Rmt) and totals that already divide evenly must not change at all."""
import io
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import openpyxl
import pytest

from backend import itemprog
from backend.test_dpr_count_units import env, export, dpr_lines, day, typed, percent, U       # the same fixture: 5 fan lines + a tray, 4 zones

SVC = "HVAC"


def plan_of(c, key):
    r = c.get(f"{U}/item-room-plan", params={"service": SVC, "item_code": c.codes[key]})
    assert r.status_code == 200, r.text
    return r.json()


def zones_of(c, key):
    return list(plan_of(c, key)["rooms"])            # the item's rooms in the order the shares are handed out


def item(c, key):
    r = c.get(f"{U}/service/{SVC}")
    assert r.status_code == 200, r.text
    return next(i for i in r.json()["items"] if i["code"] == c.codes[key])


def tick(c, key, rooms, done=True):
    r = c.post(f"{U}/mark-rooms-done", json={"service": SVC, "item_code": c.codes[key], "rooms": rooms, "done": done})
    assert r.status_code == 200, r.text


def test_seven_nos_over_four_zones_is_2_2_2_1(env):
    c = env
    p = plan_of(c, "c")
    assert p["unit"] == "Nos"
    shares = [p["rooms"][z]["planned"] for z in p["rooms"]]
    assert shares == [2.0, 2.0, 2.0, 1.0]                      # whole, the first zones carry the extra unit
    assert sum(shares) == item(c, "c")["planned"] == 7          # and they add back up to the item's own total


def test_nine_six_and_ten_nos_split_the_same_way(env):
    c = env
    for key, total, want in (("e", 9, [3, 2, 2, 2]), ("f", 6, [2, 2, 1, 1]), ("j", 10, [3, 3, 2, 2])):
        p = plan_of(c, key)
        got = [p["rooms"][z]["planned"] for z in p["rooms"]]
        assert got == [float(x) for x in want], (key, got)
        assert sum(got) == total


def test_a_total_that_already_divides_evenly_is_untouched(env):
    c = env
    p = plan_of(c, "d")                                         # 8 Nos over 4 zones
    assert [p["rooms"][z]["planned"] for z in p["rooms"]] == [2.0, 2.0, 2.0, 2.0]


def test_a_length_keeps_its_equal_decimal_share(env):
    """100 Rmt over 4 zones is 25 each; and a length that does NOT divide stays a real decimal share (only counted units go whole)."""
    c = env
    p = plan_of(c, "tray")
    assert [p["rooms"][z]["planned"] for z in p["rooms"]] == [25.0, 25.0, 25.0, 25.0]
    pl = c.dir / "planned.json"
    allp = json.loads(pl.read_text())
    allp[SVC][c.codes["tray"]] = 70.0                           # 70 Rmt over 4 zones
    pl.write_text(json.dumps(allp))
    p = plan_of(c, "tray")
    assert [p["rooms"][z]["planned"] for z in p["rooms"]] == [17.5, 17.5, 17.5, 17.5]


def test_ticking_the_zones_one_by_one_adds_whole_fans(env):
    c = env
    zs = zones_of(c, "c")
    seen = []
    for z in zs:
        tick(c, "c", [z])
        seen.append(item(c, "c")["used"])
    assert seen == [2, 4, 6, 7]                                 # was 1.75, 3.5, 5.25, 7
    last = item(c, "c")
    assert last["used"] == last["planned"] == 7 and last["pct"] == pytest.approx(100.0)


def test_progress_percent_is_the_real_share_not_the_zone_count(env):
    """One zone of the four holds 2 of the 7 fans: 28.6 %, not 25 %."""
    c = env
    tick(c, "c", [zones_of(c, "c")[0]])
    assert item(c, "c")["pct"] == pytest.approx(100 * 2 / 7, abs=0.06)


def test_the_overall_box_still_means_the_typed_number(env):
    """Typing 6 of 7 in the item's own box is 6 fans, whichever zones they are in."""
    c = env
    typed(c, "c", 6)
    it = item(c, "c")
    assert it["used"] == pytest.approx(6.0) and it["pct"] == pytest.approx(100 * 6 / 7, abs=0.06)


def test_a_typed_zone_quantity_is_whole_and_clamped_to_that_zones_share(env):
    c = env
    zs = zones_of(c, "c")
    r = c.post(f"{U}/progress/rooms", json={"service": SVC, "item_code": c.codes["c"], "entries": [{"room": zs[3], "qty": 0.6}]})
    assert r.status_code == 200, r.text                         # the last zone holds 1 fan; 0.6 is a whole fan
    assert item(c, "c")["used"] == 1
    r = c.post(f"{U}/progress/rooms", json={"service": SVC, "item_code": c.codes["c"], "entries": [{"room": zs[0], "qty": 1.4}]})
    assert r.status_code == 200
    assert item(c, "c")["used"] == 2                            # 1.4 -> 1 of the first zone's 2, plus the last zone's 1
    r = c.post(f"{U}/progress/rooms", json={"service": SVC, "item_code": c.codes["c"], "entries": [{"room": zs[0], "qty": 5.5}]})
    assert r.status_code == 200
    assert item(c, "c")["used"] == 3                            # 5.5 -> 6, above the zone's share of 2, so the zone is full: 2 + 1


def test_a_whole_area_total_of_a_counted_item_is_whole(env):
    c = env
    zs = zones_of(c, "c")
    r = c.post(f"{U}/progress/area-total", json={"service": SVC, "item_code": c.codes["c"], "rooms": zs[:2], "qty": 3.5})
    assert r.status_code == 200, r.text
    assert item(c, "c")["used"] == 4                            # 3.5 -> 4 of the 2 + 2 these two zones hold: both full


def test_the_dpr_prints_the_whole_fans_each_tick_added(env):
    """Today the engineer ticks the first two zones (2 + 2 fans) and the last one (1 fan): the DPR line is 5, a whole number."""
    c = env
    day(14)
    zs = zones_of(c, "c")
    tick(c, "c", zs[:2])
    tick(c, "c", [zs[3]])
    got = dpr_lines(export(c, 14, "none"))["FANC"]
    assert (got["prev"], got["today"], got["cum"], got["bal"]) == (0, 5, 5, 2)
    assert got["fmt"] == "#,##0" and float(got["today"]) == int(got["today"])
    zones = c.get(f"{U}/service/{SVC}").json()
    assert zones                                                # the screen's own data is reachable


def test_the_dpr_logged_quantity_per_zone_is_the_zones_whole_share(env):
    c = env
    day(14)
    zs = zones_of(c, "c")
    for z in zs:
        tick(c, "c", [z])
    log = json.loads((c.dir / "dpr_log.json").read_text())
    qtys = sorted(e["qty"] for e in log if e["item_code"] == c.codes["c"])
    assert qtys == [1.0, 2.0, 2.0, 2.0]


def test_the_older_daily_updates_sheet_prints_nos_whole_too(env):
    c = env
    day(14)
    zs = zones_of(c, "c")
    tick(c, "c", zs[:2])
    r = c.get(f"{U}/export-dpr", params={"start": "2026-10-14"})
    assert r.status_code == 200, r.text
    wb = openpyxl.load_workbook(io.BytesIO(r.content))
    texts = [str(cell.value) for ws in wb for row in ws.iter_rows() for cell in row if isinstance(cell.value, str) and cell.value.endswith(" Nos")]
    assert texts, [ws.title for ws in wb]
    assert all(t.split(" ")[0].replace(",", "").isdigit() for t in texts), texts      # "4 Nos", never "4.00 Nos"


# ------------------------------------------------------------------ the pure helper
@pytest.mark.parametrize("unit,total,rooms,want", [
    ("Nos", 7, ["a", "b", "c", "d"], {"a": 2, "b": 2, "c": 2, "d": 1}),
    ("Nos", 523, list("abcdefghijklmn"), None),                                  # 523 over 14: 37 each + 5 extra (checked below)
    ("Nos", 8, ["a", "b", "c", "d"], None),                                      # divides evenly: nothing changes
    ("Nos", 7, ["a"], None),                                                     # one room: nothing to share
    ("Nos", 7.5, ["a", "b"], None),                                              # not a whole total: nothing invented
    ("Nos", 0, ["a", "b"], None),
    ("Rmt", 7, ["a", "b", "c", "d"], None),                                      # a length is not counted
    ("Set", 5, ["a", "b"], {"a": 3, "b": 2}),
    (None, 7, ["a", "b", "c", "d"], None),
])
def test_whole_split(unit, total, rooms, want):
    got = itemprog.whole_split(unit, total, rooms)
    if total == 523 and unit == "Nos":
        assert sum(got.values()) == 523 and sorted(set(got.values())) == [37.0, 38.0]
        assert list(got.values()) == [38.0] * 5 + [37.0] * 9                      # the first rooms carry the extras
        return
    assert (got if got is None else {k: int(v) for k, v in got.items()}) == want


def test_every_counted_unit_the_screen_knows_is_split_whole():
    for u in sorted(itemprog.COUNT_UNITS):
        assert itemprog.whole_split(u, 5, ["a", "b", "c"]) == {"a": 2.0, "b": 2.0, "c": 1.0}, u
