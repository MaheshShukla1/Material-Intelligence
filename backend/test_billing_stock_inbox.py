"""Challans straight from the stock register. Material reaches site, the store enters it in the register, and the
challan is made from what arrived: the register's dated IN rows are an inbox; the engineer adds them to an RA.
Real scenario: Thoth Mall Electrical on delivery challans, plus the real wire deliveries of the site's WIRE CHALLAN
sheet (1207 / 615 / 617) coming from the register instead of being typed again."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd
import pytest

from backend import siteprogress as sp, billing
from backend.test_billing_wire import env, B, SLUG, new_ra, alloc, real_alloc, wch, sz, code  # noqa: F401

E = "Electrical"
BASE_ROWS = [
    (E, "SWITCH 6A MODULE BOX", "Box", "2026-07-04", 5, 0.0),          # linked to 1.1.6 (Nos), factor 0.1 box per Nos
    (E, "MCB 32A FP", "Pkt", "2026-07-09", 4, 0.0),                    # linked to 1.1.18, units differ, no factor
    (E, "MAINS CABLE 6SQMM", "RMT", "2026-06-01", 100, 40.0),          # linked to 1.2.1 (Rmt): same unit -> factor 1; opening 40
    (E, "MAINS CABLE 6SQMM", "RMT", "2026-07-12", 300, 40.0),
    (E, "PVC CLAMP 20MM", "Nos", "2026-07-14", 500, 0.0),              # not linked to any BOQ line
    ("Plumbing", "CPVC PIPE 25MM", "Mtr", "2026-07-10", 200, 0.0),     # another service's material
]
WIRE = {  # date -> {size: {colour: metres}}  (the real WIRE CHALLAN sheet)
    "2026-03-28": {"1.5": dict(RED=300, YELLOW=600, BLUE=300, BLACK=900, GREEN=1500), "2.5": dict(RED=300, YELLOW=300, BLUE=300, BLACK=600)},
    "2026-07-02": {"1.5": dict(RED=3300, YELLOW=3000, BLUE=2700, BLACK=9600, GREEN=13800), "2.5": dict(RED=1500, YELLOW=1500, BLUE=1200, BLACK=3900)},
    "2026-07-16": {"1.5": dict(RED=3900, YELLOW=3600, BLUE=2400, BLACK=10500, GREEN=14700), "2.5": dict(RED=1500, YELLOW=1500, BLUE=1800, BLACK=4800)},
}


def wire_rows(scale=1.0, skip=()):
    out = []
    for dte, bysz in WIRE.items():
        if dte in skip:
            continue
        for size, cols in bysz.items():
            for col, m in cols.items():
                out.append((E, f"{size}SQMM FRLS WIRE {col}", "Mtr", dte, m * scale, 0.0))
    return out


def register(rows, run="r1", created="2026-10-01T10:00:00"):
    df = pd.DataFrame(rows, columns=["service", "material", "unit", "date", "qty_in", "opening"])
    df["date"] = pd.to_datetime(df["date"])
    df["qty_out"] = 0.0
    df["balance"] = np.nan
    rd = sp.RUNS / run
    rd.mkdir(parents=True, exist_ok=True)
    df.to_parquet(rd / "daily.parquet")
    (rd / "meta.json").write_text(json.dumps({"run_id": run, "project_slug": SLUG, "created": created}))


def links(d):
    (d / "links.json").write_text(json.dumps({E: {
        "1.1.6": [{"material": "SWITCH 6A MODULE BOX", "factor": 0.1}],
        "1.1.18": [{"material": "MCB 32A FP", "factor": None}],
        "1.2.1": [{"material": "MAINS CABLE 6SQMM", "factor": None}],
    }}))


@pytest.fixture
def inbox(env):
    client, d = env
    register(BASE_ROWS)
    links(d)
    return client, d


def inb(client, **kw):
    r = client.get(f"{B}/stock-inbox/Electrical", params=kw)
    assert r.status_code == 200, r.text
    return r.json()


def entry(j, material, date=None, opening=False):
    return next(e for e in j["entries"] if e["material"] == material and (date is None or e["date"] == date) and e["opening"] is opening)


def accept(client, *keys, **kw):
    items = [{"key": k, **kw.pop("each", {})} for k in keys]
    return client.post(f"{B}/stock-inbox/Electrical/accept", json={"items": items, **kw})


# --------------------------------------------------------------- the inbox itself
def test_inbox_lists_the_registers_dated_deliveries_with_the_line_they_feed(inbox):
    client, _ = inbox
    j = inb(client)
    assert j["ready"] and j["open_no"] == 1
    sw = entry(j, "SWITCH 6A MODULE BOX")
    assert (sw["date"], sw["qty"], sw["unit"], sw["scope"], sw["kind"]) == ("2026-07-04", 5, "Box", "this", "line")
    c = sw["lines"][0]
    assert (c["item_code"], c["factor"], c["counts_as"], c["problem"], c["dup"]) == ("1.1.6", 0.1, 50, None, None)
    assert sw["item_code"] == "1.1.6"
    assert j["counts"]["waiting"] == 6 and j["counts"]["other"] == 1


def test_a_material_with_no_link_is_never_matched_by_name_it_asks_to_be_linked(inbox):
    client, _ = inbox
    e = entry(inbox[0] and inb(client), "PVC CLAMP 20MM")
    assert e["kind"] == "unlinked" and e["lines"] == [] and e["item_code"] is None
    r = accept(client, e["key"])
    assert r.status_code == 409 and "Link it" in r.text


def test_same_unit_needs_no_factor_but_different_unit_without_factor_cannot_be_added(inbox):
    client, _ = inbox
    j = inb(client)
    cab = entry(j, "MAINS CABLE 6SQMM", "2026-07-12")
    assert cab["lines"][0]["factor"] == 1.0 and cab["lines"][0]["counts_as"] == 300           # RMT = Rmt
    mcb = entry(j, "MCB 32A FP")
    assert mcb["lines"][0]["problem"] == "factor not set" and mcb["lines"][0]["counts_as"] is None
    r = accept(client, mcb["key"])
    assert r.status_code == 409 and "factor not set" in r.text


def test_the_opening_balance_is_one_delivery_so_the_total_matches_the_register(inbox):
    client, _ = inbox
    j = inb(client)
    op = entry(j, "MAINS CABLE 6SQMM", "2026-06-01", opening=True)
    assert op["qty"] == 40
    ins = [e for e in j["entries"] if e["material"] == "MAINS CABLE 6SQMM" and not e["opening"]]
    assert sorted(e["qty"] for e in ins) == [100, 300]
    r = accept(client, op["key"])
    assert r.status_code == 200
    ch = [c for c in client.get(f"{B}/challans/Electrical").json()["challans"]][0]
    assert ch["no"] == "Stock opening" and ch["lines"][0]["qty"] == 40


def test_another_services_material_is_listed_but_cannot_be_added_here(inbox):
    client, _ = inbox
    e = entry(inb(client), "CPVC PIPE 25MM")
    assert e["scope"] == "other" and e["kind"] == "other"
    assert accept(client, e["key"]).status_code == 409


def test_stock_source_units_have_no_inbox(env):
    client, _ = env
    client.post(f"{B}/setup", json={"service": "Electrical", "mode": "separate", "supply_pct": 75, "install_pct": 15, "supply_source": "stock"})
    j = client.get(f"{B}/stock-inbox/Electrical").json()
    assert j["ready"] is False and j["source"] == "stock"


# ------------------------------------------------------------- adding a delivery to an RA
def test_adding_a_delivery_makes_a_frozen_challan_that_feeds_the_supply_bill(inbox):
    client, _ = inbox
    k = entry(inb(client), "SWITCH 6A MODULE BOX")["key"]
    r = accept(client, k)
    assert r.status_code == 200 and r.json()["added"] == 1
    chs = client.get(f"{B}/challans/Electrical").json()["challans"]
    c = chs[0]
    assert (c["no"], c["date"], c["ra"], c["no_missing"]) == ("Stock IN 04-Jul-26", "2026-07-04", 1, True)
    assert c["src"]["kind"] == "stock" and c["src"]["material"] == "SWITCH 6A MODULE BOX"
    assert c["lines"] == [{"item_code": "1.1.6", "desc": c["lines"][0]["desc"], "unit": "Nos", "qty": 50}]
    b = client.get(f"{B}/ra/{new_ra(client)}").json()
    sup = next(l for l in b["lines"] if l["item_code"] == "1.1.6")["supply"]
    assert (sup["this"], sup["source"]) == (50, "challan")
    j = inb(client)
    assert all(e["key"] != k for e in j["entries"]) and j["counts"]["waiting"] == 5        # left the inbox: counted once


def test_the_real_challan_number_is_optional_and_clears_the_missing_tag_when_typed(inbox):
    client, _ = inbox
    j = inb(client)
    a, b = entry(j, "SWITCH 6A MODULE BOX")["key"], entry(j, "MAINS CABLE 6SQMM", "2026-07-12")["key"]
    assert accept(client, a, each={"no": "1188"}).status_code == 200
    assert accept(client, b).status_code == 200
    chs = {c["no"]: c for c in client.get(f"{B}/challans/Electrical").json()["challans"]}
    assert chs["1188"]["no_missing"] is False and chs["Stock IN 12-Jul-26"]["no_missing"] is True
    cid = chs["Stock IN 12-Jul-26"]["id"]
    r = client.post(f"{B}/challans/Electrical/{cid}", json={"no": "2203", "date": "2026-07-12", "ra": 1,
                                                          "lines": [{"item_code": "1.2.1", "qty": 300}]})
    assert r.status_code == 200
    again = {c["no"]: c for c in client.get(f"{B}/challans/Electrical").json()["challans"]}
    assert again["2203"]["no_missing"] is False and again["2203"]["src"]["kind"] == "stock"


def test_two_deliveries_on_one_day_get_distinct_labels(inbox):
    client, d = inbox
    register(BASE_ROWS + [(E, "SWITCH 6A MODULE BOX", "Box", "2026-07-04", 2, 0.0)][:0] +
             [(E, "MAINS CABLE 6SQMM", "RMT", "2026-07-04", 25, 40.0)], run="r2", created="2026-10-02T10:00:00")
    j = inb(client)
    keys = [entry(j, "SWITCH 6A MODULE BOX")["key"], entry(j, "MAINS CABLE 6SQMM", "2026-07-04")["key"]]
    assert accept(client, *keys).status_code == 200
    assert sorted(c["no"] for c in client.get(f"{B}/challans/Electrical").json()["challans"]) == ["Stock IN 04-Jul-26", "Stock IN 04-Jul-26 (2)"]


def test_a_typed_challan_for_the_same_delivery_is_flagged_and_needs_a_decision(inbox):
    client, _ = inbox
    r = client.post(f"{B}/challans/Electrical", json={"no": "0911", "date": "2026-07-04", "lines": [{"item_code": "1.1.6", "qty": 50}]})
    assert r.status_code == 200
    e = entry(inb(client), "SWITCH 6A MODULE BOX")
    assert e["lines"][0]["dup"]["no"] == "0911"
    r = accept(client, e["key"])
    assert r.status_code == 409 and "0911" in r.text and "once" in r.text
    assert accept(client, e["key"], force=True).status_code == 200            # "Different delivery. Continue"
    assert len(client.get(f"{B}/challans/Electrical").json()["challans"]) == 2


def test_all_or_nothing_when_one_of_several_selected_cannot_be_added(inbox):
    client, _ = inbox
    j = inb(client)
    good, bad = entry(j, "SWITCH 6A MODULE BOX")["key"], entry(j, "PVC CLAMP 20MM")["key"]
    assert accept(client, good, bad).status_code == 409
    assert client.get(f"{B}/challans/Electrical").json()["challans"] == []


def test_a_delivery_can_go_to_a_later_ra_or_be_held_back(inbox):
    client, _ = inbox
    j = inb(client)
    assert accept(client, entry(j, "SWITCH 6A MODULE BOX")["key"], ra=2).status_code == 200
    assert accept(client, entry(j, "MAINS CABLE 6SQMM", "2026-07-12")["key"], ra=None).status_code == 200
    s = {c["no"]: c["state"] for c in client.get(f"{B}/challans/Electrical").json()["challans"]}
    assert s == {"Stock IN 04-Jul-26": "later", "Stock IN 12-Jul-26": "held"}


def test_deleting_a_stock_challan_puts_the_delivery_back_in_the_inbox(inbox):
    client, _ = inbox
    k = entry(inb(client), "SWITCH 6A MODULE BOX")["key"]
    accept(client, k)
    cid = client.get(f"{B}/challans/Electrical").json()["challans"][0]["id"]
    assert client.delete(f"{B}/challans/Electrical/{cid}").status_code == 200
    assert any(e["key"] == k for e in inb(client)["entries"])


# ---------------------------------------------------------- hold / not billable / search scope
def test_hold_and_not_billable_remove_from_waiting_and_can_be_brought_back(inbox):
    client, _ = inbox
    j = inb(client)
    h, n = entry(j, "SWITCH 6A MODULE BOX")["key"], entry(j, "PVC CLAMP 20MM")["key"]
    assert client.post(f"{B}/stock-inbox/Electrical/mark", json={"action": "hold", "keys": [h]}).status_code == 200
    assert client.post(f"{B}/stock-inbox/Electrical/mark", json={"action": "skip", "keys": [n]}).status_code == 200
    j = inb(client)
    assert j["counts"] == {"waiting": 4, "held": 1, "dismissed": 1, "other": 1}
    assert accept(client, n).status_code == 409                               # marked Not billable
    assert accept(client, h).status_code == 200                               # a held one can still be added
    client.post(f"{B}/stock-inbox/Electrical/mark", json={"action": "unskip", "keys": [n]})
    assert inb(client)["counts"]["dismissed"] == 0


def test_not_billable_applies_to_the_material_so_future_deliveries_stay_hidden(inbox):
    client, d = inbox
    n = entry(inb(client), "PVC CLAMP 20MM")["key"]
    client.post(f"{B}/stock-inbox/Electrical/mark", json={"action": "skip", "keys": [n]})
    register(BASE_ROWS + [(E, "PVC CLAMP 20MM", "Nos", "2026-08-01", 100, 0.0)], run="r2", created="2026-10-02T10:00:00")
    j = inb(client)
    assert [e["state"] for e in j["entries"] if e["material"] == "PVC CLAMP 20MM"] == ["dismissed", "dismissed"]
    assert j["counts"]["waiting"] == 5


# ------------------------------------------------- the register changes after a delivery was added
def test_an_open_ra_shows_the_register_change_and_resync_takes_it(inbox):
    client, _ = inbox
    k = entry(inb(client), "SWITCH 6A MODULE BOX")["key"]
    accept(client, k)
    cid = client.get(f"{B}/challans/Electrical").json()["challans"][0]["id"]
    register([r if r[1] != "SWITCH 6A MODULE BOX" else (E, r[1], r[2], r[3], 4, r[5]) for r in BASE_ROWS], run="r2", created="2026-10-02T10:00:00")
    c = client.get(f"{B}/challans/Electrical").json()["challans"][0]
    assert c["register"] == {"now": 4, "now_counts": 40} and c["lines"][0]["qty"] == 50          # frozen copy did not move
    assert client.post(f"{B}/challans/Electrical/{cid}/resync").status_code == 200
    c = client.get(f"{B}/challans/Electrical").json()["challans"][0]
    assert c["lines"][0]["qty"] == 40 and "register" not in c


def test_a_locked_ra_never_moves_when_the_register_changes(inbox):
    client, _ = inbox
    k = entry(inb(client), "SWITCH 6A MODULE BOX")["key"]
    accept(client, k)
    cid = client.get(f"{B}/challans/Electrical").json()["challans"][0]["id"]
    ra = new_ra(client)
    assert client.post(f"{B}/ra/{ra}/lock").status_code == 200
    register([r if r[1] != "SWITCH 6A MODULE BOX" else (E, r[1], r[2], r[3], 4, r[5]) for r in BASE_ROWS], run="r2", created="2026-10-02T10:00:00")
    c = client.get(f"{B}/challans/Electrical").json()["challans"][0]
    assert c["frozen"] and "register" not in c and c["lines"][0]["qty"] == 50
    assert client.post(f"{B}/challans/Electrical/{cid}/resync").status_code == 409
    assert client.post(f"{B}/challans/Electrical/{cid}", json={"no": "x", "date": "2026-07-04", "lines": [{"item_code": "1.1.6", "qty": 1}]}).status_code == 409


def test_a_delivery_that_left_the_register_is_reported_not_silently_deleted(inbox):
    client, _ = inbox
    accept(client, entry(inb(client), "SWITCH 6A MODULE BOX")["key"])
    register([r for r in BASE_ROWS if r[1] != "SWITCH 6A MODULE BOX"], run="r2", created="2026-10-02T10:00:00")
    c = client.get(f"{B}/challans/Electrical").json()["challans"][0]
    assert c["register"] == {"missing": True} and len(client.get(f"{B}/challans/Electrical").json()["challans"]) == 1


def test_deliveries_up_to_the_last_locked_rafs_cutoff_are_not_new(inbox):
    client, _ = inbox
    r = client.post(f"{B}/ra", json={"service": "Electrical", "series": "supply", "cutoff": "2026-07-05"})
    assert r.status_code == 200
    assert client.post(f"{B}/ra/{r.json()['id']}/lock").status_code == 200
    j = inb(client)
    assert j["since"] == "2026-07-05" and j["earlier"] == 3                       # the 04-Jul switch, the 01-Jun cable and the 01-Jun opening
    assert all(e["date"] > "2026-07-05" for e in j["entries"])
    assert len(inb(client, earlier=1)["entries"]) == len(j["entries"]) + 3


# ------------------------------------------------------------------- wire from the stock register
def wire_up(client, scale=1.0):
    assert client.put(f"{B}/wire/Electrical/source", json={"source": "stock"}).status_code == 200
    mats = client.get(f"{B}/wire/Electrical").json()["stock"]["materials"]
    r = client.put(f"{B}/wire/Electrical/stock-map", json={"confirm": [m["material"] for m in mats]})
    assert r.status_code == 200, r.text
    r = client.post(f"{B}/wire/Electrical/stock-sync")
    assert r.status_code == 200, r.text
    return r.json()


def test_wire_materials_are_not_challans_they_go_to_the_wire_helper(env):
    client, d = env
    register(BASE_ROWS + wire_rows())
    links(d)
    j = inb(client)
    assert not any("WIRE" in e["material"] for e in j["entries"])
    assert j["wire"]["available"] and j["wire"]["entries"] == 27 and j["wire"]["from"] == "2026-03-28" and j["wire"]["to"] == "2026-07-16"


def test_the_wire_materials_are_read_from_the_register_names_and_confirmed_once(env):
    client, d = env
    register(wire_rows())
    st = client.get(f"{B}/wire/Electrical").json()
    assert st["source"] == "stock" and st["source_set"] is False and st["stock"]["available"]
    got = {(m["size"], m["colour"]): (m["counted"], m["confirmed"]) for m in st["stock"]["materials"]}
    assert got[("1.5", "RED")] == (True, False) and got[("1.5", "GREEN")] == (False, False) and got[("2.5", "BLACK")] == (False, False)
    assert len(got) == 9 and st["stock"]["unconfirmed"] == 9
    r = client.post(f"{B}/wire/Electrical/stock-sync")
    assert r.status_code == 409 and "Confirm" in r.text                        # nothing is counted before the engineer confirms


def test_real_ra1_comes_from_the_register_with_no_typing_4020_300_130_points(env):
    client, d = env
    register(wire_rows())
    res = wire_up(client)
    assert (res["added"], res["updated"], res["removed"]) == (3, 0, 0)
    st = client.get(f"{B}/wire/Electrical").json()
    assert [(c["date"], c["ra"], c["no"], c["no_missing"], c["counts"]) for c in st["challans"]] == [
        ("2026-03-28", 1, "Stock IN 28-Mar-26", True, True), ("2026-07-02", 1, "Stock IN 02-Jul-26", True, True),
        ("2026-07-16", 1, "Stock IN 16-Jul-26", True, True)]
    assert {x["size"]: x["counted"] for x in st["size_status"]} == {"1.5": 20100, "2.5": 9900}        # same as the typed WIRE CHALLAN sheet
    assert real_alloc(client).status_code == 200
    st = client.get(f"{B}/wire/Electrical").json()
    assert {l["ref"]: l["points"] for l in st["lines"]} == {"1.1.5": 4020, "1.1.3": 300, "1.1.4": 130}
    assert st["totals"]["supply_this"] == pytest.approx(6428812.5)


def test_typing_a_wire_challan_is_refused_while_the_source_is_the_register(env):
    client, _ = env
    register(wire_rows())
    wire_up(client)
    r = wch(client, "9", "2026-07-01", [sz("1.5", red=5)])
    assert r.status_code == 409 and "stock register" in r.text
    assert client.put(f"{B}/wire/Electrical/source", json={"source": "typed"}).status_code == 200
    assert wch(client, "9", "2026-07-01", [sz("1.5", red=5)]).status_code == 200


def test_switching_source_never_counts_the_same_delivery_twice(env):
    client, _ = env
    register(wire_rows())
    for no, date, a in (("1207", "2026-03-28", dict(red=300, yellow=600, blue=300)),
                        ("615", "2026-07-02", dict(red=3300, yellow=3000, blue=2700)),
                        ("617", "2026-07-16", dict(red=3900, yellow=3600, blue=2400))):
        assert wch(client, no, date, [sz("1.5", **a)]).status_code == 200
    assert {x["size"]: x["counted"] for x in client.get(f"{B}/wire/Electrical").json()["size_status"]} == {"1.5": 20100}
    res = wire_up(client)
    st = client.get(f"{B}/wire/Electrical").json()
    typed = [c for c in st["challans"] if not c["src"]]
    assert all(c["counts"] is False for c in typed)                              # typed ones in the open RA stop counting
    assert {x["size"]: x["counted"] for x in st["size_status"]} == {"1.5": 20100, "2.5": 9900}      # not 40,200
    assert res["typed_kept"] == 0


def test_a_typed_wire_challan_already_in_a_locked_ra_keeps_counting_after_the_switch(env):
    client, _ = env
    register(wire_rows(skip=("2026-03-28",)))
    assert wch(client, "1207", "2026-03-28", [sz("1.5", red=300, yellow=600, blue=300)]).status_code == 200
    assert client.post(f"{B}/ra/{new_ra(client)}/lock").status_code == 200       # RA-01 submitted with the typed challan
    res = wire_up(client)
    assert res["typed_kept"] == 1                                                # the UI warns: its date may also be in the register
    st = client.get(f"{B}/wire/Electrical").json()
    assert [c["counts"] for c in st["challans"] if not c["src"]] == [True]
    assert [c["ra"] for c in st["challans"] if c["src"]] == [2, 2]
    assert {x["size"]: x["counted"] for x in st["size_status"]}["1.5"] == 1200 + 9000 + 9900


def test_refresh_follows_the_register_in_an_open_ra_but_never_touches_a_locked_one(env):
    client, _ = env
    register(wire_rows())
    wire_up(client)
    register(wire_rows(skip=("2026-07-16",)) + [(E, "1.5SQMM FRLS WIRE RED", "Mtr", "2026-07-02", 100, 0.0)][:0], run="r2", created="2026-10-02T10:00:00")
    r = client.post(f"{B}/wire/Electrical/stock-sync")
    assert r.status_code == 200 and (r.json()["removed"], r.json()["added"]) == (1, 0)
    assert [c["date"] for c in client.get(f"{B}/wire/Electrical").json()["challans"]] == ["2026-03-28", "2026-07-02"]
    assert client.post(f"{B}/ra/{new_ra(client)}/lock").status_code == 200
    register(wire_rows(scale=2.0, skip=("2026-07-16",)), run="r3", created="2026-10-03T10:00:00")
    r = client.post(f"{B}/wire/Electrical/stock-sync").json()
    assert r["updated"] == 0 and sorted(r["locked_differs"]) == ["Stock IN 02-Jul-26", "Stock IN 28-Mar-26"]
    assert {x["size"]: x["counted"] for x in client.get(f"{B}/wire/Electrical").json()["size_status"]}["1.5"] == 1200 + 9000


def test_sync_that_would_break_the_metres_already_given_to_lines_is_refused(env):
    client, _ = env
    register(wire_rows())
    wire_up(client)
    assert real_alloc(client).status_code == 200
    register(wire_rows(skip=("2026-07-16",)), run="r2", created="2026-10-02T10:00:00")      # 16-Jul wire no longer in the register
    r = client.post(f"{B}/wire/Electrical/stock-sync")
    assert r.status_code == 409 and "given to point lines" in r.text
    assert len(client.get(f"{B}/wire/Electrical").json()["challans"]) == 3                    # nothing was changed


def test_only_confirmed_materials_make_wire_and_the_name_is_never_guessed(env):
    client, _ = env
    register(wire_rows() + [(E, "WIRE LOOM ASSORTED", "Mtr", "2026-07-02", 50, 0.0), (E, "2.5SQMM CABLE TIE", "Nos", "2026-07-02", 5, 0.0)])
    st = client.get(f"{B}/wire/Electrical").json()
    assert len(st["stock"]["materials"]) == 9                                     # the loom and the cable tie are not wire sizes + colours
    r = client.put(f"{B}/wire/Electrical/stock-map", json={"confirm": ["WIRE LOOM ASSORTED"]})
    assert r.status_code == 400


# --------------------------------------------------------------------- Link it, from the inbox
def test_link_it_from_the_inbox_uses_the_same_item_master_and_needs_a_factor_only_when_units_differ(inbox):
    client, d = inbox
    k = entry(inb(client), "PVC CLAMP 20MM")["key"]
    r = client.post(f"{B}/stock-inbox/Electrical/link", json={"key": k, "item_code": "1.1.18"})
    assert r.status_code == 200, r.text                         # PVC CLAMP unit Nos = BOQ unit Nos: no factor needed
    links_ = json.loads((d / "links.json").read_text())["Electrical"]["1.1.18"]
    assert {"material": "PVC CLAMP 20MM", "factor": None} in links_ and {"material": "MCB 32A FP", "factor": None} in links_
    e = entry(inb(client), "PVC CLAMP 20MM")
    assert e["kind"] == "line" and e["lines"][0]["item_code"] == "1.1.18" and e["lines"][0]["counts_as"] == 500
    assert accept(client, e["key"]).status_code == 200


def test_link_it_refuses_a_different_unit_without_a_factor_then_takes_it(inbox):
    client, d = inbox
    register(BASE_ROWS + [(E, "CONDUIT 20MM", "Mtr", "2026-07-15", 300, 0.0)], run="r2", created="2026-10-02T10:00:00")
    k = entry(inb(client), "CONDUIT 20MM")["key"]
    r = client.post(f"{B}/stock-inbox/Electrical/link", json={"key": k, "item_code": "1.1.6"})
    assert r.status_code == 400 and "Enter how many" in r.text
    assert client.post(f"{B}/stock-inbox/Electrical/link", json={"key": k, "item_code": "1.1.6", "factor": 5}).status_code == 200
    e = entry(inb(client), "CONDUIT 20MM")
    assert e["lines"][0]["factor"] == 5 and e["lines"][0]["counts_as"] == 60
    assert json.loads((d / "links.json").read_text())["Electrical"]["1.1.6"][0]["material"] == "SWITCH 6A MODULE BOX"    # the old link stays


def test_link_it_for_another_services_material_is_refused(inbox):
    client, _ = inbox
    k = entry(inb(client), "CPVC PIPE 25MM")["key"]
    assert client.post(f"{B}/stock-inbox/Electrical/link", json={"key": k, "item_code": "1.1.6", "factor": 1}).status_code == 409
