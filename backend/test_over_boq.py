"""Quantity above the BOQ is kept (item level), marked, and never billed above the BOQ quantity.
Scenario from the report: ELV 'Supply & laying of 2 Core cable' planned 12,650 Mtr, the engineer types 13,000 -- it used to snap back
to 12,650 without a word. Same shape here on the Fire 'GI pipe 100mm' item: 100 Rmt per room x 4 rooms = 400 planned; the Billing sheet's BOQ qty for the line is 100."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json
import pytest
from backend import siteprogress as sp, billing
from backend.test_billing import env, prepared, make_run, link, progress, setup_fire, line, STOCK1, SLUG   # noqa: F401

SP = f"/api/siteprogress/{SLUG}"
BL = f"/api/billing/{SLUG}"


def item(view, code):
    return next(r for r in view["items"] if r["code"] == code)


def qty(client, code, q):
    r = client.post(f"{SP}/progress/item-qty", json={"service": "Fire", "item_code": code, "qty": q})
    assert r.status_code == 200, r.text
    return r.json()


def test_up_to_planned_is_the_usual_progress(env):
    client, d, _ = env
    before = (d / "planned.json").read_text() if (d / "planned.json").exists() else None
    it = item(qty(client, "3", 60), "3")
    assert it["planned"] == 100 and it["used"] == 60 and round(it["pct"], 1) == 60.0
    assert it["boq_planned"] == 100
    after = (d / "planned.json").read_text() if (d / "planned.json").exists() else None
    assert before == after and not (d / "over_boq.json").exists()                 # nothing raised, nothing recorded


def test_above_planned_is_kept_planned_follows_and_item_is_at_100(env):
    client, d, _ = env
    v = qty(client, "3", 130)
    it = item(v, "3")
    assert it["used"] == 130 and it["planned"] == 130 and it["pct"] == 100.0      # item % never above 100
    assert it["boq_planned"] == 100                                               # the BOQ quantity itself is untouched
    assert it["planned_override"] is True and it["remaining"] == 0
    assert json.loads((d / "planned.json").read_text())["Fire"]["3"] == 130
    assert it["done_val"] > 0


def test_overall_percent_stays_inside_100(env):
    client, d, _ = env
    qty(client, "3", 130)
    o = client.get(f"{SP}/overall").json()
    assert 0 <= o["overall_pct"] <= 100


def test_typing_again_below_the_raised_planned_just_sets_progress(env):
    client, d, _ = env
    qty(client, "3", 130)
    it = item(qty(client, "3", 65), "3")                                          # half of 130
    assert it["used"] == 65 and round(it["pct"]) == 50 and it["planned"] == 130
    assert it["boq_planned"] == 100                                               # still remembered


def test_raising_twice_remembers_the_original_boq(env):
    client, d, _ = env
    qty(client, "3", 130)
    it = item(qty(client, "3", 180), "3")
    assert it["planned"] == 180 and it["boq_planned"] == 100


def test_back_to_boq_qty_restores_the_seeded_figure(env):
    client, d, _ = env
    qty(client, "3", 130)
    r = client.post(f"{SP}/progress/item-qty/undo", json={"service": "Fire", "item_code": "3"})
    assert r.status_code == 200, r.text
    it = item(r.json(), "3")
    assert it["used"] == 100 and it["planned"] == 100 and it["boq_planned"] == 100 and it["pct"] == 100.0
    assert json.loads((d / "planned.json").read_text())["Fire"]["3"] == 100        # exactly the old figure, not a recomputed one
    assert not json.loads((d / "over_boq.json").read_text()).get("Fire", {}).get("3")
    assert client.post(f"{SP}/progress/item-qty/undo", json={"service": "Fire", "item_code": "3"}).status_code == 404


def test_back_to_boq_when_there_was_no_override(env):
    client, d, _ = env
    (d / "planned.json").write_text("{}")                                         # an item with no override: planned = qty x rooms
    base = item(qty(client, "3", 1), "3")["planned"]
    v = qty(client, "3", base + 50)
    assert item(v, "3")["boq_planned"] == base
    it = item(client.post(f"{SP}/progress/item-qty/undo", json={"service": "Fire", "item_code": "3"}).json(), "3")
    assert it["planned"] == base and it["planned_override"] is False
    assert "3" not in json.loads((d / "planned.json").read_text()).get("Fire", {})


def test_manual_planned_edit_takes_over(env):
    client, d, _ = env
    qty(client, "3", 130)
    client.post(f"{SP}/planned", json={"service": "Fire", "item_code": "3", "planned": 150})
    it = item(qty(client, "3", 75), "3")
    assert it["planned"] == 150 and it["boq_planned"] == 150                       # the engineer set 150 himself: no "over" mark
    assert client.post(f"{SP}/progress/item-qty/undo", json={"service": "Fire", "item_code": "3"}).status_code == 404


def test_bad_input(env):
    client, d, _ = env
    assert client.post(f"{SP}/progress/item-qty", json={"service": "Fire", "item_code": "nope", "qty": 5}).status_code == 404
    assert client.post(f"{SP}/progress/item-qty", json={"service": "Fire", "item_code": "3", "qty": "abc"}).status_code == 400
    assert item(qty(client, "3", -5), "3")["used"] == 0                           # negative = 0, as before
    assert client.post(f"{SP}/progress/item-qty", json={"service": "Fire"}).status_code == 400


def test_old_route_still_clamps_at_100_percent(env):
    client, d, _ = env
    r = client.post(f"{SP}/progress/item", json={"service": "Fire", "item_code": "3", "frac": 1.7})
    assert item(r.json(), "3")["used"] == 100


def test_bad_input(env):
    client, d, _ = env
    assert client.post(f"{SP}/progress/item-qty", json={"service": "Fire", "item_code": "nope", "qty": 5}).status_code == 404
    assert client.post(f"{SP}/progress/item-qty", json={"service": "Fire", "item_code": "3", "qty": "abc"}).status_code == 400
    assert item(qty(client, "3", -5), "3")["used"] == 0                           # negative = 0, as before
    assert client.post(f"{SP}/progress/item-qty", json={"service": "Fire"}).status_code == 400


def test_billing_never_bills_above_the_boq_and_says_so(env):
    client, d, tmp = prepared(env)                                                # links + setup; item 3 at 20 of 100
    qty(client, "3", 130)
    r = client.post(f"{BL}/auto")
    assert r.status_code == 200, r.text
    ras = client.get(BL).json()["ras"]
    dr = next(x for x in ras if x["status"] == "draft")
    bill = client.get(f"{BL}/ra/{dr['id']}").json()
    ln = line(bill, "3")
    assert ln["install"]["utd"] == 100                                            # capped at the BOQ qty
    msgs = [w["msg"] for w in bill["warnings"] if w["item_code"] == "3"]
    assert any("30" in m and "above the BOQ qty of 100" in m and "Billed only up to the BOQ qty" in m for m in msgs), msgs


def test_billing_is_quiet_when_not_above_the_boq(env):
    client, d, tmp = prepared(env)
    qty(client, "3", 90)
    client.post(f"{BL}/auto")
    dr = next(x for x in client.get(BL).json()["ras"] if x["status"] == "draft")
    bill = client.get(f"{BL}/ra/{dr['id']}").json()
    assert not [w for w in bill["warnings"] if "above the BOQ qty" in w["msg"]]


def test_item_with_zone_wise_quantities_stays_capped(env):
    """An item whose quantity is set zone by zone (item_room_qty) has no project-wide plan to raise -- itemprog ignores a flat
    override for it -- so a bigger number is capped exactly as before and nothing is written. Found in a real-Chromium run on
    Thoth Mall's HVAC jet fan e#16 (9 Nos in Zone 5)."""
    client, d, _ = env
    rid = sp._all_room_ids(d)[0]
    (d / "item_room_qty.json").write_text(json.dumps({"Fire": {"3": [{"rooms": [rid], "qty": 40}]}}))
    planned_before = item(client.post(f"{SP}/progress/item-qty", json={"service": "Fire", "item_code": "3", "qty": 0}).json(), "3")["planned"]
    plan_file = (d / "planned.json").read_text()
    it = item(qty(client, "3", planned_before + 25), "3")
    assert it["used"] == planned_before and it["planned"] == planned_before and it["pct"] == 100.0
    assert (d / "planned.json").read_text() == plan_file and not (d / "over_boq.json").exists()
