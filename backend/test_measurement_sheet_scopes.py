"""From-To Detail sheet (Annexure D) for entries that are NOT in one zone: a whole floor / level, and the whole project (a feeder).

Reported need (site engineers, Hyatt / Thoth): the cable of a riser or a feeder belongs to no room, and a cable tray measured for a whole
level is one number. test_run_sheet.py pins the zone case; this file pins the other two places in the SAME Annexure D, from the real bill:
  * a whole-floor entry prints under the floor's name as "Whole floor (one total, not zone-wise)"; a whole-project entry prints last as
    "Whole project (not located)";
  * Previous / This bill come from what the last LOCKED RA billed in that same place, so the sheet ties to Annexure B-BOQ and C;
  * quantity the place holds that no entry explains is one visible line, never dropped;
  * a locked RA keeps its lines for ever.
Harness: test_billing.py's Fire BOQ (GI pipe 100 Rmt over 4 zones = 25 each; floors "1": roo2, roo3 and "2": roo5, roo6; 20 % overall = 5 each)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.test_billing import env, prepared, SLUG                      # noqa: F401
from backend.test_billing_challan import new_ra, B
from backend.test_measurement_sheet import recalc, lock, boq_this, item_block, vals
from backend.test_run_sheet import dws, block, row_of, PIPE, SHEET_C

P = f"/api/siteprogress/{SLUG}"


def entry(client, scope, frm, to, start=None, end=None, qty=None, **kw):
    body = {"service": "Fire", "item_code": "3", "scope": scope, "from": frm, "to": to}
    if start is not None:
        body.update(start=start, end=end)
    if qty is not None:
        body["qty"] = qty
    body.update(kw)
    r = client.post(f"{P}/progress/runs", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_a_feeder_for_the_whole_project_prints_last_and_ties(env):
    client, d, _ = prepared(env)
    entry(client, "project", "Transformer", "MDB", start=500, end=485)           # 15 on top of the 20 recorded overall
    entry(client, "project", "MDB", "DB-1", qty=5, remarks="in trunking")
    ra = new_ra(client, "install")
    wb = recalc(client, ra)
    ws = dws(wb)
    h, rows, tot = block(ws, PIPE)
    got = [row_of(ws, r) for r in rows]
    assert all("Whole project (not located)" in g[0] for g in got), got
    assert [g[5] for g in got] == [20, 15, 5] and "without From-To detail" in got[0][8]       # the 20 nobody wrote an entry for
    assert got[1][1:5] == ["Transformer", "MDB", 500, 485] and got[2][8] == "in trunking"
    assert [ws.cell(tot, c).value for c in (8, 9, 10)] == [40, 0, 40]
    assert boq_this(wb["Annexure B-BOQ"], PIPE)[0] == 40
    wc = wb[SHEET_C]
    assert vals(wc, item_block(wc, PIPE)[3]) == [0, 40, 40]


def test_a_whole_floor_prints_under_its_name_and_the_rest_stays_visible(env):
    client, d, _ = prepared(env)
    entry(client, "area", "Riser", "Floor 1 end", start=0, end=12, area="1")      # floor 1: 10 recorded overall + 12
    ra = new_ra(client, "install")
    wb = recalc(client, ra)
    ws = dws(wb)
    h, rows, tot = block(ws, PIPE)
    got = [row_of(ws, r) for r in rows]
    area = [g for g in got if "Whole" in g[0]]
    assert len(area) == 2 and all(g[0].strip().startswith("1 — Whole ") and "one total, not zone-wise" in g[0] for g in area), got
    assert [g[5] for g in area] == [10, 12]
    rest = [g for g in got if "Rest of the item" in g[0]]
    assert len(rest) == 1 and rest[0][5] == 10                                      # floor 2, recorded overall: 10
    assert [ws.cell(tot, c).value for c in (8, 9, 10)] == [32, 0, 32]
    assert boq_this(wb["Annexure B-BOQ"], PIPE)[0] == 32


def test_next_bill_splits_a_feeder_into_previous_and_this(env):
    client, d, _ = prepared(env)
    entry(client, "project", "Transformer", "MDB", qty=15)
    ra1 = new_ra(client, "install")
    lock(client, ra1)                                                               # 35 billed: 20 + 15
    entry(client, "project", "MDB", "DB-1", qty=10)
    ra2 = new_ra(client, "install")
    wb = recalc(client, ra2)
    ws = dws(wb)
    h, rows, tot = block(ws, PIPE)
    got = [row_of(ws, r) for r in rows]
    assert [g[5:8] for g in got] == [[20, 20, 0], [15, 15, 0], [10, 0, 10]]
    assert [ws.cell(tot, c).value for c in (8, 9, 10)] == [45, 35, 10]
    assert boq_this(wb["Annexure B-BOQ"], PIPE)[0] == 10
    wc = wb[SHEET_C]
    assert vals(wc, item_block(wc, PIPE)[3]) == [35, 10, 45]


def test_a_locked_bill_keeps_its_feeder_lines(env):
    client, d, _ = prepared(env)
    entry(client, "project", "Transformer", "MDB", qty=15)
    ra1 = new_ra(client, "install")
    lock(client, ra1)
    before = [row_of(dws(recalc(client, ra1)), r) for r in range(9, 20)]
    entry(client, "project", "MDB", "DB-1", qty=10)
    assert [row_of(dws(recalc(client, ra1)), r) for r in range(9, 20)] == before
