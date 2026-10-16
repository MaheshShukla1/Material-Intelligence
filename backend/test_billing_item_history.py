"""Billing -- item history: one BOQ line read across every RA (qty this bill, to date, amount, extra over the BOQ),
with the challans behind it. Read-only. Same harness as test_billing_challan.py (real FastAPI routers).

The scenario is the Hyatt Plumbing one that was reported: a pipe line whose challans are above the BOQ already in RA-1
(accepted as extra), billed together with its installation, and again above the BOQ in the Supply RA-2.
Numbers are scaled down from 4,320 / 6,500 / 1,900 rmt to BOQ 100, RA-1 150, RA-2 +60 (the real rate and % are kept).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from backend.test_billing import env, progress, SLUG, line  # noqa: F401  (env is a fixture)
from backend.test_billing_challan import cs, ch, new_ra, bill, B

PIPE = "3"          # "GI pipe 100mm", Rmt, BOQ 100, rate 800


def history(client, svc="Fire"):
    r = client.get(f"{B}/item-history/{svc}")
    assert r.status_code == 200, r.text
    return r.json()


def item(h, code):
    return next(x for x in h["lines"] if x["item_code"] == code)


def accept_all(client, ra):
    b = bill(client, ra)
    if b.get("excess"):
        r = client.post(f"{B}/ra/{ra}/accept-excess",
                        json={"items": [{"item_code": e["item_code"], "stage": e["stage"]} for e in b["excess"]]})
        assert r.status_code == 200, r.text


def ra1_and_ra2(client):
    """RA-1 Together: pipe challans 150 (BOQ 100) + installation typed 38; locked. RA-2 Supply: challans +60, extra accepted."""
    cs(client, sup=80, ins=10, svc="Fire", mode="together")
    assert ch(client, "1354", "2026-04-27", [(PIPE, 50)]).status_code == 200            # tagged to the open RA (1)
    assert ch(client, "1356", "2026-04-29", [(PIPE, 100), ("2", 2)]).status_code == 200
    ra1 = new_ra(client, series="together")
    accept_all(client, ra1)
    r = client.post(f"{B}/ra/{ra1}/line", json={"item_code": PIPE, "stage": "install", "qty": 38,
                                                 "reason": "as billed", "accept_excess": True})
    assert r.status_code == 200, r.text
    assert client.post(f"{B}/ra/{ra1}/lock").status_code == 200
    assert ch(client, "119", "2026-09-03", [(PIPE, 25)]).status_code == 200            # now tagged RA 2
    assert ch(client, "121", "2026-09-11", [(PIPE, 35)]).status_code == 200
    ra2 = new_ra(client, series="supply")
    accept_all(client, ra2)
    return ra1, ra2


def test_item_history_gives_ra1_and_ra2_quantities_with_their_challans(env):
    client, _, _ = env
    ra1, ra2 = ra1_and_ra2(client)
    h = history(client)
    pipe = item(h, PIPE)
    assert (pipe["boq_qty"], pipe["unit"], pipe["rate"]) == (100, "Rmt", 800)

    rows = {(r["no"], r["stage"]): r for r in pipe["rows"]}
    assert set(rows) == {(1, "supply"), (1, "install"), (2, "supply")}
    s1, i1, s2 = rows[(1, "supply")], rows[(1, "install")], rows[(2, "supply")]
    assert (s1["qty"], s1["prev"], s1["to_date"], s1["extra"], s1["status"], s1["series"]) == (150, 0, 150, 50, "locked", "together")
    assert s1["amount"] == pytest.approx(800 * 150 * .80)
    assert (i1["qty"], i1["pct"], i1["status"]) == (38, 10, "locked")
    assert i1["amount"] == pytest.approx(800 * 38 * .10)
    assert (s2["qty"], s2["prev"], s2["to_date"], s2["extra"], s2["status"], s2["series"]) == (60, 150, 210, 60, "draft", "supply")
    assert s2["amount"] == pytest.approx(800 * 60 * .80)
    assert s2["accepted"] is True and s1["accepted"] is True

    chs = [(c["no"], c["ra"], c["state"], c["qty"]) for c in pipe["challans"]]
    assert chs == [("1354", 1, "locked", 50), ("1356", 1, "locked", 100), ("119", 2, "open", 25), ("121", 2, "open", 35)]
    assert sum(q for *_, q in chs) == 210 == s2["to_date"]            # the challans add up to what the RAs billed


def test_the_ra_rows_are_the_same_numbers_the_ra_screens_show(env):
    client, _, _ = env
    ra1, ra2 = ra1_and_ra2(client)
    pipe = item(history(client), PIPE)
    for ra, no in ((ra1, 1), (ra2, 2)):
        b = bill(client, ra)
        for r in pipe["rows"]:
            if r["no"] == no:
                cell = line(b, PIPE)[r["stage"]]
                assert (r["qty"], r["amount"]) == (cell["this"], cell["amount"])


def test_a_locked_ra_row_does_not_move_when_later_challans_arrive(env):
    client, _, _ = env
    ra1, ra2 = ra1_and_ra2(client)
    before = [r for r in item(history(client), PIPE)["rows"] if r["no"] == 1]
    assert ch(client, "130", "2026-10-02", [(PIPE, 500)]).status_code == 200
    after = item(history(client), PIPE)
    assert [r for r in after["rows"] if r["no"] == 1] == before
    assert after["challans"][-1]["no"] == "130" and after["challans"][-1]["state"] == "open"


def test_a_line_nobody_billed_has_no_rows_and_a_held_challan_says_so(env):
    client, _, _ = env
    cs(client, sup=80, ins=10, svc="Fire", mode="together")
    assert ch(client, "700", "2026-05-01", [("5", 3)], ra=None).status_code == 200       # hydrant valve, on hold
    h = history(client)
    never = item(h, "1")                                                                 # diesel engine pump, nothing yet
    assert never["rows"] == [] and never["challans"] == []
    held = item(h, "5")
    assert held["rows"] == [] and [(c["no"], c["state"], c["ra"]) for c in held["challans"]] == [("700", "held", None)]


def test_ras_are_listed_and_section_and_site_progress_done_come_with_the_line(env):
    client, _, _ = env
    ra1, ra2 = ra1_and_ra2(client)
    progress(client, PIPE, 0.2)                                                          # 20 of 100 done on site
    h = history(client)
    assert [(r["no"], r["status"], r["series"]) for r in h["ras"]] == [(1, "locked", "together"), (2, "draft", "supply")]
    pipe = item(h, PIPE)
    assert pipe["section"] == "Fire Pump & Accessories"
    assert pipe["done"] == pytest.approx(20)


def test_unknown_service_is_a_404(env):
    client, _, _ = env
    assert client.get(f"{B}/item-history/Nope").status_code == 404
